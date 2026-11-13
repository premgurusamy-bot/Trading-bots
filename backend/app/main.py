"""FastAPI entry point: `python -m app.main` (or uvicorn app.main:app)."""
from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, routes
from app.config import settings, BACKEND_DIR, APP_VERSION


class _RedactFilter(logging.Filter):
    """Belt-and-braces: never let a token-like header value reach the logs."""
    def filter(self, record):
        msg = str(record.getMessage())
        if "access-token" in msg.lower() or "token=" in msg:
            record.msg = "[redacted log line containing credentials]"
            record.args = ()
        return True


def setup_logging():
    logdir = Path(settings.data_dir) / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(logdir / "app.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fmt = logging.Formatter('{"t":"%(asctime)s","lvl":"%(levelname)s","src":"%(name)s","msg":%(message)r}')
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    root.handlers = [fh, sh]
    for h in root.handlers:
        h.addFilter(_RedactFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx INFO lines include URLs


def create_app(database_url: str | None = None, start_feed: bool = True, start_loop: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from app.runtime import Runtime
        rt = Runtime(database_url, start_feed=start_feed)
        routes.RT = rt
        app.state.rt = rt
        if start_loop:
            rt.start()
        yield
        rt.stop()

    app = FastAPI(title="NIFTY/BANKNIFTY Options Trader", version=APP_VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.include_router(routes.router)

    @app.middleware("http")
    async def security_headers(request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = resp.headers.get("Cache-Control", "no-store") if request.url.path.startswith("/api") else resp.headers.get("Cache-Control", "")
        return resp

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        tok = websocket.query_params.get("token")
        if not auth.valid(tok):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        rt = websocket.app.state.rt
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        loop = asyncio.get_running_loop()

        def listener(ev):
            try:
                loop.call_soon_threadsafe(q.put_nowait, ev)
            except Exception:
                pass

        rt.svc.listeners.append(listener)
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=10)
                    await websocket.send_text(json.dumps(ev, default=str))
                except asyncio.TimeoutError:
                    await websocket.send_text(json.dumps({"kind": "heartbeat"}))
                if not auth.valid(tok):
                    await websocket.close(code=4401)
                    break
        except WebSocketDisconnect:
            pass
        finally:
            if listener in rt.svc.listeners:
                rt.svc.listeners.remove(listener)

    dist = BACKEND_DIR.parent / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            f = dist / path
            if path and f.is_file() and dist in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")
    return app


app = None
if os.environ.get("APP_FACTORY") != "0":
    app = create_app()


def main():
    setup_logging()
    import uvicorn
    if settings.app_host not in ("127.0.0.1", "localhost"):
        logging.getLogger("main").warning("Listening on %s — dashboard login is required for every API call. "
                                          "Do NOT port-forward this to the internet; use a VPN/Tailscale.", settings.app_host)
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
