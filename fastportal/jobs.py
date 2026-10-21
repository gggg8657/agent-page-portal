"""작업 알림 센터 — 작업 레지스트리(portal.db 의 jobs 표) + 즉시 전달(SSE 구독 허브) + 도구용 내부 경로의 토큰.

  기록 두 갈래
    1) 자동: 1분(PORTAL_JOBS_AUTO_S) 넘게 걸리는 프록시 POST — 그 시간이 지나면 running, 끝나면 done/failed (예전 메모리 EVENTS 를 대체).
       자기 알림(portal_jobs.py)을 쓰는 도구는 자동 기록을 건너뜀(같은 작업이 두 번 뜨지 않게)
    2) 도구가 직접: POST /api/internal/jobs {id?, user, tool, status, title, link, message, progress?}
       헤더 X-Portal-Jobs-Token = HMAC(비밀값, 도구 dir) — 도구마다 다른 토큰이라 한 도구가 다른 도구 이름으로 못 씀.
       비밀값: PORTAL_JOBS_SECRET(환경변수, 컨테이너 여럿이 나눠 쓸 때) 또는 _data/.portal_jobs_secret(처음 기동 때 무작위, 권한 600).
       파일에 두는 까닭: 포털만 재시작해도(start-portal.sh) 이미 떠 있는 도구의 토큰이 그대로 맞게.
       받는 곳: 127.0.0.1/::1, tools.json 의 host 주소(다른 컨테이너 도구), PORTAL_JOBS_ALLOW(쉼표 IP) — 그 밖은 403
  전달: 프로세스 안 asyncio 큐 구독(연결마다 큐 하나, 스레드 없음). 쓰기는 스레드 풀에서 하고 call_soon_threadsafe 로 루프에 넘김.
  보기 규칙: /api/events 와 같음 — 본인 작업만, 주인(owner)은 전부. 읽음은 보는 사람마다(job_reads)."""
import asyncio, hashlib, hmac, json, os, re, secrets, socket, time, urllib.parse, urllib.request

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from . import config, tools
from .db import db

AUTO_S = float(os.environ.get("PORTAL_JOBS_AUTO_S", "60"))
KEEP_DAYS = 30
STATUSES = ("queued", "running", "done", "failed", "canceled")
TERMINAL = ("done", "failed", "canceled")
SECRET_FILE = config.secret_file(".portal_jobs_secret")   # PORTAL_SECRETS_DIR(기본 DATA)
USER_RE = re.compile(r"[A-Za-z0-9_.@-]{1,64}")
ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,80}")


# ── 비밀값·토큰 ──────────────────────────────────────────────────────────
def _secret():
    v = os.environ.get("PORTAL_JOBS_SECRET", "").strip()
    if v:
        return v.encode()
    try:
        fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32) + "\n")
    except FileExistsError:
        pass
    os.chmod(SECRET_FILE, 0o600)
    with open(SECRET_FILE, encoding="utf-8") as f:
        v = f.read().strip()
    if len(v) < 16:
        raise RuntimeError(f"{SECRET_FILE} 가 비었거나 너무 짧음 — 지우고 다시 띄우면 새로 만듦")
    return v.encode()


SECRET = _secret()


def token_for(tool_dir):
    """도구 하나의 토큰(값은 어디에도 출력하지 않음 — 도구 환경변수로만 넘김)"""
    return hmac.new(SECRET, ("portal-jobs:" + tool_dir).encode(), hashlib.sha256).hexdigest()


# 도구 서명 키(포털 → 도구 요청의 X-Agent-Auth, 도구 → 도구 호출도 같은 키) — 모든 도구가 같은 값(서로 부르므로). 도구 portal_auth.py 가 확인
TOOL_KEY = hmac.new(SECRET, b"portal-proxy", hashlib.sha256).hexdigest()


def sign(user, tool_dir, now=None):
    """도구로 가는 요청에 붙일 X-Agent-Auth 값: v2.<초>.HMAC(TOOL_KEY, "v2|<초>|<받는 도구 dir>|<user>") — portal_auth.verify 와 같은 규칙.
    받는 도구 이름이 들어가므로 한 도구 로그에 남은 서명을 다른 도구에 다시 쓸 수 없음"""
    ts = str(int(now if now is not None else time.time()))
    return f"v2.{ts}." + hmac.new(TOOL_KEY.encode(), f"v2|{ts}|{tool_dir}|{user}".encode(), hashlib.sha256).hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


