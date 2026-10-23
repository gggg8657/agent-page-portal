"""격리 도구 컨테이너의 시작 프로그램(표준 라이브러리만) — 이미지 dongjukim-devcheck 의 /opt/devtool/entry.py. 포털 프로세스에서는 import 하지 않음.

승인된 업로드 도구(tools.json "runtime": "container")는 내부 전용 망(인터넷·호스트·다른 컨테이너로 가는 길 없음)에서 돈다.
  1) LLM·임베딩 중계: 도구가 127.0.0.1:11440(LLM, OpenAI 호환)·127.0.0.1:11436(임베딩, Ollama)에 붙으면, 포털이 :19742 로
     미리 열어 둔 역방향 연결(첫 줄 'llm' 또는 'emb')과 짝지어 그대로 잇는다. 포털 쪽은 받은 연결을 정해진 곳 한 곳에만 잇는다
     (fastportal/devtool.py — 목적지를 고르는 기능 없음). 이 컨테이너는 바깥으로 연결을 만들지 않는다.
  2) /app 에서 python app.py 를 띄우고, 끝나면 같은 종료 코드로 끝남(다시 띄우기는 docker --restart). SIGTERM 은 앱에 넘김.
환경변수: RELAY_WAIT(빈 역방향 연결을 기다리는 초, 기본 120)"""
import asyncio, os, signal, subprocess, sys, threading

TUNNEL_PORT = 19742
KINDS = {b"llm": 11440, b"emb": 11436}
WAIT = float(os.environ.get("RELAY_WAIT") or 120)
IDLE = {k: None for k in KINDS}   # 종류 → asyncio.Queue(루프 안에서 만듦)


async def pump(r, w):
    try:
        while True:
            b = await r.read(65536)
            if not b:
                break
            w.write(b)
            await w.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def tunnel(r, w):
    """포털의 역방향 연결: 첫 줄(종류)이 맞으면 그 종류의 빈 연결 목록에 넣음"""
    try:
        kind = (await asyncio.wait_for(r.readline(), 10)).strip()
    except (asyncio.TimeoutError, ConnectionError):
        w.close(); return
    if kind not in IDLE:
        w.close(); return
    await IDLE[kind].put((r, w))


def client(kind):
    async def handle(r, w):
        """도구의 LLM·임베딩 연결: 살아 있는 빈 역방향 연결과 짝지음(없으면 RELAY_WAIT 초까지 기다림)"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + WAIT
        while True:
            left = deadline - loop.time()
            if left <= 0:
                w.close(); return
            try:
                tr, tw = await asyncio.wait_for(IDLE[kind].get(), left)
            except asyncio.TimeoutError:
                w.close(); return
            if tr.at_eof() or tw.is_closing():
                tw.close(); continue
            break
        await asyncio.gather(pump(r, tw), pump(tr, w))
    return handle


async def relay():
    for k in IDLE:
        IDLE[k] = asyncio.Queue()
    servers = [await asyncio.start_server(tunnel, "0.0.0.0", TUNNEL_PORT)]
    servers += [await asyncio.start_server(client(k), "127.0.0.1", p) for k, p in KINDS.items()]
    await asyncio.gather(*(s.serve_forever() for s in servers))


def main():
    threading.Thread(target=lambda: asyncio.run(relay()), daemon=True, name="relay").start()
    if not os.path.exists("/app/app.py"):
        sys.exit("app.py 가 없습니다")
    p = subprocess.Popen([sys.executable, "app.py"], cwd="/app")
    signal.signal(signal.SIGTERM, lambda *_: p.terminate())
    signal.signal(signal.SIGINT, lambda *_: p.terminate())
    sys.exit(p.wait())


if __name__ == "__main__":
    main()
