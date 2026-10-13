#!/usr/bin/env python3
"""포털 자가검증: sqlite 통계·피드·도구 등록 로직 (서버·도구 없이).  python3 selftest.py"""
import json, os, tempfile
os.environ["AGENT_DATA"] = tempfile.mkdtemp()
import portal
with portal.db() as c:
    c.executemany("INSERT INTO events VALUES(?,?,?,?)", [("open", "kordoc-local", portal.now(), "kim"), ("open", "kordoc-local", "2020-01-01T00:00:00", "10.0.0.2"), ("like", "kordoc-local", portal.now(), "kim"),
                                                        ("open", "kordoc-local", portal.now(), "10.0.0.9")])  # IP 로 남은 예전 기록은 랭킹에 안 나옴
    portal.set_user(c, "kim", "김연구", "", "1234")
    u = portal.usage(c)["kordoc-local"]
    assert (u["recent"], u["total"], u["users"], u["likes"]) == (2, 3, 3, 1), u
    assert portal.user_rank(c) == [{"user": "김연구", "n": 1}], portal.user_rank(c)
    # 로그인: PIN 해시 확인, 아이디·PIN 규칙, 주인 아이디는 밖에서 가입 불가
    assert portal.check_pin(c, "kim", "1234") and not portal.check_pin(c, "kim", "1235") and not portal.check_pin(c, "nobody", "1234")
    assert c.execute("SELECT hash FROM users WHERE id='kim'").fetchone()[0] != b"1234"
    for uid, name, pin, local in (("kim", "x", "1234", False), ("Kim", "x", "1234", False), ("k", "x", "1234", False), ("owner", "x", "1234", True),
                                  (next(iter(portal.OWNERS)), "x", "1234", False), ("lee", "", "1234", False), ("lee", "x", "12a4", False), ("lee", "x", "123", False)):
        try:
            portal.check_new(c, uid, name, pin, local); raise AssertionError(uid)
        except ValueError:
            pass
    portal.check_new(c, "lee.k-2", "이", "123456", False)
    c.execute("INSERT INTO posts(title,body,author,dept,tags,ts) VALUES('t','b','a','d','[\"#x\"]',?)", (portal.now(),))
    c.execute("INSERT OR IGNORE INTO post_likes VALUES(1,'u')"); c.execute("INSERT OR IGNORE INTO post_likes VALUES(1,'u')")
    p = portal.posts(c, "u")[0]
    assert p["likes"] == 1 and p["liked"] and p["tags"] == ["#x"]
# 도구 등록 검증 (tools.json 은 건드리지 않게 임시 파일로)
portal.TOOLS_FILE = os.path.join(os.environ["AGENT_DATA"], "tools.json"); json.dump(json.load(open(os.path.join(portal.ROOT, "tools.json"), encoding="utf-8")), open(portal.TOOLS_FILE, "w"))
for bad in ({"dir": "../x", "port": 9000}, {"dir": "kordoc-local", "port": 9000}, {"dir": "ok-tool", "port": 8766}, {"dir": "ok-tool", "port": 80}):
    try: portal.add_tool(bad); raise SystemExit(f"거부돼야 함: {bad}")
    except ValueError: pass
row = portal.add_tool({"dir": "ok-tool", "port": 9001, "title": "OK", "tags": "a #b"})
assert row["tags"] == ["#a", "#b"] and portal.tool("ok-tool") and json.load(open(portal.TOOLS_FILE))[-1]["dir"] == "ok-tool"
# 부서·역할·요청: 부서장 없으면 관리자만 승인, 부서장은 자기 부서 가입만, 승인되면 소속 반영
with portal.db() as c:
    c.execute("INSERT OR IGNORE INTO depts(name) VALUES('가부서')")
    owner = next(iter(portal.OWNERS))
    for u in ("aa", "bb", owner): portal.set_user(c, u, u, "", "1234")
    portal.new_request(c, "aa", "dept_join", "가부서")
    rid = c.execute("SELECT max(id) FROM requests").fetchone()[0]
    try: portal.decide(c, "bb", rid, True); raise AssertionError("일반 사용자가 승인함")
    except PermissionError: pass
    portal.decide(c, owner, rid, True)
    assert c.execute("SELECT dept FROM users WHERE id='aa'").fetchone()[0] == "가부서"
    portal.new_request(c, "aa", "dept_head", "가부서"); portal.decide(c, owner, c.execute("SELECT max(id) FROM requests").fetchone()[0], True)
    assert portal.role(c, "aa") == "부서장" and portal.role(c, "bb") == "일반" and portal.role(c, owner) == "관리자"
    portal.new_request(c, "bb", "dept_join", "가부서")
    try: portal.new_request(c, "bb", "dept_join", "가부서"); raise AssertionError("대기 요청 둘")
    except ValueError: pass
    rid = c.execute("SELECT max(id) FROM requests").fetchone()[0]
    assert portal.can_decide(c, "aa", dict(c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()))
    portal.decide(c, "aa", rid, False, "확인 필요")
    assert c.execute("SELECT status, approver FROM requests WHERE id=?", (rid,)).fetchone()[:] == ("rejected", "aa") and c.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 3
    # 예전 200k 해시는 로그인 성공 때 600k 로 다시 해시
    salt = b"s" * 16; c.execute("UPDATE users SET salt=?, hash=?, iters=200000 WHERE id='bb'", (salt, portal._hash("1234", salt, 200_000)))
    assert portal.check_pin(c, "bb", "1234") and c.execute("SELECT iters FROM users WHERE id='bb'").fetchone()[0] == portal.ITERS and portal.check_pin(c, "bb", "1234")
    c.execute("UPDATE users SET disabled=1 WHERE id='bb'"); assert not portal.check_pin(c, "bb", "1234")