TOOL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())   # 포털이 도구를 직접 부를 때 — 프록시 안 거치고, 리디렉트를 안 따름(서명 헤더가 다른 주소로 새지 않게)


def tool_headers(user, tool_dir):
    """포털이 도구(tool_dir)를 직접 부를 때의 사용자 헤더 + 서명(TOOL_AUTH 일 때 — 도구도 그때만 확인함)"""
    return {"X-Agent-User": user, **({"X-Agent-Auth": sign(user, tool_dir)} if config.TOOL_AUTH else {})}


def tool_env(t, port=None):
    """tools.start 가 도구에 넘기는 환경변수. URL 은 PORTAL_JOBS_URL(컨테이너 등) 또는 이 포털의 127.0.0.1:PORT.
    PORTAL_TOOL_KEY: 도구가 포털 서명만 받게(config.TOOL_AUTH, 기본 켬) — 다른 컨테이너 도구는 `python -m fastportal.jobs toolkey` 값을 env 로"""
    url = os.environ.get("PORTAL_JOBS_URL") or f"http://127.0.0.1:{port or os.environ.get('PORT', '8720')}/api/internal/jobs"
    env = {"PORTAL_JOBS_URL": url, "PORTAL_JOBS_TOKEN": token_for(t["dir"]), "PORTAL_JOBS_TOOL": t["dir"]}
    if config.TOOL_AUTH:
        env["PORTAL_TOOL_KEY"] = TOOL_KEY
    return env


_ALLOW = {"t": 0.0, "v": {}}


def _allowed_ips(tool_dir):
    """이 도구를 받아도 되는 바깥 주소: 그 도구의 tools.json host 주소 + PORTAL_JOBS_ALLOW(모든 도구) — 이름 풀이는 60초 캐시(스레드 풀에서만 부름)"""
    if time.monotonic() - _ALLOW["t"] > 60:
        m = {}
        for t in tools.tools():
            h = tools.tool_host(t) if tools.is_remote(t) else None
            if h:
                try:
                    m[t["dir"]] = {ai[4][0] for ai in socket.getaddrinfo(h, None)}
                except OSError:
                    pass
        _ALLOW.update(t=time.monotonic(), v=m)
    return _ALLOW["v"].get(tool_dir, set()) | {x.strip() for x in os.environ.get("PORTAL_JOBS_ALLOW", "").split(",") if x.strip()}


def internal_ok(client, header, tool_dir):
    """내부 경로 허용: 접속지(로컬, 또는 그 도구의 host 주소·허용 목록) + 그 도구의 토큰"""
    if not header or not tool_dir:
        return False
    if client not in config.LOCAL and client not in _allowed_ips(tool_dir):
        return False
    return hmac.compare_digest(header.strip().encode(), token_for(tool_dir).encode())


