"""Combined single-port server: dashboard + REST API (FastAPI) and MCP streamable HTTP at /mcp.
Deployment (Render etc.): binds 0.0.0.0:$PORT. Local: SAVEHUB_HOST/SAVEHUB_PORT (default 127.0.0.1:8765).
"""
import contextlib, os
from . import app as webapp, auth, db, seed
from .mcp_server import BearerAuth, mcp, mcp_token

TOKEN = mcp_token()
_mcp_asgi = mcp.streamable_http_app()          # Starlette app whose route is /mcp
MCP_APP = BearerAuth(_mcp_asgi, TOKEN)

def _start_autosync():
    """SAVEHUB_AUTOSYNC=1: run the auto-sync loop in this process if a browser is available. On Render (no browser)
    it stays off and is reported as unavailable; it never takes the server down."""
    if os.environ.get("SAVEHUB_AUTOSYNC", "0").lower() not in ("1", "true", "yes", "on"): return None
    try:
        from .collector import autosync, platforms  # noqa: F401
        r = autosync.start_background("server")
    except Exception as e:                       # pragma: no cover - defensive
        r = {"started": False, "reason": f"{type(e).__name__}: {e}"}
    print("[savehub] auto-sync:", "started" if r.get("started") else f"unavailable ({r.get('reason')})", flush=True)
    return r

@contextlib.asynccontextmanager
async def lifespan(_app):
    from .collector.logutil import install_scrubber
    install_scrubber()                           # scrub tokens/cookies from uvicorn/app log lines
    db.harden_files()
    # fresh deploy: DB file + schema are created on first connect; seed samples if the library is empty
    if os.environ.get("SAVEHUB_SEED", "1") != "0":
        print("[savehub] seed:", seed.seed(), flush=True)
    started = _start_autosync()
    try:
        async with mcp.session_manager.run():       # required: mounted apps don't run their own lifespan
            yield
    finally:
        if started and started.get("started"):
            from .collector import autosync
            autosync.stop_background()

webapp.app.router.lifespan_context = lifespan

@webapp.app.get("/healthz", include_in_schema=False)
def healthz():
    db.conn().execute("SELECT 1")
    commit = os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("SAVEHUB_COMMIT") or ""
    return {"ok": True, **({"commit": commit[:12]} if commit else {})}   # deploy check: which git commit is live

def _basic_ok(scope):
    return auth.basic_ok(dict(scope.get("headers") or []).get(b"authorization", b""))

async def app(scope, receive, send):
    """ASGI dispatcher: /mcp (and /mcp/...) -> MCP (bearer auth), everything else -> FastAPI
    (optionally behind HTTP Basic auth when SAVEHUB_DASHBOARD_PASSWORD is set; /healthz stays open)."""
    if scope["type"] in ("http", "websocket"):
        path = scope.get("path", "")
        # /connect/<token> and its POST /api/connect/<token> are gated by the single-use token itself (no dashboard login)
        open_connect = path.startswith("/connect/") or path.startswith("/api/connect/")
        if auth.login_configured() and path != "/healthz" and not open_connect and not (path == "/mcp" or path.startswith("/mcp/")) and not _basic_ok(scope):
            if scope["type"] == "websocket":   # an HTTP response is not valid on a websocket scope: refuse the handshake
                await send({"type": "websocket.close", "code": 1008}); return
            await send({"type": "http.response.start", "status": 401, "headers": webapp.login_required_headers(scope)})
            await send({"type": "http.response.body", "body": b"login required"}); return
        if path == "/mcp" or path.startswith("/mcp/"):
            if path != "/mcp":
                scope = dict(scope, path="/mcp")
            return await MCP_APP(scope, receive, send)
    return await webapp.app(scope, receive, send)

def main():
    import uvicorn
    deploy = bool(os.environ.get("PORT"))
    host = os.environ.get("SAVEHUB_HOST", "0.0.0.0" if deploy else "127.0.0.1")  # nosec B104 - all interfaces only on a PaaS ($PORT set); local default is loopback
    port = int(os.environ.get("PORT") or os.environ.get("SAVEHUB_PORT", 8765))
    print(f"[savehub] dashboard http://{host}:{port}/  ·  MCP http://{host}:{port}/mcp  ·  db {db.DB_PATH}", flush=True)
    uvicorn.run(app, host=host, port=port, proxy_headers=True, forwarded_allow_ips="*")

if __name__ == "__main__": main()
