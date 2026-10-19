"""계정별 즐겨찾기·최근 사용(lists.py) — 계정마다 따로, 등록된 도구만, 중복 없이, 개수 상한."""
import sqlite3, unittest
from unittest import mock

from fastportal import lists


class ListsTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        lists.init(self.c)
        self.p = mock.patch.object(lists.tools, "tool", lambda d: {"dir": d} if d.endswith("-local") else None)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_per_account(self):
        self.assertEqual(lists.get(self.c, "a"), {"fav": [], "recent": []})
        lists.put(self.c, "a", {"kind": "fav", "items": ["sql-local", "chart-local"]})
        self.assertEqual(lists.get(self.c, "a")["fav"], ["sql-local", "chart-local"])
        self.assertEqual(lists.get(self.c, "b")["fav"], [], "다른 계정에 보이면 안 됨")

    def test_clean_and_replace(self):
        r = lists.put(self.c, "a", {"kind": "fav", "items": ["sql-local", "nope", "sql-local", "../x"]})
        self.assertEqual(r, {"fav": ["sql-local"]})
        lists.put(self.c, "a", {"kind": "fav", "items": []})
        self.assertEqual(lists.get(self.c, "a")["fav"], [])
        lists.put(self.c, "a", {"kind": "recent", "items": [f"t{i}-local" for i in range(30)]})
        self.assertEqual(len(lists.get(self.c, "a")["recent"]), 10)

    def test_bad_input(self):
        for req in ({"kind": "x", "items": []}, {"kind": "fav", "items": "sql-local"}, {"kind": "fav", "items": [1]}):
            with self.assertRaises(ValueError):
                lists.put(self.c, "a", req)


if __name__ == "__main__":
    unittest.main()