# ── DB ───────────────────────────────────────────────────────────────────
def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, user TEXT, tool TEXT, title TEXT, status TEXT, progress INTEGER,
                                    started REAL, finished REAL, link TEXT, message TEXT, seq INTEGER, updated REAL);
    CREATE INDEX IF NOT EXISTS jobs_seq ON jobs(seq);
    CREATE INDEX IF NOT EXISTS jobs_user ON jobs(user, seq);
    CREATE TABLE IF NOT EXISTS job_reads(job TEXT, user TEXT, UNIQUE(job, user));""")


SELF_REPORT = set()   # 직접 알리는 도구(자동 기록 건너뜀) — 기동 때 DB 에서, 이후 내부 경로로 알 때마다


def load_self_report():
    with db() as c:
        SELF_REPORT.update(r[0] for r in c.execute("SELECT DISTINCT tool FROM jobs WHERE id NOT LIKE 'auto:%'"))


def _name(tool_dir):
    t = tools.tool(tool_dir)
    return (t or {}).get("name", tool_dir)


PORTAL_KINDS = {"security": "포털 보안 · 의심 요청 차단", "flows": "모두의 에이전트 · 연결", "devsubmit": "모두의 에이전트 · 개발자 제출", "community": "모두의 에이전트 · 리뷰·요청"}


def public(r, viewer=None, unread=None):
    """화면에 내보내는 모양(link 는 포털 주소 /t/<도구>/…)"""
    d = dict(r)
    link = d.get("link") or "/"
    portal = d["tool"] in PORTAL_KINDS   # 포털 자체 작업(모두의 에이전트 흐름·개발자 제출) — 링크는 포털 화면 주소 그대로
    return {"id": d["id"], "tool": d["tool"], "name": PORTAL_KINDS[d["tool"]] if portal else _name(d["tool"]), "user": d["user"], "title": d["title"] or "", "status": d["status"],
            "progress": d["progress"], "started": d["started"], "finished": d["finished"], "link": link if portal else f"/t/{d['tool']}{link}",
            "message": d["message"] or "", "seq": d["seq"], "unread": bool(unread) if unread is not None else d["status"] in TERMINAL}


def clean_link(tool_dir, link):
    """도구 안 경로만('/' 로 시작, '//'·스킴·제어 문자 없음). /t/<도구>/… 로 오면 앞을 뗌. 틀리면 '/'"""
    link = str(link or "").strip()
    pre = f"/t/{tool_dir}"
    if link.startswith(pre + "/") or link == pre:
        link = link[len(pre):] or "/"
    if link.startswith(("?", "#")):
        link = "/" + link
    if not link.startswith("/") or link.startswith("//") or "\\" in link or re.search(r"[\x00-\x20\x7f]", link):
        return "/"
    return link[:500]


def _seq_sql():
    return "(SELECT MAX(COALESCE(MAX(seq), 0) + 1, ?) FROM jobs)"   # 예전 /api/events id(초 단위 시각)보다 늘 크게 — 탭에 남은 agNotifyLast 와 이어짐


def upsert(user, tool_dir, status, jid=None, title="", link="", message="", progress=None, auto=False):
    """작업 하나를 넣거나 고침 → 바뀐 행(public 모양) 또는 None(무시: 끝난 작업을 다시 진행 중으로 등)"""
    if status not in STATUSES:
        raise ValueError("status 는 queued|running|done|failed|canceled")
    if not USER_RE.fullmatch(str(user or "")):
        raise ValueError("user 형식이 틀립니다")
    if jid is not None and not ID_RE.fullmatch(str(jid)):
        raise ValueError("id 는 영문·숫자·_.:- 80자까지")
    key = f"{'auto:' + tool_dir if auto else tool_dir}:{jid or secrets.token_hex(6)}"
    now = time.time()
    title, message = str(title or "")[:200], str(message or "")[:500]
    link = clean_link(tool_dir, link) if link else ""
    prog = None if progress is None else max(0, min(100, int(progress)))
    with db() as c:
        old = c.execute("SELECT * FROM jobs WHERE id=?", (key,)).fetchone()
        if old:
            if old["user"] != user or old["tool"] != tool_dir:
                raise PermissionError("다른 사용자·도구의 작업입니다")
            if old["status"] in TERMINAL and status not in TERMINAL:
                return None
            if old["status"] == status and status in TERMINAL:
                return None   # 같은 끝 알림이 두 번 — 다시 띄우지 않음
            c.execute(f"UPDATE jobs SET status=?, title=?, link=?, message=?, progress=?, finished=?, updated=?, seq={_seq_sql()} WHERE id=?",
                      (status, title or old["title"], link or old["link"], message or old["message"],
                       prog if prog is not None else (100 if status == "done" else old["progress"]),
                       now if status in TERMINAL else None, now, int(now), key))
            c.execute("DELETE FROM job_reads WHERE job=?", (key,))   # 다시 바뀌면 다시 안 읽음
        else:
            c.execute(f"INSERT INTO jobs(id, user, tool, title, status, progress, started, finished, link, message, seq, updated) "
                      f"VALUES(?,?,?,?,?,?,?,?,?,?,{_seq_sql()},?)",
                      (key, user, tool_dir, title, status, prog if prog is not None else (100 if status == "done" else None), now,
                       now if status in TERMINAL else None, link or "/", message, int(now), now))
            if secrets.randbelow(50) == 0:   # 가끔 오래된 것 정리
                cut = now - KEEP_DAYS * 86400
                c.execute("DELETE FROM job_reads WHERE job IN (SELECT id FROM jobs WHERE updated<?)", (cut,))
                c.execute("DELETE FROM jobs WHERE updated<?", (cut,))
        r = c.execute("SELECT * FROM jobs WHERE id=?", (key,)).fetchone()
    if not auto:
        SELF_REPORT.add(tool_dir)
    return public(r)


def _vis(viewer):
    return ("1=1", ()) if viewer == "owner" else ("user=?", (viewer,))


def listing(viewer, unread_only=False, limit=50, since=0):
    w, a = _vis(viewer)
    with db() as c:
        rows = c.execute(f"SELECT j.*, (j.status IN ('done','failed','canceled') AND NOT EXISTS(SELECT 1 FROM job_reads r WHERE r.job=j.id AND r.user=?)) AS ur "
                         f"FROM jobs j WHERE {w} AND seq>? ORDER BY seq DESC LIMIT ?", (viewer, *a, since, max(1, min(200, limit)))).fetchall()
        n = c.execute(f"SELECT COUNT(*) FROM jobs j WHERE {w} AND status IN ('done','failed','canceled') "
                      f"AND NOT EXISTS(SELECT 1 FROM job_reads r WHERE r.job=j.id AND r.user=?)", (*a, viewer)).fetchone()[0]
        last = c.execute(f"SELECT COALESCE(MAX(seq), 0) FROM jobs WHERE {w}", a).fetchone()[0]
    jobs = [public(r, unread=r["ur"]) for r in rows]
    if unread_only:
        jobs = [j for j in jobs if j["unread"] or j["status"] not in TERMINAL]   # 안 읽은 끝난 작업 + 아직 도는 작업
    return {"jobs": jobs, "unread": n, "last": last}


def mark_read(viewer, ids=None):
    w, a = _vis(viewer)
    with db() as c:
        if ids is None:
            q, qa = f"SELECT id FROM jobs WHERE {w} AND status IN ('done','failed','canceled')", a
        else:
            ids = [str(i) for i in ids][:500]
            q, qa = f"SELECT id FROM jobs WHERE {w} AND status IN ('done','failed','canceled') AND id IN ({','.join('?' * len(ids))})", (*a, *ids)
        got = [r[0] for r in c.execute(q, qa)] if ids is None or ids else []
        c.executemany("INSERT OR IGNORE INTO job_reads(job, user) VALUES(?,?)", [(j, viewer) for j in got])
    return got


def legacy_events(viewer, since):
    """예전 /api/events 모양 — 끝난(done·failed) 작업 {id(seq), dir, name, user, ok, sec, t}"""
    w, a = _vis(viewer)
    with db() as c:
        rows = c.execute(f"SELECT * FROM jobs WHERE {w} AND status IN ('done','failed') AND seq>? ORDER BY seq DESC LIMIT 30", (*a, since)).fetchall()
        last = c.execute("SELECT COALESCE(MAX(seq), 0) FROM jobs").fetchone()[0]
    ev = [{"id": r["seq"], "dir": r["tool"], "name": _name(r["tool"]), "user": r["user"], "ok": r["status"] == "done",
           "sec": round((r["finished"] or r["updated"]) - (r["started"] or r["updated"])), "t": int(r["finished"] or r["updated"]),
           "title": r["title"] or "", "link": public(r)["link"]} for r in reversed(rows)]
    return {"events": ev, "last": max(last, since)}


# ── 구독 허브(SSE) ────────────────────────────────────────────────────────
class Hub:
    def __init__(self):
        self.loop = None
        self.subs = set()

    def subscribe(self, viewer):
        q = asyncio.Queue(maxsize=200)
        q.viewer, q.dead = viewer, False
        self.subs.add(q)
        return q

    def unsubscribe(self, q):
        self.subs.discard(q)

    def _dispatch(self, kind, payload, owner, exact=False):
        for q in list(self.subs):
            if q.viewer == owner or (q.viewer == "owner" and not exact):
                try:
                    q.put_nowait((kind, payload))
                except asyncio.QueueFull:   # 너무 느린 연결 — 끊음(브라우저가 다시 붙어 since 로 빠진 것을 받음)
                    q.dead = True
                    self.subs.discard(q)

    def publish(self, kind, payload, owner, exact=False):
        """어느 스레드에서든 불러도 됨. exact=True 면 그 사람에게만(읽음 표시 — 주인 화면엔 안 감)"""
        if not self.loop:
            return
        try:
            if asyncio.get_running_loop() is self.loop:
                return self._dispatch(kind, payload, owner, exact)
        except RuntimeError:
            pass
        try:
            self.loop.call_soon_threadsafe(self._dispatch, kind, payload, owner, exact)
        except RuntimeError:   # 루프가 닫힘(종료 중)
            pass


HUB = Hub()


def sse(kind, data, eid=None):
    return ((f"id: {eid}\n" if eid is not None else "") + f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n").encode()


# ── HTTP: 도구 → 포털(내부 경로) · 화면 ← 포털(SSE). ctx 는 app 이 넘김(ctx.Ctx) ─────────
def internal_post(ctx, body):
    """POST /api/internal/jobs — 쿠키 없음, 접속지 + 도구 토큰(internal_ok)"""
    from .ctx import parse_json
    try:
        req = parse_json(body)
    except ValueError:
        return ctx.send({"error": "요청 본문은 JSON 객체여야 합니다"}, code=400)
    d = str(req.get("tool") or "")
    if not tools.tool(d) or not internal_ok(ctx.client, ctx.headers.get("x-portal-jobs-token"), d):   # 없는 도구도 같은 403(목록을 알려 주지 않게)
        return ctx.send({"error": "forbidden"}, code=403)
    try:
        j = upsert(req.get("user"), d, req.get("status"), req.get("id"), req.get("title"), req.get("link"), req.get("message"), req.get("progress"))
    except PermissionError as e:
        return ctx.send({"error": str(e)}, code=403)
    except (ValueError, TypeError) as e:
        return ctx.send({"error": str(e) if isinstance(e, ValueError) else "요청 값 형식이 틀립니다"}, code=400)
    if j:
        HUB.publish("job", j, j["user"])
    return ctx.send({"ok": True, "id": j["id"] if j else None, "ignored": j is None})


class JobStream(Response):
    """GET /api/jobs/stream — text/event-stream. 연결마다 asyncio 큐 하나(스레드 없음). 처음에 hello{last, unread},
    since(?since= 또는 Last-Event-ID) 뒤에 바뀐 작업을 다시 보내고, 그다음 바뀔 때마다 event: job / event: read. 빈 줄 주석으로 연결 유지"""

    def __init__(self, ctx, since):
        self.status_code, self.background, self.ctx, self.since = 200, None, ctx, since
        from .ctx import hval   # 늦게: python -m fastportal.jobs token 은 화면 모듈을 안 읽게
        from .pages import _SIG_A
        hd = [("X-Author", hval(_SIG_A)), ("Content-Type", "text/event-stream; charset=utf-8"), ("Cache-Control", "no-cache"),
              ("X-Accel-Buffering", "no"), ("X-Content-Type-Options", "nosniff")]
        if ctx.setcookie:
            hd.append(("Set-Cookie", ctx.setcookie))
        self.raw_headers = [(k.lower().encode("latin-1"), v if isinstance(v, bytes) else v.encode("latin-1")) for k, v in hd]

    async def __call__(self, scope, receive, send):
        viewer = self.ctx.agent_user
        q = HUB.subscribe(viewer)   # 다시 보내기 전에 구독 — 그 사이 바뀐 것도 놓치지 않음(화면은 seq·id 로 중복을 걸러냄)
        try:
            async with anyio.create_task_group() as tg:
                async def pump():
                    try:
                        await send({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
                        head = await run_in_threadpool(listing, viewer, False, 100, self.since)
                        buf = b"retry: 3000\n\n" + sse("hello", {"last": head["last"], "unread": head["unread"]})
                        if self.since:
                            buf += b"".join(sse("job", j, j["seq"]) for j in reversed(head["jobs"]))
                        await send({"type": "http.response.body", "body": buf, "more_body": True})
                        while not q.dead:
                            try:
                                kind, data = await asyncio.wait_for(q.get(), config.JOBS_KEEPALIVE)
                                chunk = sse(kind, data, data.get("seq") if kind == "job" else None)
                            except asyncio.TimeoutError:
                                chunk = b": ping\n\n"
                            await send({"type": "http.response.body", "body": chunk, "more_body": True})
                    except OSError:
                        pass
                    finally:
                        tg.cancel_scope.cancel()

                async def watch():
                    while (await receive())["type"] != "http.disconnect":
                        pass
                    tg.cancel_scope.cancel()

                tg.start_soon(pump)
                tg.start_soon(watch)
        finally:
            HUB.unsubscribe(q)


def stream(ctx):
    """GET /api/jobs/stream 응답"""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query)
    try:
        since = int(ctx.headers.get("last-event-id") or q.get("since", ["0"])[0] or 0)
    except ValueError:
        since = 0
    return JobStream(ctx, since)


if __name__ == "__main__":   # 다른 컨테이너 도구에 넣을 토큰 만들기(관리자만 — 출력값은 그 컨테이너 환경변수로만, 커밋·로그 금지)
    import sys
    if len(sys.argv) == 2 and sys.argv[1] == "toolkey":   # 도구 서명 키(PORTAL_TOOL_KEY) — 다른 컨테이너 도구 env 로
        print(TOOL_KEY)
        raise SystemExit
    if len(sys.argv) != 3 or sys.argv[1] != "token" or not tools.tool(sys.argv[2]):
        raise SystemExit("python -m fastportal.jobs token <tools.json 의 dir>  |  python -m fastportal.jobs toolkey")
    print(token_for(sys.argv[2]))
