"""폐쇄망 pip 패키지 목록(pkgindex.py) — 이름 정규화, 목록 파일 여러 형식, requirements·pyproject·setup.sh 읽기, 검사 경고, devsubmit 형식 검사 연동."""
import os, sqlite3, tempfile, unittest

from fastportal import pkgindex


class PkgIndexTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.execute("CREATE TABLE pkg_index(name TEXT PRIMARY KEY, versions TEXT, source TEXT, updated TEXT)")

    def test_norm(self):
        for a in ("Flask_SQLAlchemy", "flask.sqlalchemy", "FLASK--sqlalchemy", " flask-sqlalchemy "):
            self.assertEqual(pkgindex.norm(a), "flask-sqlalchemy")

    def test_parse_list_formats(self):
        text = """# 주석
numpy
Pandas==2.2.3
torch 2.4.0, 2.5.1
-i https://example.invalid/simple
Package    Version
---------- -------
scipy      1.14.1
fastapi>=0.110
requests (2.32.3)
Available versions: 2.32.3, 2.31.0
zope.interface @ file:///x.whl
"""
        e = pkgindex.parse_list(text)
        self.assertIsNone(e["numpy"])
        self.assertEqual(e["pandas"], {"2.2.3"})
        self.assertEqual(e["torch"], {"2.4.0", "2.5.1"})
        self.assertEqual(e["scipy"], {"1.14.1"})
        self.assertIsNone(e["fastapi"])
        self.assertEqual(e["requests"], {"2.32.3", "2.31.0"})
        self.assertIn("zope-interface", e)
        self.assertNotIn("package", e)
        self.assertNotIn("-i", e)

    def test_requirements_readers(self):
        r = pkgindex.parse_requirements("numpy==1.26.4  # 주석\nPandas[excel]>=2; python_version>'3.8'\n-r other.txt\ngit+https://x/y.git\n./wheels/a.whl\n-e .\n")
        self.assertEqual([(x["kind"], x["name"], x["versions"]) for x in r],
                         [("pkg", "numpy", ["1.26.4"]), ("pkg", "Pandas", []), ("git", "", []), ("path", "", []), ("path", "", [])])
        p = pkgindex.requirements_from_pyproject('[project]\nname="x"\ndependencies=["httpx==0.27.0", "rich"]\n')
        self.assertEqual([(x["name"], x["versions"]) for x in p], [("httpx", ["0.27.0"]), ("rich", [])])
        rs, files = pkgindex.requirements_from_setup_sh('set -e\npython3 -m venv venv\nvenv/bin/pip install -q --upgrade pip \\\n  "uvicorn==0.30.1" -r requirements.txt --index-url $IDX\n# pip install ignored\npip3 install https://x.invalid/a.tar.gz && echo ok\n')
        self.assertEqual([(x["kind"], x["name"]) for x in rs], [("pkg", "pip"), ("pkg", "uvicorn"), ("url", "")])
        self.assertEqual(files, ["requirements.txt"])

    def test_check_warnings(self):
        reqs = pkgindex.parse_requirements("numpy==1.26.4\nnumpy==9.9\nnotthere\ngit+https://x/y.git\nTorch\n")
        w, i = pkgindex.check_requirements(self.c, reqs)
        self.assertEqual(w, [])
        self.assertIn("건너뜀", i[0], "표가 비어 있으면 검사 안 하고 안내만")
        pkgindex.replace(self.c, {"numpy": {"1.26.4"}, "torch": None}, "t.txt")
        w, i = pkgindex.check_requirements(self.c, reqs, "requirements.txt")
        self.assertTrue(any(x.startswith("버전 없음: numpy==9.9") for x in w), w)
        self.assertTrue(any(x.startswith("폐쇄망에서 설치 불가: notthere") for x in w), w)
        self.assertTrue(any("git" in x and "설치 불가" in x for x in w), w)
        self.assertFalse(any("Torch" in x or x.startswith("버전 없음: numpy==1.26.4") for x in w), "있는 패키지·있는 버전·버전 모름은 경고 없음")
        self.assertEqual(len(w), 3)

    def test_search_lookup_meta(self):
        pkgindex.replace(self.c, {"torch": None, "torchvision": {"0.19.0"}, "pytorch-lightning": None, "numpy": None}, "list.txt")
        self.assertEqual([x["name"] for x in pkgindex.search(self.c, "Torch")], ["torch", "torchvision", "pytorch-lightning"], "접두 먼저, 부분 일치 뒤")
        self.assertEqual(pkgindex.search(self.c, "%"), [])
        lk = pkgindex.lookup(self.c, ["TorchVision", "nope"])
        self.assertTrue(lk["TorchVision"]["found"]); self.assertEqual(lk["TorchVision"]["versions"], ["0.19.0"])
        self.assertFalse(lk["nope"]["found"])
        m = pkgindex.meta(self.c)
        self.assertEqual((m["count"], m["source"]), (4, "list.txt"))

    def test_scan_tree(self):
        pkgindex.replace(self.c, {"fastapi": None}, "t")
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "x"))
            open(os.path.join(d, "requirements.txt"), "w").write("fastapi\nmissingpkg\n")
            open(os.path.join(d, "setup.sh"), "w").write("pip install -r requirements.txt otherpkg==1.0\n")
            w, i = pkgindex.scan_tree(self.c, d, ["requirements.txt", "setup.sh"])
            self.assertEqual(sorted(x.split(" (")[0] for x in w), ["폐쇄망에서 설치 불가: missingpkg", "폐쇄망에서 설치 불가: otherpkg"])
            w, i = pkgindex.scan_tree(self.c, d, ["x"])
            self.assertEqual(w, []); self.assertIn("없습니다", i[0])

    def test_seed_on_empty(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "closed-pypi-2026-01-01.txt"), "w").write("a\nb\n")
            open(os.path.join(d, "closed-pypi-2026-02-01.txt"), "w").write("a\nb\nc\n")
            c = sqlite3.connect(":memory:")
            orig = pkgindex.SEED_DIR
            pkgindex.SEED_DIR = d
            try:
                pkgindex.init(c)
                self.assertEqual(pkgindex.meta(c)["count"], 3, "가장 최근 파일")
                open(os.path.join(d, "closed-pypi-2026-03-01.txt"), "w").write("z\n")
                pkgindex.init(c)
                self.assertEqual(pkgindex.meta(c)["count"], 3, "표가 차 있으면 다시 읽지 않음")
            finally:
                pkgindex.SEED_DIR = orig


if __name__ == "__main__":
    unittest.main()
