"""개발자 업로드(devsubmit.py) — zip 검사(zip slip·절대 경로·링크·크기), 형식 검사, 개발자·관리자 권한, 승인 조건. Docker·운영 폴더는 안 씀."""
import io, json, os, stat, tempfile, unittest, zipfile
from types import SimpleNamespace
from unittest.mock import patch

from fastportal import config, devsubmit, pkgindex
from fastportal.db import db


def mkzip(entries):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, v in entries.items():
            if isinstance(v, tuple):   # (내용, 모드)
                i = zipfile.ZipInfo(n); i.external_attr = v[1] << 16; z.writestr(i, v[0])
            else:
                z.writestr(n, v)
    return b.getvalue()


GOOD = {"hello-local/app.py": "import os, portal_auth\nPORT = int(os.environ['PORT'])\nBIND = os.environ.get('BIND', '127.0.0.1')\n",
        "hello-local/README.md": "# hello", "hello-local/agent.json": json.dumps({"title": "Hello", "desc": "인사", "group": "기타", "usage": "사용법 " * 10})}

PIPELINE = devsubmit._pipeline


class DevSubmitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")),
                  patch.object(devsubmit, "SUB_DIR", os.path.join(self.tmp.name, "subs")),
                  patch.object(config, "BASE", os.path.join(self.tmp.name, "base")),
                  patch.object(devsubmit.tools, "tool", return_value=None), patch.object(devsubmit.tools, "tools", return_value=[{"dir": "sql-local", "port": 8772, "group": "데이터"}]),
                  patch.object(devsubmit, "_pipeline", lambda *a: None), patch.object(devsubmit, "_notify", lambda *a: None),
                  patch.object(pkgindex, "SEED_DIR", os.path.join(self.tmp.name, "no-seed"))]
        self.p.append(patch.object(config, "DEV_APPROVE", True))   # 승인 경로 시험은 잠금을 푼 상태로(잠금은 test_approve_locked)
        for x in self.p: x.start()
        with db() as c:
            devsubmit.init(c)
            pkgindex.init(c)
            c.execute("CREATE TABLE audit(ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT)")
            c.execute("CREATE TABLE users(id TEXT PRIMARY KEY, disabled INTEGER DEFAULT 0)")
            c.executemany("INSERT INTO users(id) VALUES(?)", [("dev",), ("bob",)])
            devsubmit.set_developer(c, "owner", "dev", True)

    def tearDown(self):
        for x in reversed(self.p): x.stop()
        self.tmp.cleanup()

    ctx = staticmethod(lambda uid, owner=False: SimpleNamespace(uid=uid, agent_user="owner" if owner else uid, is_owner=owner))

    def test_zip_attacks_rejected(self):
        d = os.path.join(self.tmp.name, "x"); os.makedirs(d)
        for bad in ({"a-local/../../evil": "x", "a-local/app.py": "x"}, {"/abs-local/app.py": "x"}, {"a-local/l": ("/etc/passwd", stat.S_IFLNK | 0o777)},
                    {"app.py": "x"}, {"a-local/x": "x", "b-local/y": "x"}, {"a-local\\..\\x": "x"}):
            with self.assertRaises(ValueError, msg=bad):
                devsubmit._unpack(mkzip(bad), d)
        with self.assertRaises(ValueError):
            devsubmit._unpack(b"not a zip", d)
        self.assertEqual(os.listdir(d), [], "거절된 zip 은 아무것도 풀지 않음(경로 검사 먼저)")
        with patch.object(devsubmit, "FILE_MAX", 10), self.assertRaises(ValueError):
            devsubmit._unpack(mkzip({"a-local/big": "x" * 100}), d)

    def test_permission(self):
        with self.assertRaises(PermissionError):
            devsubmit.submit(self.ctx("bob"), mkzip(GOOD))
        for c in (self.ctx("dev"), self.ctx("bob")):
            with self.assertRaises(PermissionError):
                devsubmit.decide(c, {"id": "abcdefgh", "ok": True})
        s = devsubmit.submit(self.ctx("dev"), mkzip(GOOD))
        with db() as c:
            with self.assertRaises(PermissionError):
                devsubmit._get(c, self.ctx("bob"), s["id"])
            self.assertEqual(devsubmit._get(c, self.ctx("dev"), s["id"])["id"], s["id"])
        st = os.stat(devsubmit.SUB_DIR).st_mode & 0o777
        self.assertEqual(st, 0o700)

    def test_checks(self):
        s = devsubmit.submit(self.ctx("dev"), mkzip(GOOD))
        self.assertEqual(s["status"], "sandbox", s["checks"])
        with self.assertRaises(ValueError):   # 검사 중인 제출이 있으면 하나씩
            devsubmit.submit(self.ctx("dev"), mkzip(GOOD))
        with db() as c:
            c.execute("UPDATE dev_submissions SET status='review'")
        bad = dict(GOOD)
        bad["hello-local/app.py"] = "import os, subprocess, pickle\nPORT=1\nsubprocess.run('x', shell=True)\napp.run(host='0.0.0.0')\neval('1')\n"
        del bad["hello-local/README.md"]
        bad = {k.replace("hello-local", "bad-local"): v for k, v in bad.items()}
        s = devsubmit.submit(self.ctx("dev"), mkzip(bad))
        self.assertEqual(s["status"], "failed")
        e, w = " ".join(s["checks"]["errors"]), " ".join(s["checks"]["warnings"])
        self.assertIn("README.md", e); self.assertIn("0.0.0.0", e)
        self.assertIn("shell=True", w); self.assertIn("eval", w); self.assertIn("portal_auth", w)
        nm = {k.replace("hello-local", "Hello"): v for k, v in GOOD.items()}
        self.assertEqual(devsubmit.submit(self.ctx("dev"), mkzip(nm))["status"], "failed", "폴더 이름 규칙")

    def test_pkg_warnings_in_checks(self):
        s = devsubmit.submit(self.ctx("dev"), mkzip(GOOD))
        self.assertTrue(any("폐쇄망 패키지 목록이 아직 없어" in x for x in s["checks"]["info"]), "목록이 비면 안내만")
        with db() as c:
            pkgindex.replace(c, {"fastapi": {"0.110.0"}}, "t.txt")
            c.execute("UPDATE dev_submissions SET status='review'")
        g = {k.replace("hello-local", "pkgs-local"): v for k, v in GOOD.items()}
        g["pkgs-local/requirements.txt"] = "fastapi==0.99\nnot-in-closed\ngit+https://example.invalid/x.git\n"
        s = devsubmit.submit(self.ctx("dev"), mkzip(g))
        w = " | ".join(s["checks"]["warnings"])
        self.assertEqual(s["status"], "sandbox", "패키지 문제는 경고(오류 아님)")
        self.assertIn("버전 없음: fastapi==0.99", w); self.assertIn("폐쇄망에서 설치 불가: not-in-closed", w); self.assertIn("git", w)

    def test_precheck_before_body(self):
        sent = []
        mk = lambda uid: SimpleNamespace(uid=uid, agent_user=uid, is_owner=False, send=lambda d, code=200: sent.append((code, d)) or (code, d))
        self.assertEqual(devsubmit.precheck(mk("bob"))[0], 403, "개발자 아님 → 본문 전에 403")
        self.assertIsNone(devsubmit.precheck(mk("dev")))
        devsubmit.submit(self.ctx("dev"), mkzip(GOOD))   # 검사 중(sandbox) 제출이 생김
        self.assertEqual(devsubmit.precheck(mk("dev"))[0], 400, "검사 중 제출이 있으면 본문 전에 400")

    def test_daily_cap(self):
        with db() as c:
            c.executemany("INSERT INTO dev_submissions(id, uid, agent_user, dir, title, status, created, updated, size, nfiles) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          [(f"old{i:06d}", "dev", "dev", "x", "x", "failed", devsubmit.now(), devsubmit.now(), 1, 1) for i in range(devsubmit.DAILY_MAX)])
        sent = []
        mk = SimpleNamespace(uid="dev", agent_user="dev", is_owner=False, send=lambda d, code=200: sent.append((code, d)) or (code, d))
        self.assertEqual(devsubmit.precheck(mk)[0], 429, "하루 상한이면 본문 전에 거절")
        with self.assertRaises(ValueError):
            devsubmit.submit(self.ctx("dev"), mkzip(GOOD))

    def test_daily_cap_counts_kst_day(self):
        # 어제(KST) 마지막 초에 낸 것은 오늘 한도에 안 셈 — KST 0시 = UTC 전날 15시
        import datetime
        from fastportal import timeutil
        before = (datetime.datetime.fromisoformat(timeutil.kst_day_start_utc(0)) - datetime.timedelta(seconds=1)).isoformat()
        with db() as c:
            c.executemany("INSERT INTO dev_submissions(id, uid, agent_user, dir, title, status, created, updated, size, nfiles) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          [(f"y{i:07d}", "dev", "dev", "x", "x", "failed", before, before, 1, 1) for i in range(devsubmit.DAILY_MAX)])
        mk = SimpleNamespace(uid="dev", agent_user="dev", is_owner=False, send=lambda d, code=200: (code, d))
        self.assertIsNone(devsubmit.precheck(mk), "어제(KST) 제출은 오늘 한도에 들지 않음")

    def test_corrupt_zip_is_400(self):
        z = bytearray(mkzip(GOOD))
        i = z.index(b"import os")   # 파일 내용 한 바이트를 바꿔 CRC 를 깨뜨림(목록은 멀쩡)
        z[i] ^= 0x01
        with self.assertRaises(ValueError):
            devsubmit.submit(self.ctx("dev"), bytes(z))

    def test_reject_while_checking_stays_rejected(self):
        real = devsubmit.check_tree
        def check_then_admin_rejects(c, sid, *a):
            out = real(c, sid, *a)
            devsubmit.decide(self.ctx("owner", True), {"id": sid, "ok": False, "note": "검사 중 거절"})
            return out
        with patch.object(devsubmit, "check_tree", check_then_admin_rejects):
            s = devsubmit.submit(self.ctx("dev"), mkzip(GOOD))
        self.assertEqual(s["status"], "rejected", "검사가 끝나도 sandbox·review 로 바뀌지 않음")

    def test_check_error_does_not_block_next_upload(self):
        with patch.object(devsubmit, "check_tree", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                devsubmit.submit(self.ctx("dev"), mkzip(GOOD))
        self.assertEqual(devsubmit.submit(self.ctx("dev"), mkzip(GOOD))["status"], "sandbox", "검사 오류 뒤에도 다시 올릴 수 있음")

    def test_reject_during_sandbox_stays_rejected(self):
        s = devsubmit.submit(self.ctx("dev"), mkzip(GOOD))   # status=sandbox(검증은 패치로 멈춤)
        devsubmit.decide(self.ctx("owner", True), {"id": s["id"], "ok": False, "note": "보안 문제"})
        sent = []
        with patch.object(devsubmit, "sandbox", lambda *a: {"status": "passed", "steps": [], "log": ""}), \
             patch.object(devsubmit, "_notify", lambda *a: sent.append(a)):
            PIPELINE(s["id"], "dev", "hello-local", "")   # 검증이 거절 뒤에 끝남(setUp 이 _pipeline 을 막아 둬서 진짜를 부름)
        with db() as c:
            self.assertEqual(c.execute("SELECT status FROM dev_submissions WHERE id=?", (s["id"],)).fetchone()[0], "rejected")
        self.assertEqual(sent, [], "거절한 제출로 '승인 대기' 알림을 다시 보내지 않음")

    def test_approve_locked(self):
        s = devsubmit.submit(self.ctx("dev"), mkzip(GOOD))
        with db() as c:
            c.execute("UPDATE dev_submissions SET status='review', sandbox=? WHERE id=?", (json.dumps({"status": "ok", "steps": []}), s["id"]))
        sent = []
        owner = SimpleNamespace(uid="owner", agent_user="owner", is_owner=True, p="/api/admin/dev/decide", send=lambda d, code=200: sent.append((code, d)) or (code, d))
        with patch.object(config, "DEV_APPROVE", False):
            code, d = devsubmit.handle_post(owner, {"id": s["id"], "ok": True})
            self.assertEqual((code, d["code"]), (409, "approve_locked"))
            self.assertIn("실행 격리 준비 중", d["error"])
            with db() as c:
                self.assertEqual(c.execute("SELECT status FROM dev_submissions WHERE id=?", (s["id"],)).fetchone()[0], "review", "잠금 중 승인은 아무것도 안 바꿈")
            code, d = devsubmit.handle_post(owner, {"id": s["id"], "ok": False, "note": "격리 뒤 다시"})
            self.assertEqual((code, d["status"]), (200, "rejected"), "거절은 잠금과 상관없이 됨")

    def test_approve_requires_review_and_no_errors(self):
        s = devsubmit.submit(self.ctx("dev"), mkzip(GOOD))
        with self.assertRaises(ValueError):   # 아직 격리 검증 중
            devsubmit.decide(self.ctx("owner", True), {"id": s["id"], "ok": True})
        with db() as c:
            c.execute("UPDATE dev_submissions SET status='review', sandbox=? WHERE id=?", (json.dumps({"status": "skipped", "steps": []}), s["id"]))
        with self.assertRaises(ValueError):   # 건너뛴 검증은 확인 필요
            devsubmit.decide(self.ctx("owner", True), {"id": s["id"], "ok": True})
        with self.assertRaises(ValueError):   # 거절은 사유 필요
            devsubmit.decide(self.ctx("owner", True), {"id": s["id"], "ok": False})
        self.assertEqual(devsubmit.decide(self.ctx("owner", True), {"id": s["id"], "ok": False, "note": "보안 검토 필요"})["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
