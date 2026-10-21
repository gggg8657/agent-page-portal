"""보관함의 계정 격리·동시 저장·기존 이력 보존 검사. 운영 데이터/도구에 요청하지 않는다."""
import json, os, tempfile, unittest
from unittest.mock import patch

from fastportal import config, workspace
from fastportal.db import db

TOOL = {"dir": "sql-local", "port": 9999, "name": "SQL"}


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")),
                        patch.object(workspace.tools, "tool", side_effect=lambda d: TOOL if d == TOOL["dir"] else None),
                        patch.object(workspace.tools, "tools", return_value=[TOOL])]
        for p in self.patches: p.start()
        with db() as c: workspace.init(c)

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def save(self, uid="alice", **kw):
        return workspace.save(uid, {"tool": "sql-local", "title": "개인 질문", "payload": {"input": "예산"}, **kw})

    def test_bad_id_is_400_not_500(self):
        for bad in ([1], 5, {"x": 1}, "../x"):   # [] 같은 빈 값은 "새 작업"(id 없음)
            with self.assertRaises(ValueError, msg=bad):
                self.save(id=bad, revision=1)
            with self.assertRaises(ValueError, msg=bad):
                workspace.modify("alice", {"id": bad, "action": "archive"})

    def test_account_isolation_even_with_forged_owner(self):
        item = self.save(uid="alice")
        other = self.save(uid="bob", payload={"uid": "alice", "input": "다른 작업"})
        self.assertEqual(workspace.listing("alice", {})["total"], 1)
        self.assertEqual(workspace.listing("bob", {})["items"][0]["id"], other["id"])
        for op in (lambda: workspace.get("bob", item["id"]),
                   lambda: workspace.modify("bob", {"id": item["id"], "action": "archive"}),
                   lambda: self.save(uid="bob", id=item["id"], revision=1)):
            with self.assertRaises(FileNotFoundError): op()

    def test_versions_and_stale_tab_conflict(self):
        a = self.save()
        b = self.save(id=a["id"], revision=1, payload={"input": "수정"})
        self.assertEqual(b["revision"], 2)
        self.assertEqual(workspace.get("alice", a["id"], 1)["payload"], {"input": "예산"})
        with self.assertRaises(workspace.Conflict): self.save(id=a["id"], revision=1)
        with self.assertRaises(FileNotFoundError): workspace.get("bob", a["id"], 1)

    def test_archive_restore_and_star(self):
        a = self.save()
        workspace.modify("alice", {"id": a["id"], "action": "star", "starred": True})
        self.assertEqual(workspace.listing("alice", {"view": ["starred"]})["total"], 1)
        workspace.modify("alice", {"id": a["id"], "action": "archive"})
        self.assertEqual(workspace.listing("alice", {})["total"], 0)
        self.assertEqual(workspace.listing("alice", {"view": ["trash"]})["total"], 1)
        workspace.modify("alice", {"id": a["id"], "action": "restore"})
        self.assertEqual(workspace.get("alice", a["id"])["payload"], {"input": "예산"})

    def test_sync_repeated_and_archive_preserved(self):
        rows = [{"run_id": "r-1", "question": "기존 질문", "ts": "2026-01-01T00:00:00"}]
        with patch.object(workspace, "_read", return_value=rows) as read:
            workspace.sync("alice", "alice")
            self.assertEqual(read.call_args.args[-1], "alice")
            a = workspace.listing("alice", {})["items"][0]
            workspace.modify("alice", {"id": a["id"], "action": "archive"})
            workspace.sync("alice", "alice")
            self.assertEqual(workspace.listing("alice", {"view": ["trash"]})["total"], 1)
            self.assertEqual(workspace.listing("bob", {})["total"], 0)

    def test_detail_offline_keeps_saved_copy(self):
        with patch.object(workspace, "_read", return_value=[{"run_id": "r-1", "question": "기존 질문"}]):
            workspace.sync("alice", "alice")
        a = workspace.listing("alice", {})["items"][0]
        with patch.object(workspace, "_read", return_value={"question": "기존 질문", "rows": [[7]]}):
            self.assertEqual(workspace.refresh_detail("alice", a["id"])["payload"]["rows"], [[7]])
        with patch.object(workspace, "_read", side_effect=OSError("서버 꺼짐")):
            w = workspace.refresh_detail("alice", a["id"])
            self.assertTrue(w["offline"])
            self.assertEqual(w["payload"]["rows"], [[7]])

    def test_search_treats_sql_wildcards_as_text(self):
        self.save(title="100% 완료")
        self.save(title="다른 작업")
        self.assertEqual(workspace.listing("alice", {"q": ["%"]})["total"], 1)
        self.assertEqual(workspace.listing("alice", {"q": ["' OR 1=1 --"]})["total"], 0)

    def test_invalid_payload_and_tool_rejected(self):
        for kw in ({"tool": "../sql-local"}, {"payload": []}, {"title": ""}, {"payload": {"x": "a" * workspace.MAX_PAYLOAD}}, {"payload": {"x": float("nan")}}):
            with self.assertRaises(ValueError): self.save(**kw)


if __name__ == "__main__": unittest.main()
