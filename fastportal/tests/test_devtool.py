"""격리 컨테이너 실행(devtool.py)·승인 설치/되돌리기/회수·개발자 신청·샘플 zip. Docker 는 부르지 않음(docker 호출은 가짜로 바꿈)."""
import io, json, os, tempfile, unittest, zipfile
from types import SimpleNamespace
from unittest.mock import patch

from fastportal import config, devsubmit, devtool, jobs, pkgindex, tools
from fastportal.db import db


class Now:
    """threading.Thread 대신 — start() 에서 바로 실행(설치 스레드를 시험에서 기다리지 않게)"""
    def __init__(self, target, args=(), **_):
        self.f, self.a = target, args

    def start(self):
        self.f(*self.a)


class DevtoolUnitTest(unittest.TestCase):
    def test_clean_requirements(self):
        ok, bad = devtool.clean_requirements("# 주석\nrequests==2.32.3\nnumpy>=1.26,<2  # 숫자\npydantic[email]~=2.7\nfoo; python_version<'3.12'\n\n"
                                             "-i http://x\n--extra-index-url http://y\ngit+https://g/x.git\n./local.whl\n-r other.txt\n-e .\nhttp://z/a.whl\n")
        self.assertEqual(ok, ["requests==2.32.3", "numpy>=1.26,<2", "pydantic[email]~=2.7", "foo; python_version<'3.12'"])
        self.assertEqual(len(bad), 7, bad)

    def test_ip_for_and_names(self):
        a, b = devtool.ip_for({"port": 8810}), devtool.ip_for({"port": 8811})
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("172.29.243."))
        self.assertIsNone(devtool.ip_for({}))
        self.assertEqual(devtool.container_name("hello-local"), "dongjukim-tool-hello-local")
        for bad in ("../x-local", "Hello-local", "x", "a b-local"):
            with self.assertRaises(ValueError):
                devtool.container_name(bad)

    def test_start_args(self):
        calls = []

        def fake(args, timeout=60, **kw):
            calls.append(list(args))
            if args[0] == "run":
                envf = args[args.index("--env-file") + 1]
                calls.append(["ENV", open(envf).read(), oct(os.stat(envf).st_mode & 0o777)])
            out = "true false 172.29.243.0/24" if args[:2] == ["network", "inspect"] else ""
            return SimpleNamespace(returncode=0, stdout=out, stderr="")
        with tempfile.TemporaryDirectory() as tmp, patch.object(devtool, "docker", fake), patch.object(config, "DATA", tmp), patch.object(config, "SECRETS_DIR", tmp):
            t = {"dir": "hello-local", "port": 8810, "runtime": "container", "image": "dongjukim-tool-hello-local:s-abc"}
            devtool.start(t, {"PORTAL_TOOL_KEY": "k" * 64, "PORTAL_JOBS_TOKEN": "nope"})
            run = next(c for c in calls if c[0] == "run")
            for flag in ("--read-only", "--cap-drop", "no-new-privileges", "--pids-limit", "--memory", "--cpus", devtool.NET, "dongjukim-tool-hello-local"):
                self.assertIn(flag, run)
            self.assertNotIn("--gpus", run)
            self.assertNotIn("k" * 64, " ".join(run), "서명 키는 명령줄에 없음(env-file 만)")
            self.assertIn(f"{os.path.join(tmp, 'hello-local')}:/data:rw", run)
            env = next(c for c in calls if c[0] == "ENV")
            self.assertIn("PORTAL_TOOL_KEY=" + "k" * 64, env[1]); self.assertNotIn("PORTAL_JOBS_TOKEN", env[1], "작업 알림 토큰은 안 넘김")
            self.assertIn("LLM_BASE_URL=http://127.0.0.1:11440/v1", env[1]); self.assertEqual(env[2], "0o600")
            self.assertEqual([f for f in os.listdir(tmp) if f.startswith(".devtool-env-")], [], "env-file 은 바로 지움")
            with self.assertRaises(RuntimeError):   # image 가 그 도구 것이 아님
                devtool.start({**t, "image": "dongjukim-atomslide:current"})

    def test_net_mismatch_refuses(self):
        fake = lambda args, timeout=60, **kw: SimpleNamespace(returncode=0, stdout="false true 172.29.243.0/24", stderr="")
        with patch.object(devtool, "docker", fake), self.assertRaises(RuntimeError):
            devtool.ensure_net()


class ToolsJsonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        f = os.path.join(self.tmp.name, "tools.json")
        json.dump([{"dir": "sql-local", "port": 8772, "group": "데이터"}], open(f, "w"))
        self.p = patch.object(config, "TOOLS_FILE", f); self.p.start()

    def tearDown(self):
        self.p.stop(); self.tmp.cleanup()

    def test_container_row_and_key(self):
        row = tools.add_tool({"dir": "hello-local", "port": 8810, "runtime": "container", "image": "dongjukim-tool-hello-local:s-1"})
        self.assertEqual((row["runtime"], row["image"]), ("container", "dongjukim-tool-hello-local:s-1"))
        t = tools.tool("hello-local")
        self.assertTrue(tools.is_container(t)); self.assertFalse(tools.is_container(tools.tool("sql-local")))
        self.assertEqual(tools.tool_host(t), devtool.ip_for(t))
        self.assertEqual(tools.tool_host(tools.tool("sql-local")), "127.0.0.1", "호스트 도구는 그대로")
        self.assertNotEqual(jobs.key_for("hello-local"), jobs.TOOL_KEY, "컨테이너 도구는 도구 전용 키")
        self.assertEqual(jobs.key_for("sql-local"), jobs.TOOL_KEY)
        with patch.object(config, "TOOL_AUTH", True):
            self.assertEqual(jobs.tool_env(t)["PORTAL_TOOL_KEY"], jobs.container_key("hello-local"))
            sig = jobs.sign("kim", "hello-local", now=1000)
            import hashlib, hmac
            self.assertEqual(sig.split(".")[2], hmac.new(jobs.container_key("hello-local").encode(), b"v2|1000|hello-local|kim", hashlib.sha256).hexdigest())
        self.assertFalse(tools.remove_tool("sql-local"), "호스트 도구 줄은 빼지 않음")
        self.assertTrue(tools.remove_tool("hello-local"))
        self.assertIsNone(tools.tool("hello-local"))


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.join(self.tmp.name, "portal")
        os.makedirs(os.path.join(root, "guides"))
        tf = os.path.join(root, "tools.json")
        json.dump([{"dir": "sql-local", "port": 8772, "group": "데이터"}], open(tf, "w"))
        self.calls = []
        fake = lambda name: (lambda *a, **k: self.calls.append((name, a)) or self.ret.get(name))
        self.ret = {"build": ("dongjukim-tool-hello-local:s-x", "log"), "wait_health": (True, '{"ok": true}'), "logs": "로그"}
        self.p = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")), patch.object(config, "ROOT", root),
                  patch.object(config, "TOOLS_FILE", tf), patch.object(config, "BASE", os.path.join(self.tmp.name, "base")),
                  patch.object(devsubmit, "SUB_DIR", os.path.join(self.tmp.name, "subs")), patch.object(devsubmit, "_notify", lambda *a, **k: self.calls.append(("notify", a))),
                  patch.object(devsubmit.threading, "Thread", Now), patch.object(config, "DEV_APPROVE", True), patch.object(devsubmit, "_pipeline", lambda *a: None),
                  patch.object(pkgindex, "SEED_DIR", os.path.join(self.tmp.name, "no-seed"))]
        self.p += [patch.object(devtool, n, fake(n)) for n in ("build", "start", "wait_health", "stop", "remove_image", "logs")]
        for x in self.p: x.start()
        with db() as c:
            devsubmit.init(c); pkgindex.init(c)
            c.execute("CREATE TABLE audit(ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT)")
            c.execute("CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT, dept TEXT, disabled INTEGER DEFAULT 0)")
            c.executemany("INSERT INTO users(id, name) VALUES(?,?)", [("dev", "개발"), ("bob", "밥")])
            devsubmit.set_developer(c, "owner", "dev", True)

    def tearDown(self):
        for x in reversed(self.p): x.stop()
        self.tmp.cleanup()

    ctx = staticmethod(lambda uid, owner=False: SimpleNamespace(uid=uid, agent_user=uid, agent_role="", is_owner=owner))

    def _submit_review(self):
        s = devsubmit.submit(self.ctx("dev"), devsubmit.sample_zip())
        self.assertEqual(s["status"], "sandbox", s["checks"]["errors"])
        with db() as c:
            c.execute("UPDATE dev_submissions SET status='review', sandbox=? WHERE id=?", (json.dumps({"status": "passed", "steps": []}), s["id"]))
        return s["id"]

    def _st(self, sid):
        with db() as c:
            return c.execute("SELECT * FROM dev_submissions WHERE id=?", (sid,)).fetchone()

    def _audit(self):
        with db() as c:
            return [r[0] for r in c.execute("SELECT action FROM audit ORDER BY rowid")]

    def test_sample_zip_passes_checks(self):
        names = set(zipfile.ZipFile(io.BytesIO(devsubmit.sample_zip())).namelist())
        self.assertTrue({"hello-local/app.py", "hello-local/README.md", "hello-local/agent.json", "hello-local/portal_auth.py", "hello-local/FORMAT.md"} <= names, names)
        sid = self._submit_review()
        self.assertTrue(sid)

    def test_approve_installs_in_container(self):
        sid = self._submit_review()
        r = devsubmit.decide(self.ctx("owner", True), {"id": sid, "ok": True})
        self.assertEqual(r["status"], "installing")
        row = self._st(sid)
        self.assertEqual(row["status"], "approved", row["install"])
        t = tools.tool("hello-local")
        self.assertEqual((t["runtime"], t["image"], t["author"]), ("container", "dongjukim-tool-hello-local:s-x", "dev"))
        self.assertTrue(os.path.exists(os.path.join(config.ROOT, "guides", "hello-local.md")))
        self.assertFalse(os.path.exists(os.path.join(config.BASE, "hello-local")), "호스트에 도구 폴더를 만들지 않음")
        names = [c[0] for c in self.calls]
        self.assertLess(names.index("build"), names.index("start")); self.assertLess(names.index("start"), names.index("wait_health"))
        start_env = next(c[1][1] for c in self.calls if c[0] == "start")
        self.assertNotEqual(start_env.get("PORTAL_TOOL_KEY"), jobs.TOOL_KEY, "공용 키가 아님")
        for a in ("dev_approve", "dev_build", "tool_start", "tool_add", "dev_install_ok"):
            self.assertIn(a, self._audit())
        inst = json.loads(row["install"])
        self.assertEqual(inst["status"], "done"); self.assertTrue(inst["guide_created"])
        # 회수: 컨테이너·이미지·등록·사용법 정리, 데이터는 남김
        with self.assertRaises(ValueError):
            devsubmit.revoke(self.ctx("owner", True), {"id": sid})
        self.calls.clear()
        self.assertEqual(devsubmit.revoke(self.ctx("owner", True), {"id": sid, "note": "시험 끝"})["status"], "revoked")
        self.assertIsNone(tools.tool("hello-local"))
        self.assertFalse(os.path.exists(os.path.join(config.ROOT, "guides", "hello-local.md")))
        self.assertEqual({c[0] for c in self.calls} & {"stop", "remove_image"}, {"stop", "remove_image"})
        self.assertIn("dev_revoke", self._audit())
        self.assertEqual(self._st(sid)["status"], "revoked")
        with self.assertRaises(PermissionError):
            devsubmit.revoke(self.ctx("dev"), {"id": sid, "note": "x"})

    def test_install_failure_rolls_back(self):
        sid = self._submit_review()
        self.ret["wait_health"] = (False, "60초 안에 /healthz 응답 없음")
        devsubmit.decide(self.ctx("owner", True), {"id": sid, "ok": True})
        row = self._st(sid)
        self.assertEqual(row["status"], "review", "실패하면 다시 승인 대기")
        self.assertIsNone(row["port"])
        inst = json.loads(row["install"])
        self.assertEqual(inst["status"], "failed"); self.assertIn("/healthz", inst["reason"])
        self.assertIsNone(tools.tool("hello-local"), "등록 안 됨")
        self.assertFalse(os.path.exists(os.path.join(config.ROOT, "guides", "hello-local.md")))
        names = [c[0] for c in self.calls]
        self.assertIn("stop", names); self.assertIn("remove_image", names)
        self.assertIn("dev_install_fail", self._audit())
        self.assertTrue(any(c[0] == "notify" and c[1][0] == "owner" and c[1][2] == "failed" for c in self.calls), "관리자에게 이유 알림")
        # 빌드 실패 — 컨테이너·이미지는 안 만들었으니 지울 것도 없음
        self.calls.clear()
        self.ret["build"] = None
        with patch.object(devtool, "build", side_effect=RuntimeError("이미지 빌드 실패:\nERROR")):
            devsubmit.decide(self.ctx("owner", True), {"id": sid, "ok": True})
        self.assertEqual(json.loads(self._st(sid)["install"])["reason"], "이미지 빌드 실패:")
        self.assertNotIn("stop", [c[0] for c in self.calls])

    def test_pkg_confirm_and_one_install_at_a_time(self):
        sid = self._submit_review()
        with db() as c:
            chk = json.loads(c.execute("SELECT checks FROM dev_submissions WHERE id=?", (sid,)).fetchone()[0])
            chk["warnings"].append("폐쇄망에서 설치 불가: foo (requirements.txt)")
            c.execute("UPDATE dev_submissions SET checks=? WHERE id=?", (json.dumps(chk), sid))
        with self.assertRaises(ValueError):
            devsubmit.decide(self.ctx("owner", True), {"id": sid, "ok": True})
        with db() as c:
            c.execute("INSERT INTO dev_submissions(id, uid, agent_user, dir, status, created, updated) VALUES('other','dev','dev','x-local','installing','t','t')")
        with self.assertRaises(ValueError):
            devsubmit.decide(self.ctx("owner", True), {"id": sid, "ok": True, "confirm_pkgs": True})
        with db() as c:
            c.execute("DELETE FROM dev_submissions WHERE id='other'")
        devsubmit.decide(self.ctx("owner", True), {"id": sid, "ok": True, "confirm_pkgs": True})
        self.assertEqual(self._st(sid)["status"], "approved")

    def test_recover_stuck_install(self):
        sid = self._submit_review()
        with db() as c:
            c.execute("UPDATE dev_submissions SET status='installing', port=8811, install=? WHERE id=?",
                      (json.dumps({"status": "running", "steps": [{"name": "이미지 빌드", "ok": True, "detail": "dongjukim-tool-hello-local:s-x"}]}), sid))
        devsubmit.recover()
        row = self._st(sid)
        self.assertEqual((row["status"], row["port"]), ("review", None))
        self.assertIn(("remove_image", ("hello-local", "dongjukim-tool-hello-local:s-x")), self.calls)

    def test_apply_flow(self):
        bob = self.ctx("bob")
        with self.assertRaises(ValueError):
            devsubmit.apply(bob, {"reason": "짧"})
        devsubmit.apply(bob, {"reason": "실험 데이터 정리 도구를 올리고 싶습니다"})
        with self.assertRaises(ValueError):
            devsubmit.apply(bob, {"reason": "한 번 더 신청합니다"})
        self.assertTrue(any(c[0] == "notify" and c[1][0] == "owner" for c in self.calls), "관리자 알림함에 올라감")
        self.assertEqual(devsubmit.my_application(bob)["application"]["status"], "pending")
        with self.assertRaises(PermissionError):
            devsubmit.decide_application(bob, {"uid": "bob", "op": "grant"})
        with db() as c:
            apps = devsubmit.applications(c)
        self.assertEqual((apps[0]["uid"], apps[0]["name"], apps[0]["status"]), ("bob", "밥", "pending"))
        devsubmit.decide_application(self.ctx("owner", True), {"uid": "bob", "op": "grant"})
        me = devsubmit.my_application(bob)
        self.assertTrue(me["developer"]); self.assertEqual(me["application"]["status"], "granted")
        self.assertIn("developer_grant", self._audit())
        with self.assertRaises(ValueError):
            devsubmit.apply(bob, {"reason": "이미 개발자인데 또 신청"})
        with self.assertRaises(PermissionError):
            devsubmit.apply(SimpleNamespace(uid="owner", is_owner=True), {"reason": "로컬 주인은 신청 안 함"})

    def test_apply_dismiss_and_admin_grant_marks_granted(self):
        with db() as c:
            c.execute("INSERT INTO users(id, name) VALUES('lee','리')")
        devsubmit.apply(self.ctx("lee"), {"reason": "보고서 자동화 도구"})
        devsubmit.decide_application(self.ctx("owner", True), {"uid": "lee", "op": "dismiss", "note": "다음 분기"})
        self.assertEqual(devsubmit.my_application(self.ctx("lee"))["application"]["status"], "dismissed")
        with self.assertRaises(ValueError):   # 보류 직후 다시 신청 — 하루 뒤
            devsubmit.apply(self.ctx("lee"), {"reason": "다시 신청합니다"})
        with db() as c:
            c.execute("UPDATE dev_applications SET decided_ts='2000-01-01T00:00:00' WHERE uid='lee'")
        devsubmit.apply(self.ctx("lee"), {"reason": "다시 신청합니다"})
        with db() as c:   # 관리 화면 '개발자 부여'로 줘도 신청은 granted
            devsubmit.set_developer(c, "owner", "lee", True)
        self.assertEqual(devsubmit.my_application(self.ctx("lee"))["application"]["status"], "granted")

    def test_routes(self):
        sent = []
        mk = lambda p, uid="bob", owner=False: SimpleNamespace(p=p, raw=p, uid=uid, agent_user=uid, is_owner=owner,
                                                             send=lambda d, ctype="application/json", code=200, headers=None: sent.append((code, ctype, d)) or (code, d))
        devsubmit.handle_get(mk("/api/dev/sample.zip"))
        self.assertEqual(sent[-1][:2], (200, "application/zip"))
        devsubmit.handle_get(mk("/api/dev/format"))
        self.assertIn("portal_auth", sent[-1][2]["markdown"])
        devsubmit.handle_get(mk("/api/admin/dev/applications"))
        self.assertEqual(sent[-1][0], 403)
        devsubmit.handle_get(mk("/api/admin/dev/applications", "owner", True))
        self.assertEqual(sent[-1][0], 200)
        devsubmit.handle_post(mk("/api/dev/apply"), {"reason": "x"})
        self.assertEqual(sent[-1][0], 400)
        devsubmit.handle_post(mk("/api/admin/dev/revoke"), {"id": "x", "note": "y"})
        self.assertEqual(sent[-1][0], 403)
        self.assertTrue(devsubmit.GET_PATHS >= {"/api/dev/apply", "/api/dev/sample.zip"})


if __name__ == "__main__":
    unittest.main()
