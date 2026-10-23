"""hello-local 자가검증 — 격리 검증이 실행함(네트워크 없음). 사용자별 폴더가 나뉘는지만 확인"""
import os, sys, tempfile, types

os.environ["WORKSPACE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402

h = lambda u: types.SimpleNamespace(headers={"X-Agent-User": u})
a, _ = app.user_ws(h("kim"))
b, _ = app.user_ws(h("../lee"))
assert a != b and a.startswith(os.environ["WORKSPACE"]) and b.startswith(os.path.join(os.environ["WORKSPACE"], "u")), (a, b)
print("ok")
