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
# 저작권 표기: 서버가 화면에 붙이는 코드가 있어야 한다 (LICENSE·NOTICE)
_src = open(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "portal.py"), encoding="utf-8").read()
assert "wqkgMjAyNiBnZ2dnODY1NyDCtyBkb25nanVraW0uZGV2QGdtYWlsLmNvbQ==" in _src and "signed(" in _src and "X-Author" in _src, "저작권 표기 누락"

print("selftest OK")
