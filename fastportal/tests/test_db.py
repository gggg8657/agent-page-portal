"""db.own_row — 흐름·아이디어·제출·리뷰가 같이 쓰는 '내 것 한 줄' 규칙."""
import sqlite3, unittest

from fastportal.db import own_row


class OwnRowTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:"); self.c.row_factory = sqlite3.Row
        self.c.execute("CREATE TABLE t(id TEXT, uid TEXT)"); self.c.execute("INSERT INTO t VALUES('abc123', 'a')")

    def test_rules(self):
        self.assertEqual(own_row(self.c, "t", "abc123", "a", "없음", "남의 것")["uid"], "a")
        for bad in ("nope00", "../x", 123, None, ["abc123"]):
            with self.assertRaises(FileNotFoundError, msg=bad):
                own_row(self.c, "t", bad, "a", "없음", "남의 것")
        with self.assertRaises(PermissionError):
            own_row(self.c, "t", "abc123", "b", "없음", "남의 것")
        self.assertTrue(own_row(self.c, "t", "abc123", "b", "없음", "남의 것", owner=True), "관리자는 남의 것도")
        with self.assertRaises(PermissionError):
            own_row(self.c, "t", "abc123", None, "없음", "관리자만")   # devsubmit admin_only


if __name__ == "__main__":
    unittest.main()
