"""개발자 업로드(devsubmit.py) — zip 검사(zip slip·절대 경로·링크·크기), 형식 검사, 개발자·관리자 권한, 승인 조건. Docker·운영 폴더는 안 씀."""
import io, json, os, stat, tempfile, unittest, zipfile
from types import SimpleNamespace
from unittest.mock import patch

from fastportal import config, devsubmit
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


class DevSubmitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")),
                  patch.object(devsubmit, "SUB_DIR", os.path.join(self.tmp.name, "subs")),
                  patch.object(config, "BASE", os.path.join(self.tmp.name, "base")),
                  patch.object(devsubmit.tools, "tool", return_value=None), patch.object(devsubmit.tools, "tools", return_value=[{"dir": "sql-local", "port": 8772, "group": "데이터"}]),
                  patch.object(devsubmit, "_pipeline", lambda *a: None), patch.object(devsubmit, "_notify", lambda *a: None)]
        for x in self.p: x.start()
        with db() as c:
            devsubmit.init(c)
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
