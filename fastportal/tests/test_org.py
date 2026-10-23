"""계층 조직(org.py) — 가져오기(몇 번 돌려도 같음·사라진 단위는 걸린 것이 있으면 비활성, 없으면 삭제), 예전 부서 이름 옮기기,
승인 경로(고른 단위 → 상위 장 → 관리자), /api/me 의 unit·예전 dept, 조직 추가 요청, 관리자 단위·장·소속."""
import json, os, tempfile, unittest
from unittest.mock import patch

from fastportal import accounts, config, db, org

UNITS = [
    {"id": "r", "name": "원장", "type": "원장", "parent": None, "order": 1},
    {"id": "vp", "name": "부원장", "type": "부원장", "parent": "r", "order": 2},
    {"id": "lab", "name": "선진원자로연구소", "type": "연구소", "parent": "vp", "order": 1},
    {"id": "bu", "name": "계통개발부", "type": "부", "parent": "lab", "order": 1},
    {"id": "sil", "name": "계통해석실", "type": "실", "parent": "bu", "order": 1},
    {"id": "hq", "name": "경영기획본부", "type": "본부", "parent": "r", "order": 1},
]


class Ctx:
    def __init__(self, uid, owner=False):
        self.uid, self.uname, self.is_owner = uid, uid, owner
        self.raw, self.client = "", "127.0.0.1"

    def send(self, body, ctype="application/json", code=200, headers=None):
        return code, body

    def local_owner(self):
        return False


class OrgTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")),
                  patch.object(org, "DATA", self.tmp.name),   # 기본 조직도 대신 시험 단위
                  patch.object(accounts, "OWNERS", {"boss"})]
        for x in self.p:
            x.start()
        with open(os.path.join(self.tmp.name, "kaeri-org.json"), "w") as f:
            json.dump(UNITS, f)
        with open(os.path.join(self.tmp.name, "local-units.json"), "w") as f:
            json.dump([{"id": "x-ai", "name": "인공지능응용연구실", "type": "실", "parent": "hq", "order": 9, "note": "공개 조직도에 없음"}], f)
        db.init_db()
        with db.db() as c:
            for u in ("kim", "lee", "park", "choi", "boss"):
                c.execute("INSERT INTO users(id, name, dept, ts) VALUES(?,?,?,?)", (u, u, "", db.now()))

    def tearDown(self):
        for x in self.p:
            x.stop()
        self.tmp.cleanup()

    def post(self, uid, path, req, owner=False):
        with db.db() as c:
            return accounts.manage_post(Ctx(uid, owner), c, path, req)

    def me(self, uid):
        with db.db() as c:
            return accounts.me(c, uid, uid)

    def test_seed_and_paths(self):
        with db.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM org_units").fetchone()[0], 7)
            i = org.info(c, "sil")
            self.assertEqual(i["path"], ["선진원자로연구소", "계통개발부", "계통해석실"])   # 원장·부원장은 경로에서 빠짐
            self.assertEqual(org.ancestors(c, "sil"), ["sil", "bu", "lab", "vp", "r"])
            self.assertEqual(org.descendants(c, "lab"), {"lab", "bu", "sil"})
            self.assertEqual(org.info(c, "x-ai")["path"], ["경영기획본부", "인공지능응용연구실"])
            self.assertEqual(c.execute("SELECT src, note FROM org_units WHERE id='x-ai'").fetchone()[:], ("local", "공개 조직도에 없음"))

    def test_import_idempotent_and_removal(self):
        with db.db() as c:
            out = org.import_units(c, UNITS, "t", "again")
            self.assertEqual((out["added"], out["updated"], out["deleted"], out["deactivated"]), (0, 0, 0, 0))
            org.set_member(c, "kim", "sil")
            changed = [dict(u) for u in UNITS if u["id"] not in ("sil", "hq")]   # 실(소속 있음)·본부(로컬 단위가 하위로 걸림)가 목록에서 사라짐
            changed[3]["name"] = "계통설계부"
            out = org.import_units(c, changed, "t", "v2")
            self.assertEqual((out["updated"], out["deactivated"], out["deleted"]), (1, 2, 0))
            self.assertEqual(c.execute("SELECT active FROM org_units WHERE id='sil'").fetchone()[0], 0)   # 지우지 않고 비활성
            self.assertEqual(c.execute("SELECT dept FROM users WHERE id='kim'").fetchone()[0], "계통해석실")
            org.set_member(c, "kim", "bu")
            self.assertEqual(c.execute("SELECT dept FROM users WHERE id='kim'").fetchone()[0], "계통설계부")   # 이름 바뀐 것이 예전 열에도
            out = org.import_units(c, changed, "t", "v3")
            self.assertEqual(out["deleted"], 1)   # 걸린 것이 없어진 실은 이제 삭제
            out = org.import_units(c, UNITS, "t", "v4")
            self.assertEqual((out["added"], out["reactivated"]), (1, 1))   # 다시 나타나면 되살림
            self.assertEqual(c.execute("SELECT COUNT(*) FROM org_imports").fetchone()[0], 5)
            with self.assertRaises(ValueError):
                org.import_units(c, UNITS + [{"id": "y", "name": "계통개발부", "type": "부", "parent": "lab"}], "t", "dup")   # 이름 겹침
            with self.assertRaises(ValueError):
                org.import_units(c, [{"id": "a", "name": "A", "type": "부", "parent": "zz"}], "t", "bad")

    def test_legacy_migration(self):
        with db.db() as c:
            c.execute("INSERT INTO users(id, name, dept, ts) VALUES('old', 'old', '인공지능응용연구실', '')")
            c.execute("INSERT INTO users(id, name, dept, ts) VALUES('odd', 'odd', '없던부서', '')")
            c.execute("INSERT INTO dept_heads(dept, uid, ts) VALUES('인공지능응용연구실', 'old', '')")
            c.execute("INSERT INTO requests(kind, uid, dept, status, ts) VALUES('dept_join', 'kim', '인공지능응용연구실', 'pending', '')")
        db.init_db()
        db.init_db()   # 두 번 불려도 같음
        with db.db() as c:
            self.assertEqual(c.execute("SELECT unit_id FROM users WHERE id='old'").fetchone()[0], "x-ai")
            self.assertEqual(c.execute("SELECT unit_id FROM dept_heads WHERE uid='old'").fetchone()[0], "x-ai")
            self.assertEqual(c.execute("SELECT unit_id FROM requests WHERE uid='kim'").fetchone()[0], "x-ai")
            odd = c.execute("SELECT unit_id FROM users WHERE id='odd'").fetchone()[0]
            self.assertEqual(c.execute("SELECT name, parent, note FROM org_units WHERE id=?", (odd,)).fetchone()[:], ("없던부서", "r", "공개 조직도에 없음 (예전 부서)"))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM org_units WHERE name='없던부서'").fetchone()[0], 1)
        self.assertEqual(self.me("old")["heads"], ["인공지능응용연구실"])
        self.assertEqual(self.me("old")["head_units"][0]["id"], "x-ai")

    def test_join_walks_up(self):
        with db.db() as c:
            org.set_member(c, "lee", "lab"); org.grant_head(c, "lab", "lee")   # 소장만 있음
        self.post("kim", "/api/account/unit", {"unit_id": "sil"})
        m = self.me("kim")
        self.assertEqual((m["dept"], m["unit"], m["unit_pending"]["id"]), ("", None, "sil"))
        with db.db() as c:
            r = accounts.req_rows(c, "uid='kim'")[0]
            self.assertIn("선진원자로연구소", r["route"])   # 실·부 장이 없어 연구소 장에게
            self.assertTrue(accounts.can_decide(c, "lee", c.execute("SELECT * FROM requests WHERE uid='kim'").fetchone()))
            self.assertFalse(accounts.can_decide(c, "park", c.execute("SELECT * FROM requests WHERE uid='kim'").fetchone()))
        with db.db() as c:
            org.set_member(c, "park", "bu"); org.grant_head(c, "bu", "park")   # 부장이 생기면 부장에게(소장은 아님)
            rq = c.execute("SELECT * FROM requests WHERE uid='kim'").fetchone()
            self.assertTrue(accounts.can_decide(c, "park", rq))
            self.assertFalse(accounts.can_decide(c, "lee", rq))
        self.post("park", "/api/approvals/decide", {"id": rq["id"], "ok": True})
        m = self.me("kim")
        self.assertEqual((m["dept"], m["unit"]["path"]), ("계통해석실", ["선진원자로연구소", "계통개발부", "계통해석실"]))
        code, body = accounts.manage_get(Ctx("park"), "/api/approvals")
        self.assertEqual(code, 200)
        self.assertEqual(sorted(x["id"] for x in body["members"]["계통개발부"]), ["kim", "park"])   # 하위 실 소속까지

    def test_join_no_heads_goes_to_admin(self):
        self.post("kim", "/api/account/unit", {"unit_id": "hq"})
        with db.db() as c:
            rq = c.execute("SELECT * FROM requests WHERE uid='kim'").fetchone()
            self.assertEqual(accounts.req_rows(c, "uid='kim'")[0]["route"], "관리자")
            self.assertFalse(accounts.can_decide(c, "lee", rq))
            self.assertTrue(accounts.can_decide(c, "boss", rq))
            self.assertFalse(accounts.can_decide(c, "kim", rq))

    def test_own_request_not_self_approved(self):
        with db.db() as c:
            org.set_member(c, "lee", "bu"); org.grant_head(c, "bu", "lee")
        self.post("lee", "/api/account/unit", {"unit_id": "sil"})   # 부장이 자기 하위 실로 — 본인 빼고 위로(없으면 관리자)
        with db.db() as c:
            rq = c.execute("SELECT * FROM requests WHERE uid='lee'").fetchone()
            self.assertFalse(accounts.can_decide(c, "lee", rq))
        self.post("boss", "/api/approvals/decide", {"id": rq["id"], "ok": True})
        self.assertEqual(self.me("lee")["head_units"], [])   # 다른 단위로 옮기면 장 권한 회수

    def test_legacy_dept_name_and_inactive(self):
        self.post("kim", "/api/account/dept", {"dept": "계통개발부"})   # 예전 화면: 이름
        self.assertEqual(self.me("kim")["unit_pending"]["id"], "bu")
        with self.assertRaises(ValueError):
            self.post("lee", "/api/account/dept", {"dept": "없는부서"})
        with db.db() as c:
            c.execute("UPDATE org_units SET active=0 WHERE id='hq'")
        with self.assertRaises(ValueError):
            self.post("lee", "/api/account/unit", {"unit_id": "hq"})
        with db.db() as c:
            self.assertNotIn("경영기획본부", accounts.visible_depts(c))
            self.assertNotIn("hq", [u["id"] for u in org.public(c)])

    def test_org_request(self):
        self.post("kim", "/api/account/org-request", {"name": "신설실", "parent": "bu", "note": "10월 신설"})
        with self.assertRaises(ValueError):
            self.post("lee", "/api/account/org-request", {"name": "계통개발부", "parent": "lab"})   # 이미 있음
        with db.db() as c:
            r = accounts.req_rows(c, "uid='kim'")[0]
            self.assertEqual((r["kind"], r["route"], r["proposal"]["parent_path"]), ("org_add", "관리자", ["선진원자로연구소", "계통개발부"]))
            org.set_member(c, "lee", "bu"); org.grant_head(c, "bu", "lee")
            self.assertFalse(accounts.can_decide(c, "lee", c.execute("SELECT * FROM requests WHERE uid='kim'").fetchone()))
        self.post("boss", "/api/approvals/decide", {"id": r["id"], "ok": True})
        m = self.me("kim")
        self.assertEqual(m["unit"]["path"], ["선진원자로연구소", "계통개발부", "신설실"])
        with db.db() as c:
            self.assertIn("사용자 요청", c.execute("SELECT note FROM org_units WHERE name='신설실'").fetchone()[0])

    def test_admin_ops(self):
        with self.assertRaises(PermissionError):
            self.post("kim", "/api/admin/org", {"op": "add", "name": "X", "type": "실", "parent": "bu"})
        code, out = self.post("boss", "/api/admin/org", {"op": "add", "name": "새실", "type": "실", "parent": "bu"}, owner=True)
        nid = out["id"]
        self.post("boss", "/api/admin/member", {"uid": "kim", "unit_id": nid}, owner=True)
        self.post("boss", "/api/admin/head", {"op": "grant", "uid": "kim", "unit_id": nid}, owner=True)
        with self.assertRaises(ValueError):
            self.post("boss", "/api/admin/head", {"op": "grant", "uid": "lee", "unit_id": nid}, owner=True)   # 소속 아님
        self.post("boss", "/api/admin/org", {"op": "update", "id": nid, "name": "새실2"}, owner=True)
        with db.db() as c:
            self.assertEqual(c.execute("SELECT dept FROM users WHERE id='kim'").fetchone()[0], "새실2")
            self.assertEqual(c.execute("SELECT dept FROM dept_heads WHERE uid='kim'").fetchone()[0], "새실2")
        with self.assertRaises(ValueError):
            self.post("boss", "/api/admin/org", {"op": "move", "id": "lab", "parent": "sil"}, owner=True)   # 자기 하위로
        self.post("boss", "/api/admin/org", {"op": "move", "id": nid, "parent": "lab"}, owner=True)
        self.assertEqual(self.me("kim")["unit"]["path"], ["선진원자로연구소", "새실2"])
        with self.assertRaises(ValueError):
            self.post("boss", "/api/admin/org", {"op": "deactivate", "id": "bu"}, owner=True)   # 살아 있는 하위
        self.post("boss", "/api/admin/org", {"op": "deactivate", "id": nid}, owner=True)
        self.assertFalse(self.me("kim")["unit"]["active"])   # 소속은 그대로
        with self.assertRaises(ValueError):
            self.post("boss", "/api/admin/dept", {"op": "add", "name": "예전식"}, owner=True)
        code, v = accounts.manage_get(Ctx("boss", True), "/api/admin/org")
        self.assertEqual(code, 200)
        self.assertEqual(next(u for u in v["units"] if u["id"] == nid)["heads"][0]["uid"], "kim")
        code, v = accounts.manage_get(Ctx("kim"), "/api/admin/org")
        self.assertEqual(code, 403)
        code, v = accounts.manage_get(Ctx("boss", True), "/api/admin")
        self.assertEqual(next(u for u in v["users"] if u["id"] == "kim")["unit"]["id"], nid)

    def test_anonymize_clears_unit(self):
        with db.db() as c:
            org.set_member(c, "kim", "sil"); org.grant_head(c, "sil", "kim")
            accounts.deactivate(c, "boss", "kim")
            self.assertEqual(org.heads(c, "sil"), [])
            accounts.anonymize(c, "boss", "kim", "시험")
            self.assertEqual(c.execute("SELECT unit_id, dept FROM users WHERE id='kim'").fetchone()[:], (None, ""))


if __name__ == "__main__":
    unittest.main()
