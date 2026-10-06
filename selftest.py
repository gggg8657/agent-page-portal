#!/usr/bin/env python3
"""포털 자가검증: sqlite 통계·피드·도구 등록 로직 (서버·도구 없이).  python3 selftest.py"""
import json, os, tempfile
os.environ["AGENT_DATA"] = tempfile.mkdtemp()
import portal
with portal.db() as c:
    c.executemany("INSERT INTO events VALUES(?,?,?,?)", [("open", "kordoc-local", portal.now(), "10.0.0.1"), ("open", "kordoc-local", "2020-01-01T00:00:00", "10.0.0.2"), ("like", "kordoc-local", portal.now(), "10.0.0.1")])
    u = portal.usage(c)["kordoc-local"]
    assert (u["recent"], u["total"], u["users"], u["likes"]) == (1, 2, 2, 1), u
    assert portal.user_rank(c)[0] == {"user": "10.0.0.1", "n": 1}
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
assert "wqkgMjAyNiDquYDrj5nso7wgwrcgZG9uZ2p1a2ltLmRldkBnbWFpbC5jb20=" in _src and "signed(" in _src and "X-Author" in _src, "저작권 표기 누락"

print("selftest OK")
