"""모두의 에이전트 (B) 기존 에이전트 연결 — 도구 API 를 순서대로 이어 붙인 '흐름'을 계정별로 저장하고 포털 서버가 실행한다.

  연결할 수 있는 단계는 아래 ADAPTERS 에 정의한 것뿐(사용자가 임의 주소·API 를 부를 수 없음). 어댑터 = 도구·작업 이름, 입력 칸, 출력, 부르는 법.
  흐름: 단계 2~6개, 단계마다 입력 칸 매핑 {from: prev(바로 앞 결과) | step(앞 단계 n 의 결과) | const(고정값) | ask(실행 때 입력)}.
  실행: 포털이 순서대로 도구 API 호출 — 프록시와 같은 X-Agent-User(= 실행자 계정 id, 로컬 주인 토큰만 owner) + 서명(X-Agent-Auth). 남의 흐름은 보지도 돌리지도 못함(403).
        단계별 시간 제한, 사용자당 동시 실행 1, 전체 동시 실행 FLOW_MAX_RUNS. 진행·결과는 작업 알림(jobs, tool='flows')과 flow_runs 표.
        실패한 단계에서 멈추고 이유를 남김.
API: GET /api/flows · /api/flows/adapters · /api/flows/<id> · /api/flows/runs/<run_id>
     POST /api/flows/save · /api/flows/delete · /api/flows/run (run 만 본문 상한 RUN_MAX — 파일 입력)"""
import base64, csv, io, json, os, re, secrets, threading, time, urllib.error, urllib.request

from . import accounts, config, jobs, timeutil, tools, toolstate
from .db import db, now, own_row

MIN_STEPS, MAX_STEPS = 2, 6
MAX_FLOWS = 50                      # 계정당 저장 흐름 수
TEXT_MAX = 20000                    # 입력·고정값 글자 상한
OUT_MAX = 100000                    # 단계 결과 글자 상한(저장)
SVG_MAX = 400000
RUN_MAX = int(os.environ.get("FLOW_RUN_MAX_BYTES", str(40 * 1024 * 1024)))   # /api/flows/run 본문 상한(녹음 파일 base64 포함)
FILE_MAX = 25 * 1024 * 1024        # 파일 입력 하나(디코드 후)
MAX_RUNS = int(os.environ.get("FLOW_MAX_RUNS", "4"))   # 포털 전체 동시 실행
ID_RE = re.compile(r"[A-Za-z0-9_-]{6,40}")
JOB_TOOL = "flows"                  # 작업 알림의 도구 이름(링크는 /create 로 — jobs.public)