# 저작권 표기: 서버가 화면에 붙이는 코드가 있어야 한다 (LICENSE·NOTICE)
_src = open(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "portal.py"), encoding="utf-8").read()
assert "wqkgMjAyNiBnZ2dnODY1NyDCtyBkb25nanVraW0uZGV2QGdtYWlsLmNvbQ==" in _src and "signed(" in _src and "X-Author" in _src, "저작권 표기 누락"

# 화면 테마: classic-light 는 CSS 를 안 붙임, 머리는 <meta charset> 뒤, 도구별 CSS 는 있을 때만
assert portal.theme_files("classic-light", "tool", "mail-local") == []
assert portal.theme_files("trendy-dark", "tool", "no-such-tool") == ["tokens.css", "base-trendy.css", "base-dark.css", "dark.js", "chrome.css"]
assert portal.theme_files("classic-dark", "portal") == ["tokens.css", "portal.css", "chrome.css"]
_h = portal.themed('<!doctype html><meta charset="utf-8"><title>x</title>', "<!--T-->")
assert _h.startswith('<!doctype html><meta charset="utf-8"><!--T-->'), _h
assert portal.themed("<p>x</p>", "<!--T-->") == "<!--T--><p>x</p>"
assert "pref = \"trendy-auto\"" in portal.theme_head("trendy-auto", "tool", "mail-local")
# v2: 도구는 base-v2, 포털은 portal-v2(+명령 팔레트), 포털 화면 배치는 v2/<화면>.html 로 바꾸되 원래 스크립트·id 는 그대로
assert portal.theme_files("v2-dark", "tool", "no-such-tool") == ["tokens.css", "base-v2.css", "base-dark.css", "dark.js", "chrome.css"]
assert portal.theme_files("v2-light", "portal") == ["tokens.css", "portal-v2.css", "chrome.css"] and portal.theme_files("v2-auto", "guide") == ["tokens.css", "guide.css"]
assert "palette-v2.js" in portal.theme_head("v2-auto", "portal") and "palette-v2.js" not in portal.theme_head("trendy-light", "portal")
assert {"v2-light", "v2-dark", "v2-auto"} <= portal.THEMES
for _n, _f, _ids in (("home", portal.HTML, ("user", "menu", "counts", "rank", "q", "chips", "home", "cols", "newcols", "gpus", "all", "count", "grid", "feed", "newPost", "addTool", "dlg", "dlgT", "dlgB")),
                     ("account", portal.ACCOUNT, ("nav", "main")), ("login", portal.LOGIN, ("in", "up", "dept", "foot"))):
    _v = portal.v2_page(_f, _n)
    assert _v != _f and _v[_v.find("<script>"):] == _f[_f.find("<script>"):], _n  # 스크립트는 원래 것 그대로
    for _i in _ids:
        assert f'id="{_i}"' in _v, (_n, _i)

# 도구 첫 화면 판정: 딥링크(쿼리)도 테마·사용법을 붙이고, 펫 iframe(?pet=1)과 하위 경로는 안 붙임
assert portal.first_html("") and portal.first_html("index.html") and portal.first_html("?job=abc") and portal.first_html("?id=3&token=x")
assert not portal.first_html("?pet=1") and not portal.first_html("?mode=a&pet=1&date=2026-10-12") and not portal.first_html("v0.1?pet=1")
assert not portal.first_html("static/app.js") and not portal.first_html("api/run?x=1")

print("selftest OK")
