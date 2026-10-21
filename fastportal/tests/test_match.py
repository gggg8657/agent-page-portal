"""할 일로 도구 찾기(match.py)·카탈로그(capabilities.json) 검사.
  카탈로그: 숨기지 않은 도구마다 할 일이 있고(숨긴 도구·없는 도구는 없음), id 가 겹치지 않고, 깊은 링크는 links 표에 적은 값만이며
            그 값을 도구 화면 소스가 실제로 읽는지(웹 저장소가 옆에 있을 때), 연결 흐름 어댑터가 flows.ADAPTERS 에 있는지.
  검색: 자모 비교·조사 떼기·유의어·임베딩 없을 때 lexical 로만·팀 밸런스 맵(owner_only)은 관리자만·숨긴 도구 빠짐·속도 제한·흐름 제안.
  평가 묶음(match_eval.json)은 임베딩 없이도 기준 이상(recall@3 ≥ 0.9) — 임베딩까지는 scripts/match_eval.py"""
import json, os, re, tempfile, unittest
from unittest.mock import patch

from fastportal import config, flows, match, tools

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEB = os.environ.get("WEB_REPO") or next((p for p in (os.path.join(os.path.dirname(REPO), "web-match"), os.path.join(os.path.dirname(REPO), "agent-page-web"),
                                                      os.path.expanduser("~/agent-page/agent-page-web")) if os.path.isdir(os.path.join(p, "src", "tools"))), "")
CAT = json.load(open(os.path.join(REPO, "capabilities.json"), encoding="utf-8"))
TOOLS = json.load(open(os.path.join(REPO, "tools.json"), encoding="utf-8"))


class CatalogTest(unittest.TestCase):
    def test_every_tool_has_caps(self):
        shown = {t["dir"] for t in TOOLS if not t.get("hidden")}
        hidden = {t["dir"] for t in TOOLS if t.get("hidden")}
        got = [t["dir"] for t in CAT["tools"]]
        self.assertEqual(len(got), len(set(got)), "도구가 두 번 나옴")
        self.assertEqual(set(got), shown, "숨기지 않은 도구마다 할 일 목록이 있어야 하고, 없는 도구는 없어야 함")
        self.assertFalse(set(got) & hidden, "숨긴 도구(persona 등)는 카탈로그에 넣지 않음")
        for t in CAT["tools"]:
            self.assertGreaterEqual(len(t["caps"]), 3, t["dir"])
            self.assertIsInstance(t.get("files", []), list)

    def test_cap_fields(self):
        for t in CAT["tools"]:
            ids = [c["id"] for c in t["caps"]]
            self.assertEqual(len(ids), len(set(ids)), f"{t['dir']} id 겹침")
            for c in t["caps"]:
                where = f"{t['dir']}/{c['id']}"
                self.assertRegex(c["id"], r"^[a-z0-9][a-z0-9-]{0,40}$", where)
                self.assertTrue(0 < len(c["task"]) <= 40, where)
                self.assertTrue(c.get("in") and c.get("out"), f"{where}: 입력·출력")
                self.assertGreaterEqual(len(c.get("syn") or []), 3, f"{where}: 같은 뜻 표현")
                for e in c.get("files") or []:
                    self.assertRegex(e, r"^\.[a-z0-9]{1,8}$", where)
                self.assertNotRegex(json.dumps(c, ensure_ascii=False), r"[<>]", f"{where}: 꺾쇠 글자(HTML) 금지")

    def test_links_allowed_and_supported(self):
        links = CAT["links"]
        for d, spec in links.items():
            self.assertIn(d, {t["dir"] for t in TOOLS if not t.get("hidden")}, d)
            if WEB:   # 화면 소스가 그 주소 값을 실제로 읽는지(웹 저장소가 옆에 있을 때)
                src = os.path.join(WEB, spec["src"])
                self.assertTrue(os.path.exists(src), f"{d}: 화면 소스 {spec['src']} 없음")
                body = open(src, encoding="utf-8").read()
                for k in spec["keys"]:
                    self.assertTrue(re.search(r"""(sp|params|searchParams|q)\.get\(["']%s["']\)""" % re.escape(k), body), f"{d}: 화면({spec['src']})이 ?{k}= 를 읽지 않음")
        n = 0
        for t in CAT["tools"]:
            for c in t["caps"]:
                q = (c.get("link") or {}).get("query")
                if not q:
                    continue
                n += 1
                spec = links.get(t["dir"])
                self.assertIsNotNone(spec, f"{t['dir']}/{c['id']}: links 표에 없는 도구의 깊은 링크")
                for k, v in q.items():
                    self.assertIn(k, spec["keys"], f"{t['dir']}/{c['id']}: {k}")
                    self.assertIn(v, spec["keys"][k], f"{t['dir']}/{c['id']}: {k}={v}")
        self.assertGreater(n, 0)

    def test_writer_patterns_exist(self):
        pd = os.path.join(os.path.dirname(REPO), "writer-local", "patterns")
        if not os.path.isdir(pd):
            pd = os.path.expanduser("~/agent-page/writer-local/patterns")
        if not os.path.isdir(pd):
            self.skipTest("writer-local 없음")
        names = set()
        for f in os.listdir(pd):
            m = re.search(r"^name:\s*(\S+)", open(os.path.join(pd, f), encoding="utf-8").read(), re.M)
            if m:
                names.add(m.group(1).strip("'\""))
        self.assertTrue(set(CAT["links"]["writer-local"]["keys"]["pattern"]) <= names, set(CAT["links"]["writer-local"]["keys"]["pattern"]) - names)

    def test_chains_and_owner_only(self):
        for c in CAT["chains"]:
            self.assertIn(c["adapter"], flows.ADAPTERS)
            self.assertTrue(c["terms"])
        oo = [t["dir"] for t in CAT["tools"] if t.get("owner_only")]
        self.assertEqual(oo, ["saju-local"], "팀 밸런스 맵(주인의 개인 도구)만 관리자 전용")


class MatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = [patch.object(match, "CACHE_DIR", self.tmp.name), patch.dict(os.environ, {"EMBED_BASE_URL": ""})]
        for x in self.p:
            x.start()
        match._EMB.update(hash=None, vecs=None, busy=False, fail_t=0.0, err="")
        match._QV.clear(); match._RATE.clear()

    def tearDown(self):
        for x in reversed(self.p):
            x.stop()
        self.tmp.cleanup()

    def dirs(self, q, owner=False, **kw):
        return [r["dir"] for r in match.rank(q, owner, **kw)]

    def test_jamo_and_particles(self):
        self.assertEqual(match.jamo("회의 록"), match.jamo("회의록"))
        self.assertIn(match.jamo("회의ㄹ"), match.jamo("회의록"))
        self.assertEqual(match.words("회의록을 정리해 줘"), ["회의록", "정리"])
        self.assertEqual(match.words("효소가 접히는 걸 보고 싶어"), ["효소", "접히", "걸"])

    def test_meeting_minutes_cross_tool(self):
        got = self.dirs("회의록")
        self.assertEqual(set(got[:3]), {"meeting-local", "kordoc-local", "weekly-local"}, got)
        r = match.rank("회의록", False)[0]
        self.assertTrue(r["caps"] and r["reason"])
        kd = next(x for x in match.rank("회의록", False) if x["dir"] == "kordoc-local")
        self.assertTrue(any("preset=" in c["link"] for c in kd["caps"]), "kordoc 회의록 프리셋 깊은 링크")
        self.assertIn(match.rank("회의ㄹ", False)[0]["dir"], {"meeting-local", "kordoc-local"}, "치는 중인 글자도")

    def test_synonym_thesaurus(self):
        self.assertEqual(self.dirs("미팅 노트")[0], "meeting-local")
        self.assertEqual(self.dirs("코테 준비")[0], "codingtest-local")
        self.assertEqual(self.dirs("그래프 숫자 뽑기")[0], "digitizer-local")
        self.assertEqual(self.dirs("표로 차트")[0], "chart-local")

    def test_owner_only_and_hidden(self):
        self.assertNotIn("saju-local", self.dirs("사주 궁합"))
        self.assertIn("saju-local", self.dirs("사주 궁합", owner=True))
        for q in ("캐릭터 대화", "페르소나"):
            self.assertNotIn("persona-local", self.dirs(q, owner=True))
        hid = [dict(t) for t in TOOLS]
        for t in hid:
            if t["dir"] == "chart-local":
                t["hidden"] = True
        with patch.object(tools, "tools", return_value=hid):
            self.assertNotIn("chart-local", self.dirs("표로 차트", owner=True), "숨긴 도구는 검색에서도 빠짐")
        from fastportal import toolstate
        with patch.object(toolstate, "is_off", side_effect=lambda d: d in ("meeting-local", "mail-local")):
            got = self.dirs("회의록", owner=True)
            self.assertNotIn("meeting-local", got, "관리자가 끈 도구는 관리자에게도 빠짐")
            self.assertTrue(got)
            f = match.chain("녹음해서 공문으로 만들고 메일로 보내기")
            self.assertTrue(f is None or all(st["tool"] not in ("meeting-local", "mail-local") for st in f["steps"]), "끈 도구는 흐름 제안에서도 빠짐")
        with patch.object(toolstate, "admin_hidden", return_value=frozenset({"meeting-local"})):
            self.assertNotIn("meeting-local", self.dirs("회의록", owner=True), "관리자가 숨긴 도구도 검색에서 빠짐(링크로는 열림)")

    def test_file_ext(self):
        got = match.rank("", False, ext=".mp3")
        self.assertEqual(got[0]["dir"], "meeting-local")
        self.assertTrue(got[0]["signals"]["file"] > 0)
        self.assertIn("hwp", "".join(r["dir"] + " " + " ".join(c["task"] for c in r["caps"]) for r in match.rank("", False, ext=".hwp")).lower() + "hwp")

    def test_no_embedding_fallback(self):
        r = match.search("회의록", False)
        self.assertFalse(r["semantic"])
        self.assertEqual(r["tools"][0]["dir"], "meeting-local")

    def test_semantic_with_fake_vectors(self):
        cat = match.catalog()
        n = len(cat.caps)
        ix = next(i for i, x in enumerate(cat.caps) if x["dir"] == "protein-local")

        def vec(i):
            v = [0.0] * 8
            v[i % 8] = 1.0
            return match._unit(v)
        vecs = [vec(1) for _ in range(n)]
        vecs[ix] = vec(3)
        match._EMB.update(hash=cat.hash, vecs=vecs)
        got = match.rank("효소 접힘 관찰", False, qvec=vec(3))
        self.assertEqual(got[0]["dir"], "protein-local", "낱말이 안 맞아도 뜻(벡터)이 맞으면")
        self.assertGreater(got[0]["signals"]["semantic"], 0.9)

    def test_embed_cache_file_roundtrip(self):
        cat = match.catalog()
        vecs = [match._unit([1.0, float(i)]) for i in range(len(cat.texts))]
        match._save_cache(cat.hash, vecs)
        back = match._load_cache(cat.hash)
        self.assertEqual(len(back), len(vecs))
        self.assertAlmostEqual(back[3][1], vecs[3][1], places=5)

    def test_chain_flow(self):
        f = match.chain("녹음해서 공문으로 만들고 메일로 보내기")
        self.assertEqual([s["adapter"] for s in f["steps"]], ["meeting.minutes", "kordoc.draft", "mail.write"])
        self.assertIn("o1.preset=", f["link"])
        self.assertTrue(f["link"].startswith("/create?"))
        self.assertIsNone(match.chain("회의록"))
        self.assertIsNone(match.chain("메일"))

    def test_shared_agents(self):
        ags = [{"id": "aa1111", "name": "코드 리뷰 도우미", "desc": "diff 를 붙이면 버그·개선점을 짚어 줌", "cat": "개발/코딩", "icon": ""},
               {"id": "bb2222", "name": "성과지표(KPI) 설계", "desc": "목표에 맞는 성과지표·목표값", "cat": "기획/전략", "icon": ""}]
        with patch.object(match, "agents_for", side_effect=lambda u: ags if u == "alice" else []):
            r = match.search("코드 리뷰", False, agent_user="alice")
            self.assertEqual(r["agents"][0]["id"], "aa1111")
            self.assertEqual(match.search("KPI 목표값", False, agent_user="alice")["agents"][0]["id"], "bb2222")
            self.assertEqual(match.search("코드 리뷰", False, agent_user="bob")["agents"], [])
            self.assertEqual(match.search("코드 리뷰", False)["agents"], [])

    def test_agents_for_filters(self):
        from fastportal import community
        b = {"agents": [{"id": "aa1111", "name": "가", "status": "published"}, {"id": "bb2222", "name": "나", "blocked": "관리자가 끔"},
                        {"id": "cc3333", "name": "다", "status": "draft"}, {"id": "../x", "name": "라"}]}
        match._AG.clear()
        with patch.object(community, "_builder", return_value=(200, b)) as m:
            self.assertEqual([a["id"] for a in match.agents_for("zed")], ["aa1111"])
            self.assertEqual(m.call_args[0][0], "zed", "그 사람 이름으로 빌더에 물음(공개 범위는 빌더가)")
        match._AG.clear()

    def test_agents_for_system_and_dept(self):
        """포털 기본(tab=system)과 동료 공유(tab=dept)를 둘 다 — 같은 id 는 한 번, kind 로 구분"""
        from fastportal import community
        lists = {"/api/agents?tab=system": {"agents": [{"id": "5e5111", "name": "기본", "status": "published", "kind": "system"}]},
                 "/api/agents?tab=dept": {"agents": [{"id": "dd4444", "name": "동료", "status": "published", "kind": "user"},
                                                     {"id": "5e5111", "name": "기본", "status": "published", "kind": "system"}]}}
        match._AG.clear()
        with patch.object(community, "_builder", side_effect=lambda u, path: (200, lists[path])):
            self.assertEqual([(a["id"], a["kind"]) for a in match.agents_for("zed")], [("5e5111", "system"), ("dd4444", "user")])
        match._AG.clear()

    def test_rate_limit(self):
        with patch.object(match, "RATE_N", 3):
            self.assertTrue(all(match.allow("u1") for _ in range(3)))
            self.assertFalse(match.allow("u1"))
            self.assertTrue(match.allow("u2"))

    def test_handle(self):
        class C:
            uid, is_owner, agent_user = "alice", False, None

            def __init__(self, raw):
                self.raw = raw

            def send(self, body, ctype="application/json", code=200, headers=None):
                return code, body
        code, b = match.handle(C("/api/match?q=%ED%9A%8C%EC%9D%98%EB%A1%9D"))
        self.assertEqual(code, 200)
        self.assertEqual(b["tools"][0]["dir"], "meeting-local")
        self.assertEqual(match.handle(C("/api/match?q=a&ext=../x"))[0], 400)
        self.assertEqual(match.handle(C("/api/match?q=" + "a" * 300))[0], 400)
        self.assertEqual(match.handle(C("/api/match?q="))[1]["tools"], [])

    def test_eval_lexical_floor(self):
        ev = json.load(open(os.path.join(REPO, "fastportal", "tests", "match_eval.json"), encoding="utf-8"))["queries"]
        r3 = 0.0
        for x in ev:
            got = self.dirs(x["q"], owner=True)
            r3 += len(set(x["expect"]) & set(got[:3])) / min(3, len(x["expect"]))
        self.assertGreaterEqual(r3 / len(ev), 0.9, "임베딩 없이도 평가 묶음 recall@3 0.9 이상")