def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS flows(id TEXT PRIMARY KEY, uid TEXT NOT NULL, name TEXT NOT NULL, steps TEXT NOT NULL,
                                     created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS flows_uid ON flows(uid, updated);
    CREATE TABLE IF NOT EXISTS flow_runs(id TEXT PRIMARY KEY, flow TEXT NOT NULL, uid TEXT NOT NULL, agent_user TEXT NOT NULL, name TEXT,
                                         status TEXT NOT NULL, started REAL, finished REAL, steps TEXT NOT NULL, error TEXT);
    CREATE INDEX IF NOT EXISTS flow_runs_uid ON flow_runs(uid, started);""")


# ── 도구 호출(서명·리디렉트 안 따름·등록된 주소만) ──────────────────────────
class StepError(Exception):
    pass


def _tool_url(d, path):
    t = tools.tool(d)
    if not t or (t.get("hidden") and not config.SHOW_HIDDEN):
        raise StepError(f"{d} 는 등록된 도구가 아닙니다")
    if toolstate.is_off(d):   # 관리자가 끈 도구 — 이미 저장한 흐름도 이 단계에서 멈춤
        raise StepError(f"{t.get('name') or d}: {toolstate.MSG}")
    host = tools.tool_host(t)
    if not host:
        raise StepError(f"{d} 주소 설정이 틀립니다 — 관리자에게 알려 주세요")
    if not tools.alive_sync(tools.tool_addr(t)):
        raise StepError(f"{t.get('name') or d} 도구가 꺼져 있습니다 — 홈에서 도구를 시작한 뒤 다시 실행하세요")
    return f"http://{host}:{int(t['port'])}{path}"


def _req(user, d, path, body, deadline):
    url = _tool_url(d, path)
    data = json.dumps(body, ensure_ascii=False).encode()
    hd = {**jobs.tool_headers(user, d), "Content-Type": "application/json", "Accept-Encoding": "identity"}
    left = deadline - time.time()
    if left <= 0:
        raise StepError("단계 시간 제한을 넘었습니다")
    try:
        return jobs.TOOL_OPENER.open(urllib.request.Request(url, data=data, headers=hd, method="POST"), timeout=min(left, 600))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(65536) or b"{}").get("error") or ""
        except (ValueError, AttributeError, OSError):
            msg = ""
        raise StepError(f"{d} 오류 HTTP {e.code}" + (f": {str(msg)[:300]}" if msg else ""))
    except (urllib.error.URLError, OSError) as e:
        raise StepError(f"{d} 응답 없음 ({type(e).__name__})")


def call_json(user, d, path, body, deadline):
    with _req(user, d, path, body, deadline) as r:
        raw = r.read(8 * 1024 * 1024)
    try:
        return json.loads(raw)
    except ValueError:
        raise StepError(f"{d} 응답이 JSON 이 아닙니다")


def call_sse(user, d, path, body, deadline, progress=None):
    """SSE(data: <JSON>) 를 끝까지 읽어 done 값을 돌려줌. error 이벤트·시간 초과는 StepError"""
    with _req(user, d, path, body, deadline) as r:
        if "text/event-stream" not in (r.headers.get("Content-Type") or ""):
            try:
                o = json.loads(r.read(1024 * 1024) or b"{}")
            except ValueError:
                o = {}
            if isinstance(o, dict) and o.get("error"):
                raise StepError(f"{d}: {str(o['error'])[:300]}")
            raise StepError(f"{d} 가 스트림을 주지 않았습니다")
        while True:
            if time.time() > deadline:
                raise StepError("단계 시간 제한을 넘었습니다")
            line = r.readline(4 * 1024 * 1024)
            if not line:
                raise StepError(f"{d} 스트림이 결과 없이 끝났습니다")
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            try:
                ev = json.loads(line[5:].strip() or b"{}")
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            if "error" in ev:
                raise StepError(f"{d}: {str(ev['error'])[:300]}")
            if "done" in ev:
                return ev["done"]
            if progress and ev.get("stage") and ev.get("msg"):
                progress(str(ev["msg"])[:120])


def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode()


def _need(v, label):
    v = (v or "").strip() if isinstance(v, str) else v
    if not v:
        raise StepError(f"'{label}' 입력이 비었습니다")
    return v


# ── 어댑터 ───────────────────────────────────────────────────────────────
# 입력 칸: key·label·type(text|textarea|select|file)·required·main(앞 단계 결과를 받는 기본 칸)·options·default
# run(user, v, deadline, progress) → {"text": 결과 글, "files": [{"name","url"}], "svg"?: 문자열}
def _writer_notice(user, v, dl, pg):
    vals = {"title": _need(v.get("title"), "제목"), "body": _need(v.get("body"), "공지 내용")}
    for k in ("when", "to", "action"):
        if (v.get(k) or "").strip():
            vals[k] = v[k].strip()
    d = call_sse(user, "writer-local", "/api/generate", {"pattern": "notice", "values": vals}, dl, pg)
    out = (d.get("variants") or [d.get("raw") or ""])[0]
    return {"text": str(out).strip()}


def _humanize(user, v, dl, pg):
    body = {"text": _need(v.get("text"), "다듬을 글"), "genre": v.get("genre") or "report", "route": v.get("route") or "light"}
    d = call_sse(user, "humanize-kr-local", "/api/humanize", body, dl, pg)
    cr = d.get("change_rate")
    note = f"판정 {d.get('gate') or '-'}" + (f" · 변경률 {cr * 100:.1f}%" if isinstance(cr, (int, float)) else "") + \
        (" · 원문 유지(되돌림)" if d.get("finalized") == "abort" or d.get("rolled_back") else "")
    return {"text": str(d.get("output") or "").strip(), "note": note}


def _kordoc_polish(user, v, dl, pg):
    d = call_json(user, "kordoc-local", "/api/polish_text", {"text": _need(v.get("text"), "다듬을 글"), "strength": v.get("strength") or "standard"}, dl)
    return {"text": str(d.get("output") or "").strip(), "note": f"고친 문장 {len(d.get('changes') or [])}곳 · 되돌린 문장 {len(d.get('rejected') or [])}곳"}


def _kordoc_draft(user, v, dl, pg):
    text = _need(v.get("text"), "문서에 담을 내용")
    body = {"task": "draft", "question": (v.get("request") or "").strip() or "첨부한 내용으로 문서 초안을 작성해 주세요",
            "file_name": "input.md", "file_b64": _b64(text), "preset": v.get("preset") or "보고서"}
    d = call_sse(user, "kordoc-local", "/api/run", body, dl, pg)
    files = []
    if d.get("hwpx") and d.get("run_id"):
        files.append({"name": str(d["hwpx"]), "url": f"/t/kordoc-local/api/runs/{d['run_id']}/{d['hwpx']}"})
    return {"text": str(d.get("output") or "").strip(), "files": files}


def _mail_write(user, v, dl, pg):
    inputs = {"points": _need(v.get("points"), "핵심 내용"), "purpose": v.get("purpose") or "보고", "tone": v.get("tone") or "정중",
              "length": v.get("length") or "보통", "lang": "ko", "to": {"relation": v.get("relation") or "동료"}}
    d = call_sse(user, "mail-local", "/api/run", {"kind": "write", "inputs": inputs}, dl, pg)
    res = d.get("result") or {}
    subj = (res.get("subjects") or [""])[0]
    return {"text": (f"제목: {subj}\n\n" if subj else "") + str(res.get("body") or "").strip()}


def _review(user, v, dl, pg):
    text = _need(v.get("text"), "검토할 원고")
    rec = call_json(user, "review-local", "/api/upload", {"name": "manuscript.md", "data": _b64(text)}, dl)
    if not rec.get("id"):
        raise StepError("review-local 이 이력 id 를 주지 않았습니다")
    d = call_sse(user, "review-local", "/api/run", {"id": rec["id"], "depth": "quick", "journal": v.get("journal") or ""}, dl, pg)
    rv = d.get("review") or {}
    lines = []
    verdict = rv.get("verdict") or {}
    if verdict.get("label"):
        lines.append(f"## 판정: {verdict['label']}")
    for r in verdict.get("reasons") or []:
        lines.append(f"- {r.get('text', '')}")
    if rv.get("questions"):
        lines.append("\n## 질문")
        lines += [f"- {q.get('q', '')}" for q in rv["questions"]]
    for i in (d.get("issues") or [])[:30]:
        if isinstance(i, dict) and (i.get("title") or i.get("msg") or i.get("text")):
            lines.append(f"- [점검] {i.get('title') or i.get('msg') or i.get('text')}")
    return {"text": "\n".join(lines).strip() or "리뷰 결과가 비었습니다"}


def _diagram(user, v, dl, pg):
    d = call_sse(user, "diagram-local", "/api/diagram", {"messages": [{"role": "user", "content": _need(v.get("text"), "그림으로 그릴 내용")}],
                                                          "type": v.get("type") or "auto"}, dl, pg)
    src = str(d.get("source") or "").strip()
    if not src:
        raise StepError("다이어그램 결과가 비었습니다")
    return {"text": src, "mermaid": True}


def _sql(user, v, dl, pg):
    d = call_sse(user, "sql-local", "/api/ask", {"question": _need(v.get("question"), "질문")}, dl, pg)
    if d.get("error"):
        raise StepError(f"sql-local: {str(d['error'])[:300]}")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(d.get("columns") or [])
    for row in (d.get("rows") or [])[:500]:
        w.writerow(["" if x is None else x for x in row])
    return {"text": buf.getvalue().strip(), "note": "실행한 SQL: " + (d.get("sql") or "")[:2000]}


def _chart(user, v, dl, pg):
    p = call_json(user, "chart-local", "/api/parse", {"text": _need(v.get("table"), "표(CSV·붙여넣기)")}, dl)
    s = call_json(user, "chart-local", "/api/suggest", {"table": p.get("table") or {}, "source": "모두의 에이전트 연결"}, dl)
    cands = s.get("candidates") or []
    if not cands:
        raise StepError("그래프 후보가 없습니다(숫자 열이 있는지 확인)")
    top = cands[0]
    text = "그래프 후보 " + str(len(cands)) + "개\n" + "\n".join(f"{i + 1}. {c.get('name') or c.get('kind')} — x: {c.get('x') or '행 순서'}, y: {', '.join(c.get('ys') or [])}"
                                                           for i, c in enumerate(cands[:6]))
    return {"text": text, "svg": str(top.get("svg") or "")[:SVG_MAX]}


def _meeting(user, v, dl, pg):
    f = v.get("audio")
    if not isinstance(f, dict) or not f.get("b64"):
        raise StepError("'녹음 파일' 입력이 비었습니다")
    d = call_sse(user, "meeting-local", "/api/run", {"file_b64": f["b64"], "file_name": f.get("name") or "audio.m4a", "speakers": 0}, dl, pg)
    rid = d.get("run_id")
    if not rid or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", str(rid)):
        raise StepError("meeting-local 이 전사 id 를 주지 않았습니다")
    meta = {"title": (v.get("title") or "").strip(), "date": (v.get("date") or "").strip()}
    d = call_sse(user, "meeting-local", f"/api/finalize/{rid}", {"names": {}, "meta": meta}, dl, pg)
    return {"text": str(d.get("minutes") or "").strip()}


GENRES = ["report", "essay", "column", "blog", "abstract"]
PRESETS = ["보고서", "기안문", "간이기안문", "계획서", "통지", "회의록", "개조식", "업무보고", "보도자료"]
ADAPTERS = {
    "meeting.minutes": {"tool": "meeting-local", "title": "회의록 만들기", "desc": "녹음 파일 → 전사·화자 분리 → 회의록(Markdown). GPU 전사라 녹음 길이의 10~30% 걸림",
                        "inputs": [{"key": "audio", "label": "녹음 파일", "type": "file", "required": True, "accept": ".m4a,.mp3,.wav,.webm,.ogg,.flac,.aac,.mp4"},
                                   {"key": "title", "label": "회의 제목", "type": "text"}, {"key": "date", "label": "회의 날짜", "type": "text"}],
                        "run": _meeting, "timeout": 3600, "heavy": True},
    "writer.notice": {"tool": "writer-local", "title": "사내 공지문 초안", "desc": "제목·내용 → 공지문 초안(첫 번째 안)",
                      "inputs": [{"key": "body", "label": "공지 내용", "type": "textarea", "required": True, "main": True},
                                 {"key": "title", "label": "제목", "type": "text", "required": True},
                                 {"key": "when", "label": "일시", "type": "text"}, {"key": "to", "label": "대상", "type": "text"},
                                 {"key": "action", "label": "요청 사항", "type": "text"}], "run": _writer_notice, "timeout": 300},
    "humanize.polish": {"tool": "humanize-kr-local", "title": "글 다듬기(자연스럽게)", "desc": "AI 티 나는 문장을 자연스러운 한국어로",
                        "inputs": [{"key": "text", "label": "다듬을 글", "type": "textarea", "required": True, "main": True},
                                   {"key": "genre", "label": "글 종류", "type": "select", "options": GENRES, "default": "report"},
                                   {"key": "route", "label": "강도", "type": "select", "options": ["light", "standard", "heavy"], "default": "light"}],
                        "run": _humanize, "timeout": 600},
    "kordoc.polish": {"tool": "kordoc-local", "title": "공문체 윤문", "desc": "공문서 표기·문체로 다듬기(서식 없는 글)",
                      "inputs": [{"key": "text", "label": "다듬을 글", "type": "textarea", "required": True, "main": True},
                                 {"key": "strength", "label": "강도", "type": "select", "options": ["light", "standard", "strong"], "default": "standard"}],
                      "run": _kordoc_polish, "timeout": 600},
    "kordoc.draft": {"tool": "kordoc-local", "title": "공문 양식 초안", "desc": "내용 → 보고서·기안문 등 양식 초안 + HWPX 파일",
                     "inputs": [{"key": "text", "label": "문서에 담을 내용", "type": "textarea", "required": True, "main": True},
                                {"key": "preset", "label": "양식", "type": "select", "options": PRESETS, "default": "보고서"},
                                {"key": "request", "label": "요청 문장", "type": "text"}], "run": _kordoc_draft, "timeout": 900},
    "mail.write": {"tool": "mail-local", "title": "메일 초안", "desc": "핵심 내용 → 업무 메일 제목·본문",
                   "inputs": [{"key": "points", "label": "핵심 내용", "type": "textarea", "required": True, "main": True},
                              {"key": "purpose", "label": "목적", "type": "select", "options": ["보고", "요청", "안내", "감사", "회신", "자료 송부", "협조 요청", "문의", "일정 조율", "기타"], "default": "보고"},
                              {"key": "relation", "label": "받는 사람", "type": "select", "options": ["상급자", "동료", "후배·팀원", "타 부서", "외부기관", "협력업체"], "default": "동료"},
                              {"key": "tone", "label": "말투", "type": "select", "options": ["격식", "정중", "간결", "친근"], "default": "정중"},
                              {"key": "length", "label": "길이", "type": "select", "options": ["짧게", "보통", "자세히"], "default": "보통"}],
                   "run": _mail_write, "timeout": 300},
    "review.quick": {"tool": "review-local", "title": "원고 검토(빠름)", "desc": "원고 → 심사위원식 판정·질문. 서버 전체에서 한 번에 하나, 수 분",
                     "inputs": [{"key": "text", "label": "검토할 원고", "type": "textarea", "required": True, "main": True},
                                {"key": "journal", "label": "대상 저널·분야", "type": "text"}], "run": _review, "timeout": 1800},
    "diagram.mermaid": {"tool": "diagram-local", "title": "다이어그램 만들기", "desc": "설명 → Mermaid 그림 소스",
                        "inputs": [{"key": "text", "label": "그림으로 그릴 내용", "type": "textarea", "required": True, "main": True},
                                   {"key": "type", "label": "그림 종류", "type": "select", "options": ["auto", "flowchart", "sequence", "mindmap", "timeline", "gantt"], "default": "auto"}],
                        "run": _diagram, "timeout": 600},
    "sql.ask": {"tool": "sql-local", "title": "데이터 질의(SQL)", "desc": "자연어 질문 → 읽기 전용 SQL 실행 → 결과 표(CSV)",
                "inputs": [{"key": "question", "label": "질문", "type": "textarea", "required": True, "main": True}], "run": _sql, "timeout": 300},
    "chart.suggest": {"tool": "chart-local", "title": "표 → 그래프", "desc": "CSV·붙여넣은 표 → 그래프 후보와 1순위 그림",
                      "inputs": [{"key": "table", "label": "표(CSV·붙여넣기)", "type": "textarea", "required": True, "main": True}], "run": _chart, "timeout": 120},
}


def adapter_list(up=None):
    out = []
    for aid, a in ADAPTERS.items():
        t = tools.tool(a["tool"])
        if not t or t.get("hidden") or toolstate.unlisted(t):   # 관리자가 끄거나 숨긴 도구의 어댑터는 목록에서 뺌
            continue
        out.append({"id": aid, "tool": a["tool"], "tool_name": t.get("name") or a["tool"], "title": a["title"], "desc": a["desc"],
                    "inputs": a["inputs"], "heavy": bool(a.get("heavy")), "timeout": a["timeout"],
                    "up": bool(up.get(tools.tool_addr(t))) if up is not None else None})
    return out


# ── 흐름 저장·검증 ───────────────────────────────────────────────────────
def _clean_steps(steps):
    if not isinstance(steps, list) or not (MIN_STEPS <= len(steps) <= MAX_STEPS):
        raise ValueError(f"단계는 {MIN_STEPS}~{MAX_STEPS}개여야 합니다")
    out = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or s.get("adapter") not in ADAPTERS:
            raise ValueError(f"{i + 1}단계: 연결할 수 없는 작업입니다")
        a = ADAPTERS[s["adapter"]]
        t = tools.tool(a["tool"])
        if not t or t.get("hidden") or toolstate.unlisted(t):   # 숨긴 도구로 새 흐름은 못 만듦(저장된 흐름은 실행됨 — 위 _tool 은 꺼짐만 막음)
            raise ValueError(f"{i + 1}단계: 목록에 없는 도구입니다")
        m = s.get("inputs") or {}
        if not isinstance(m, dict):
            raise ValueError(f"{i + 1}단계: 입력 매핑 형식이 틀립니다")
        clean = {}
        for f in a["inputs"]:
            src = m.get(f["key"])
            if src is None:
                if f.get("main") and i > 0:
                    src = {"from": "prev"}
                elif f.get("required"):
                    src = {"from": "ask"}
                elif f.get("default"):
                    src = {"from": "const", "value": f["default"]}
                else:
                    continue
            if not isinstance(src, dict):
                raise ValueError(f"{i + 1}단계 '{f['label']}': 매핑 형식이 틀립니다")
            kind = src.get("from")
            if f["type"] == "file" and kind != "ask":
                raise ValueError(f"{i + 1}단계 '{f['label']}': 파일은 실행 때 올려야 합니다")
            if kind == "prev":
                if i == 0:
                    raise ValueError("첫 단계는 앞 단계 결과를 받을 수 없습니다")
                clean[f["key"]] = {"from": "prev"}
            elif kind == "step":
                n = src.get("step")
                if not isinstance(n, int) or not (0 <= n < i):
                    raise ValueError(f"{i + 1}단계 '{f['label']}': 앞 단계 번호가 틀립니다")
                clean[f["key"]] = {"from": "step", "step": n}
            elif kind == "const":
                val = src.get("value")
                if not isinstance(val, str) or len(val) > TEXT_MAX:
                    raise ValueError(f"{i + 1}단계 '{f['label']}': 고정값은 {TEXT_MAX}자 이하 글")
                if f["type"] == "select" and val not in f["options"]:
                    raise ValueError(f"{i + 1}단계 '{f['label']}': 목록에 있는 값을 고르세요")
                if f.get("required") and not val.strip():
                    raise ValueError(f"{i + 1}단계 '{f['label']}': 필수 칸이 비었습니다")
                clean[f["key"]] = {"from": "const", "value": val}
            elif kind == "ask":
                clean[f["key"]] = {"from": "ask"}
            else:
                raise ValueError(f"{i + 1}단계 '{f['label']}': from 은 prev|step|const|ask")
        out.append({"adapter": s["adapter"], "inputs": clean})
    return out


def _own(c, uid, fid):
    """흐름 하나(관리자도 남의 흐름은 못 봄)"""
    return own_row(c, "flows", fid, uid, "흐름을 찾을 수 없습니다", "다른 사람의 흐름입니다")


def _flow_public(r):
    return {"id": r["id"], "name": r["name"], "steps": json.loads(r["steps"]), "created": r["created"], "updated": r["updated"]}


def save(c, uid, req):
    name = str(req.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("흐름 이름을 적어 주세요")
    steps = _clean_steps(req.get("steps"))
    fid = req.get("id")
    if fid:
        _own(c, uid, fid)
        c.execute("UPDATE flows SET name=?, steps=?, updated=? WHERE id=?", (name, json.dumps(steps, ensure_ascii=False), now(), fid))
    else:
        if c.execute("SELECT COUNT(*) FROM flows WHERE uid=?", (uid,)).fetchone()[0] >= MAX_FLOWS:
            raise ValueError(f"흐름은 계정당 {MAX_FLOWS}개까지 저장합니다")
        fid = secrets.token_urlsafe(9)
        c.execute("INSERT INTO flows VALUES(?,?,?,?,?,?)", (fid, uid, name, json.dumps(steps, ensure_ascii=False), now(), now()))
    return _flow_public(_own(c, uid, fid))


def delete(c, uid, req):
    _own(c, uid, req.get("id"))
    c.execute("DELETE FROM flows WHERE id=?", (req["id"],))
    return {"ok": True}


def listing(c, uid):
    flows = [_flow_public(r) for r in c.execute("SELECT * FROM flows WHERE uid=? ORDER BY updated DESC", (uid,))]
    runs = [_run_public(r, brief=True) for r in c.execute("SELECT * FROM flow_runs WHERE uid=? ORDER BY started DESC LIMIT 20", (uid,))]
    return {"flows": flows, "runs": runs, "limits": {"min_steps": MIN_STEPS, "max_steps": MAX_STEPS, "run_max_bytes": RUN_MAX, "file_max_bytes": FILE_MAX}}


def _run_public(r, brief=False):
    d = {"id": r["id"], "flow": r["flow"], "name": r["name"], "status": r["status"], "started": r["started"], "finished": r["finished"], "error": r["error"]}
    steps = json.loads(r["steps"])
    if brief:
        d["steps"] = [{"adapter": s["adapter"], "status": s["status"]} for s in steps]
    else:
        d["steps"] = steps
    return d


def get_flow(c, uid, fid):
    return _flow_public(_own(c, uid, fid))


def get_run(c, uid, rid):
    if not isinstance(rid, str) or not ID_RE.fullmatch(rid):
        raise FileNotFoundError("실행 기록을 찾을 수 없습니다")
    r = c.execute("SELECT * FROM flow_runs WHERE id=?", (rid,)).fetchone()
    if not r:
        raise FileNotFoundError("실행 기록을 찾을 수 없습니다")
    if r["uid"] != uid:
        raise PermissionError("다른 사람의 실행 기록입니다")
    return _run_public(r)


# ── 실행 ─────────────────────────────────────────────────────────────────
_LOCK = threading.Lock()
_ACTIVE = {}   # uid → run id. shortcut: 프로세스 메모리 — 포털 프로세스가 여럿이면 사용자당 1개 실행이 프로세스 수만큼, 그때 DB 로


def _resolve_inputs(flow_steps, given):
    """실행 때 입력 검사 → 단계별 '실행 때 입력' 값 {i: {key: 값}}. 파일은 {name, b64}(디코드 크기 확인)"""
    if not isinstance(given, dict):
        raise ValueError("inputs 는 객체여야 합니다")
    out = {}
    for i, s in enumerate(flow_steps):
        a = ADAPTERS[s["adapter"]]
        for f in a["inputs"]:
            m = s["inputs"].get(f["key"])
            if not m or m["from"] != "ask":
                continue
            v = given.get(f"{i}.{f['key']}")
            if f["type"] == "file":
                if v is None and not f.get("required"):
                    continue
                if not isinstance(v, dict) or not isinstance(v.get("b64"), str) or not v["b64"]:
                    raise ValueError(f"{i + 1}단계 '{f['label']}' 파일을 올려 주세요")
                if len(v["b64"]) * 3 // 4 > FILE_MAX:
                    raise ValueError(f"{i + 1}단계 '{f['label']}': 파일은 {FILE_MAX // (1024 * 1024)}MB 이하")
                try:
                    base64.b64decode(v["b64"][:4096] + "=" * (-len(v["b64"][:4096]) % 4), validate=False)
                except ValueError:
                    raise ValueError(f"{i + 1}단계 '{f['label']}': base64 가 아닙니다")
                nm = re.sub(r"[^\w.\-가-힣 ]", "_", str(v.get("name") or "file"))[:120]
                out.setdefault(i, {})[f["key"]] = {"name": nm, "b64": v["b64"]}
                continue
            if v is None:
                v = ""
            if not isinstance(v, str) or len(v) > TEXT_MAX:
                raise ValueError(f"{i + 1}단계 '{f['label']}': {TEXT_MAX}자 이하 글")
            if f["type"] == "select" and v and v not in f["options"]:
                raise ValueError(f"{i + 1}단계 '{f['label']}': 목록에 있는 값을 고르세요")
            if f.get("required") and not v.strip():
                raise ValueError(f"{i + 1}단계 '{f['label']}' 을(를) 입력해 주세요")
            out.setdefault(i, {})[f["key"]] = v
    return out


def busy(uid):
    """본문을 읽기 전 빠른 확인(app.py) — 이 사용자가 이미 실행 중이거나 전체가 꽉 찼으면 문구, 아니면 None. 최종 판정은 start_run 의 잠금 안에서"""
    with _LOCK:
        if uid in _ACTIVE:
            return "이미 실행 중인 흐름이 있습니다 — 끝난 뒤 다시 실행하세요(사용자당 한 번에 하나)"
        if len(_ACTIVE) >= MAX_RUNS:
            return "포털에서 실행 중인 흐름이 많습니다 — 잠시 뒤 다시 실행하세요"
    return None


def start_run(c, uid, agent_user, req):
    """POST /api/flows/run {id, inputs} → {run}. 검사·자리 잡기만 하고 실행은 스레드에서"""
    r = _own(c, uid, req.get("id"))
    steps = json.loads(r["steps"])
    asked = _resolve_inputs(steps, req.get("inputs") or {})
    with _LOCK:
        if uid in _ACTIVE:
            raise ValueError("이미 실행 중인 흐름이 있습니다 — 끝난 뒤 다시 실행하세요(사용자당 한 번에 하나)")
        if len(_ACTIVE) >= MAX_RUNS:
            raise ValueError("포털에서 실행 중인 흐름이 많습니다 — 잠시 뒤 다시 실행하세요")
        rid = secrets.token_urlsafe(9)
        _ACTIVE[uid] = rid
    try:
        st = [{"adapter": s["adapter"], "tool": ADAPTERS[s["adapter"]]["tool"], "title": ADAPTERS[s["adapter"]]["title"], "status": "queued",
               "started": None, "finished": None, "text": "", "files": [], "error": None, "msg": ""} for s in steps]
        c.execute("INSERT INTO flow_runs VALUES(?,?,?,?,?,?,?,?,?,?)", (rid, r["id"], uid, agent_user, r["name"], "running", time.time(), None,
                                                                      json.dumps(st, ensure_ascii=False), None))
        accounts.audit(c, uid, "flow_run", r["id"], f"{r['name'][:60]} #{rid}")
    except Exception:
        with _LOCK:
            _ACTIVE.pop(uid, None)
        raise
    # 실행 스레드는 이 트랜잭션이 커밋된 뒤에 시작해야 함(handle_post 가 with 블록을 나온 뒤 부름)
    go = lambda: threading.Thread(target=_execute, args=(rid, uid, agent_user, r["name"], steps, asked, st), daemon=True, name=f"flow-{rid}").start()
    return {"run": {"id": rid, "status": "running"}}, go


def _job(agent_user, rid, status, name, msg, progress=None):
    try:
        j = jobs.upsert(agent_user, JOB_TOOL, status, rid, f"흐름: {name}"[:200], f"/create?tab=make&m=flow&run={rid}", msg, progress)
        if j:
            jobs.HUB.publish("job", j, j["user"])
    except Exception as e:   # 알림 실패가 실행을 멈추지 않게
        print("flows: 작업 알림 실패", type(e).__name__, e, flush=True)


def _save_steps(rid, st, status=None, error=None):
    with db() as c:
        if status:
            c.execute("UPDATE flow_runs SET steps=?, status=?, finished=?, error=? WHERE id=?",
                      (json.dumps(st, ensure_ascii=False), status, time.time() if status != "running" else None, error, rid))
        else:
            c.execute("UPDATE flow_runs SET steps=? WHERE id=?", (json.dumps(st, ensure_ascii=False), rid))


def _execute(rid, uid, agent_user, name, steps, asked, st):
    n = len(steps)
    results = []
    try:
        _job(agent_user, rid, "running", name, f"0/{n} 단계", 0)
        for i, s in enumerate(steps):
            a = ADAPTERS[s["adapter"]]
            vals = {}
            for f in a["inputs"]:
                m = s["inputs"].get(f["key"])
                if not m:
                    continue
                if m["from"] == "prev":
                    vals[f["key"]] = results[i - 1]["text"]
                elif m["from"] == "step":
                    vals[f["key"]] = results[m["step"]]["text"]
                elif m["from"] == "const":
                    vals[f["key"]] = m["value"]
                else:
                    vals[f["key"]] = asked.get(i, {}).get(f["key"], "")
            st[i].update(status="running", started=time.time())
            _save_steps(rid, st)
            _job(agent_user, rid, "running", name, f"{i + 1}/{n} {a['title']} 진행 중", int(100 * i / n))
            last = {"t": 0.0}

            def pg(msg, i=i):
                if time.time() - last["t"] > 2:   # DB 를 너무 자주 쓰지 않게
                    last["t"] = time.time()
                    st[i]["msg"] = msg
                    _save_steps(rid, st)
            try:
                out = a["run"](agent_user, vals, time.time() + a["timeout"], pg)
            except StepError as e:
                st[i].update(status="failed", finished=time.time(), error=str(e)[:500])
                for k in range(i + 1, n):
                    st[k]["status"] = "skipped"
                _save_steps(rid, st, "failed", f"{i + 1}단계({a['title']})에서 멈춤: {str(e)[:300]}")
                _job(agent_user, rid, "failed", name, f"{i + 1}단계 {a['title']} 실패: {str(e)[:200]}")
                return
            text = str(out.get("text") or "")
            if not text.strip():
                st[i].update(status="failed", finished=time.time(), error="결과가 비었습니다")
                for k in range(i + 1, n):
                    st[k]["status"] = "skipped"
                _save_steps(rid, st, "failed", f"{i + 1}단계({a['title']}) 결과가 비었습니다")
                _job(agent_user, rid, "failed", name, f"{i + 1}단계 결과 없음")
                return
            results.append({"text": text})
            main = [vals.get(f["key"]) for f in a["inputs"] if f.get("main")]
            same = any(isinstance(m, str) and m.strip() == text.strip() for m in main)   # 다듬기 단계가 글을 하나도 안 바꿈
            st[i].update(status="done", finished=time.time(), text=text[:OUT_MAX], files=out.get("files") or [], msg="",
                         **({"svg": out["svg"]} if out.get("svg") else {}), **({"mermaid": True} if out.get("mermaid") else {}),
                         **({"note": out["note"]} if out.get("note") else {}), **({"unchanged": True} if same else {}))
            _save_steps(rid, st)
        _save_steps(rid, st, "done")
        _job(agent_user, rid, "done", name, f"{n}단계 모두 끝남", 100)
    except Exception as e:   # 예상 못 한 오류 — 상세는 로그에만
        print("flows: 실행 오류", rid, type(e).__name__, e, flush=True)
        for s in st:
            if s["status"] in ("queued", "running"):
                s["status"] = "failed" if s["status"] == "running" else "skipped"
        _save_steps(rid, st, "failed", "서버 오류로 멈췄습니다")
        _job(agent_user, rid, "failed", name, "서버 오류로 멈춤")
    finally:
        with _LOCK:
            _ACTIVE.pop(uid, None)


def recover():
    """재시작 때: 실행 중으로 남은 기록은 실패로(스레드가 사라졌으므로)"""
    with db() as c:
        c.execute("UPDATE flow_runs SET status='failed', finished=?, error=? WHERE status='running'", (time.time(), "포털이 다시 시작되어 멈췄습니다"))


def community(c):
    """GET /api/flows/stats — 모두의 에이전트 참여 현황(포털 쪽 수만, 개인 내용 없음): 흐름·실행·도구 사용·개발자 등록 도구"""
    import datetime as _dt
    week = time.time() - 7 * 86400
    since = (timeutil.utcnow() - _dt.timedelta(days=7)).isoformat(timespec="seconds")
    one = lambda q, a=(): (c.execute(q, a).fetchone() or [0])[0] or 0
    has = lambda t: bool(c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone())
    return {"flows": one("SELECT COUNT(*) FROM flows"), "flow_makers": one("SELECT COUNT(DISTINCT uid) FROM flows"),
            "flow_runs_7d": one("SELECT COUNT(*) FROM flow_runs WHERE started>=?", (week,)),
            "tool_opens_7d": one("SELECT COUNT(*) FROM events WHERE kind='open' AND ts>=?", (since,)) if has("events") else 0,
            "active_users_7d": one("SELECT COUNT(DISTINCT user) FROM events WHERE ts>=?", (since,)) if has("events") else 0,
            "dev_tools": one("SELECT COUNT(*) FROM dev_submissions WHERE status='approved'") if has("dev_submissions") else 0}


# ── 라우팅(app.py 가 부름) ───────────────────────────────────────────────
def handle_get(ctx, up=None):
    p = ctx.p
    try:
        with db() as c:
            if p == "/api/flows":
                return ctx.send(listing(c, ctx.uid))
            if p == "/api/flows/adapters":
                return ctx.send({"adapters": adapter_list(up)})
            if p == "/api/flows/stats":
                return ctx.send(community(c))
            if p.startswith("/api/flows/runs/"):
                return ctx.send(get_run(c, ctx.uid, p.rsplit("/", 1)[1]))
            return ctx.send(get_flow(c, ctx.uid, p.rsplit("/", 1)[1]))
    except PermissionError as e:
        return ctx.send({"error": str(e), "code": "forbidden"}, code=403)
    except FileNotFoundError as e:
        return ctx.send({"error": str(e), "code": "not_found"}, code=404)


def handle_post(ctx, req):
    p, res = ctx.p, None
    try:
        try:
            with db() as c:
                if p == "/api/flows/save":
                    return ctx.send({"flow": save(c, ctx.uid, req)})
                if p == "/api/flows/delete":
                    return ctx.send(delete(c, ctx.uid, req))
                if p == "/api/flows/run":
                    res, go = start_run(c, ctx.uid, ctx.agent_user, req)
                else:
                    return ctx.send({"error": "not found"}, code=404)
        except BaseException:
            if res:   # 자리는 잡았는데 커밋 실패(DB 잠김 등) — 돌려주지 않으면 재시작 전까지 '이미 실행 중'
                with _LOCK:
                    _ACTIVE.pop(ctx.uid, None)
            raise
        go()   # 커밋한 뒤 실행 시작
        return ctx.send(res)
    except PermissionError as e:
        return ctx.send({"error": str(e), "code": "forbidden"}, code=403)
    except FileNotFoundError as e:
        return ctx.send({"error": str(e), "code": "not_found"}, code=404)
    except (ValueError, KeyError, TypeError) as e:
        return ctx.send({"error": str(e) if not isinstance(e, TypeError) else "요청 값 형식이 틀립니다", "code": "bad_request"}, code=400)
