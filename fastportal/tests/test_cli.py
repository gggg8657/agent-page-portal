"""관리자 복구 CLI(python -m fastportal add-user) — 새 계정 만들기, 있는 계정 PIN 초기화(세션 끊김), 임시 DB 에서만."""
import os, sqlite3, subprocess, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class AddUserTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="fastportal-cli-")
        self.env = {**os.environ, "AGENT_DATA": self.data, "AUTH_BACKEND": "local"}
        self.py("from fastportal import db; db.init_db()")

    def py(self, code):
        return subprocess.run([sys.executable, "-c", code], cwd=REPO, env=self.env, capture_output=True, text=True, check=True).stdout

    def cli(self, *a):
        return subprocess.run([sys.executable, "-m", "fastportal", "add-user", *a], cwd=REPO, env=self.env, capture_output=True, text=True)

    def verify(self, uid, pin):
        return self.py(f"from fastportal import auth, db\nwith db.db() as c: print(bool(auth.backend().verify(c, {uid!r}, {pin!r})))").strip()

    def test_create_then_reset(self):
        r = self.cli("kim", "--name", "김", "--dept", "연구", "--pin", "1234")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("1234", r.stdout, "준 PIN 은 다시 출력하지 않음")
        self.assertEqual(self.verify("kim", "1234"), "True")
        db = os.path.join(self.data, "portal.db")
        with sqlite3.connect(db) as c:
            c.execute("INSERT INTO sessions(token, uid, exp) VALUES('x', 'kim', 9e12)")
        r = self.cli("kim")   # PIN 없이 = 무작위 6자리를 화면에
        self.assertEqual(r.returncode, 0, r.stderr)
        pin = r.stdout.split("PIN ")[1].strip()
        self.assertEqual(self.verify("kim", pin), "True")
        self.assertEqual(self.verify("kim", "1234"), "False")
        with sqlite3.connect(db) as c:
            self.assertEqual(c.execute("SELECT name, dept FROM users WHERE id='kim'").fetchone(), ("김", "연구"), "안 준 값은 그대로")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sessions WHERE uid='kim'").fetchone()[0], 0, "초기화하면 세션 끊김")

    def test_fresh_db(self):
        self.env["AGENT_DATA"] = tempfile.mkdtemp(prefix="fastportal-cli-")   # 표 없는 새 폴더
        r = self.cli("first", "--pin", "1234")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_rejects_bad_input(self):
        for a in (["owner", "--pin", "1234"], ["kim", "--pin", "12"], ["Bad id", "--pin", "1234"]):
            self.assertNotEqual(self.cli(*a).returncode, 0, a)


if __name__ == "__main__":
    unittest.main()
