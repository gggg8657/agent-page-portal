#!/usr/bin/env python3
"""할 일 검색 평가 — fastportal/tests/match_eval.json 의 검색어마다 기대 도구가 위 3개에 드는지(recall@3)와 첫 정답 순위(MRR).

  before  예전 검색: 이름·태그·설명·분류에 낱말이 다 들어 있는지(웹 catalog.ts matchScore 와 같은 규칙) — 도구 정보(tools.json)만
  lexical 새 검색에서 임베딩 없이(lexical + synonyms)
  after   새 검색(lexical + synonyms + semantic — EMBED_BASE_URL 이 있어야. 없으면 lexical 과 같음)

  python3 scripts/match_eval.py [--show] [--hard]       (임베딩: EMBED_BASE_URL=http://127.0.0.1:11436 EMBED_MODEL=bge-m3)
recall@3 = 기대 도구 중 위 3개에 든 비율(기대가 3개를 넘으면 3으로 나눔)의 평균, hit@3 = 하나라도 위 3개에 든 검색어 비율"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("AGENT_DATA", os.path.join(os.environ.get("TMPDIR", "/tmp"), "match-eval-data"))

from fastportal import match, tools  # noqa: E402

CAT = {"humanize-kr-local": "업무지원", "kordoc-local": "업무지원", "writer-local": "업무지원", "mail-local": "업무지원", "award-local": "업무지원",
       "weekly-local": "업무지원", "meeting-local": "업무지원", "coach-local": "업무지원", "tts-local": "업무지원", "review-local": "연구/실험",
       "protein-local": "연구/실험", "battery-local": "연구/실험", "shield-local": "연구/실험", "agent-cad-local": "연구/실험", "sql-local": "분석/데이터",
       "digitizer-local": "분석/데이터", "chart-local": "분석/데이터", "agent-builder-local": "개발/코딩", "codingtest-local": "개발/코딩",
       "diagram-local": "디자인/시각화", "sketch-local": "디자인/시각화", "image-local": "디자인/시각화", "avatar-local": "디자인/시각화",
       "notebook-local": "교육/학습", "saju-local": "생활/기타", "padong-local": "생활/기타"}


def old_rank(q):
    """웹 catalog.ts matchScore(인기 점수는 같은 값으로 봄 — 시험 데이터 없이)"""
    ws = [w for w in q.lower().split() if w]
    out = []
    for t in tools.tools():
        if t.get("hidden"):
            continue
        name = f"{t.get('name', '')} {t.get('title', '')}".lower()
        tags = " ".join(t.get("tags") or []).lower()
        rest = f"{t.get('desc', '')} {t.get('group', '')} {CAT.get(t['dir'], '')}".lower()
        s = 0
        for w in ws:
            k = w.lstrip("#")
            v = (6 if k in name else 0) + (4 if k in tags else 0) + (2 if k in rest else 0)
            if not v:
                s = 0
                break
            s += v
        if s:
            out.append((s, t["dir"]))
    out.sort(key=lambda x: (-x[0], x[1]))
    return [d for _, d in out]


def metrics(rows):
    r3 = sum(len(set(e) & set(got[:3])) / min(3, len(e)) for _, e, got in rows) / len(rows)
    hit = sum(bool(set(e) & set(got[:3])) for _, e, got in rows) / len(rows)
    mrr = sum(next((1 / (i + 1) for i, d in enumerate(got) if d in e), 0) for _, e, got in rows) / len(rows)
    return r3, hit, mrr


def main():
    allq = json.load(open(os.path.join(ROOT, "fastportal", "tests", "match_eval.json"), encoding="utf-8"))
    ev = allq["hard"] if "--hard" in sys.argv else allq["queries"]
    show = "--show" in sys.argv
    sem_ok = match.cap_vectors() is not None
    if not sem_ok and os.environ.get("EMBED_BASE_URL"):
        import time
        for _ in range(120):
            time.sleep(0.5)
            if match.cap_vectors() is not None:
                sem_ok = True
                break
    res = {}
    for mode in ("before", "lexical", "after"):
        rows = []
        for x in ev:
            if mode == "before":
                got = old_rank(x["q"])
            else:
                qv = match.query_vector(x["q"]) if (mode == "after" and sem_ok) else None
                got = [r["dir"] for r in match.rank(x["q"], is_owner=True, qvec=qv, limit=10)]
            rows.append((x["q"], x["expect"], got))
        res[mode] = (metrics(rows), rows)
    print(f"검색어 {len(ev)}개 · 임베딩 {'켜짐' if sem_ok else '없음'}")
    for mode, ((r3, hit, mrr), _) in res.items():
        print(f"  {mode:8s} recall@3 {r3:.3f}  hit@3 {hit:.3f}  MRR {mrr:.3f}")
    if show:
        for (q, e, b), (_, _, a) in zip(res["before"][1], res["after"][1]):
            mark = "" if set(e) & set(a[:3]) else "  ✗"
            print(f"{q:22s} 기대 {','.join(x.replace('-local', '') for x in e):34s} 예전 {','.join(x.replace('-local', '') for x in b[:3]) or '-':30s} 새 {','.join(x.replace('-local', '') for x in a[:3])}{mark}")
    if "--json" in sys.argv:
        print(json.dumps({m: dict(zip(("recall@3", "hit@3", "mrr"), v[0])) for m, v in res.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