if __name__ == "__main__":
    unittest.main()


class InternalMatchTest(unittest.TestCase):
    class C:
        client = "127.0.0.1"

        def __init__(self, tok):
            self.headers = {"x-portal-jobs-token": tok} if tok else {}

        def send(self, body, ctype="application/json", code=200, headers=None):
            return code, body

    def setUp(self):
        match._RATE.clear()
        self.p = patch.dict(os.environ, {"EMBED_BASE_URL": ""})
        self.p.start()
        match._EMB.update(fail_t=__import__("time").time())

    def tearDown(self):
        self.p.stop()

    def test_token_and_owner(self):
        from fastportal import jobs
        good = jobs.token_for("padong-local")
        self.assertEqual(match.internal_post(self.C(None), b'{"tool":"padong-local","q":"x"}')[0], 403)
        self.assertEqual(match.internal_post(self.C("bad"), b'{"tool":"padong-local","q":"x"}')[0], 403)
        self.assertEqual(match.internal_post(self.C(jobs.token_for("chart-local")), b'{"tool":"padong-local","q":"x"}')[0], 403, "다른 도구 토큰")
        code, b = match.internal_post(self.C(good), json.dumps({"tool": "padong-local", "user": "bob", "q": "회의록"}).encode())
        self.assertEqual(code, 200)
        self.assertEqual(b["tools"][0]["dir"], "meeting-local")
        self.assertLessEqual(len(b["tools"]), 3)
        code, b = match.internal_post(self.C(good), json.dumps({"tool": "padong-local", "user": "bob", "q": "사주 궁합"}).encode())
        self.assertNotIn("saju-local", [t["dir"] for t in b["tools"]])
        code, b = match.internal_post(self.C(good), json.dumps({"tool": "padong-local", "user": "owner", "q": "사주 궁합"}).encode())
        self.assertIn("saju-local", [t["dir"] for t in b["tools"]])
        self.assertEqual(match.internal_post(self.C(good), b'{"tool":"padong-local","q":""}')[0], 400)
        r = self.C(good)
        r.client = "10.9.9.9"
        self.assertEqual(match.internal_post(r, b'{"tool":"padong-local","q":"x"}')[0], 403, "바깥 접속지")
