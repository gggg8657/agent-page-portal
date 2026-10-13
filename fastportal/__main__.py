"""python -m fastportal — uvicorn 1워커로 띄움(README 'FastAPI 포털' 참고). TLS_CERT·TLS_KEY 가 있으면 https(TLS 1.2 이상)"""
import os, ssl

import uvicorn

if __name__ == "__main__":
    tls = bool(os.environ.get("TLS_CERT") and os.environ.get("TLS_KEY"))
    cfg = uvicorn.Config("fastportal.app:app", host=os.environ.get("HOST") or "0.0.0.0", port=int(os.environ.get("PORT", "8720")),
                         workers=1, proxy_headers=False, server_header=False, date_header=True, access_log=False,
                         timeout_keep_alive=int(os.environ.get("KEEPALIVE_S", "75")), log_level="info",
                         ssl_certfile=os.environ.get("TLS_CERT") if tls else None, ssl_keyfile=os.environ.get("TLS_KEY") if tls else None)
    cfg.load()
    if cfg.ssl:
        cfg.ssl.minimum_version = ssl.TLSVersion.TLSv1_2
    uvicorn.Server(cfg).run()
