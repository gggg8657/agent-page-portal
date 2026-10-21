"""LocalBackend — 같은 아이디로 동시에 가입해도 먼저 만든 계정(PIN)을 덮어쓰지 않음."""
import sqlite3, unittest

from fastportal.auth.local import LocalBackend


class CreateUserTest(unittest.TestCase):
    def test_second_create_does_not_overwrite(self):
        c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT, dept TEXT, salt BLOB, hash BLOB, iters INT, ts TEXT, disabled INT DEFAULT 0)")
        b = LocalBackend()
        b.create_user(c, "kim", "A", "1111")   # 둘 다 check_new 를 통과한 뒤(사이에 끼어든 가입)
        with self.assertRaises(ValueError):
            b.create_user(c, "kim", "B", "2222")
        self.assertTrue(b.verify(c, "kim", "1111")); self.assertFalse(b.verify(c, "kim", "2222"))
        self.assertEqual(c.execute("SELECT name FROM users").fetchone()[0], "A")


if __name__ == "__main__":
    unittest.main()
