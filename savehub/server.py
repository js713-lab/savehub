"""Combined single-port server: dashboard + REST API (FastAPI) and MCP streamable HTTP at /mcp.
Deployment (Render etc.): binds 0.0.0.0:$PORT. Local: SAVEHUB_HOST/SAVEHUB_PORT (default 127.0.0.1:8765).
"""
import contextlib, os
from . import app as webapp, db, seed
from .mcp_server import BearerAuth, mcp, mcp_token

TOKEN = mcp_token()
_mcp_asgi = mcp.streamable_http_app()          # Starlette app whose route is /mcp
MCP_APP = BearerAuth(_mcp_asgi, TOKEN)

@contextlib.asynccontextmanager
async def lifespan(_app):
    # fresh deploy: DB file + schema are created on first connect; seed samples if the library is empty
    if os.environ.get("SAVEHUB_SEED", "1") != "0":
        print("[savehub] seed:", seed.seed(), flush=True)
    async with mcp.session_manager.run():       # required: mounted apps don't run their own lifespan
        yield

webapp.app.router.lifespan_context = lifespan

@webapp.app.get("/healthz", include_in_schema=False)
def healthz():
    db.conn().execute("SELECT 1")
    return {"ok": True}

DASH_USER = os.environ.get("SAVEHUB_DASHBOARD_USER", "savehub")
DASH_PASS = os.environ.get("SAVEHUB_DASHBOARD_PASSWORD", "")

def _basic_ok(scope):
    import base64, hmac
    h = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
    if not h.lower().startswith("basic "): return False
    try: u, _, pw = base64.b64decode(h[6:]).decode().partition(":")
    except Exception: return False
    return hmac.compare_digest(u, DASH_USER) and hmac.compare_digest(pw, DASH_PASS)

async def app(scope, receive, send):
    """ASGI dispatcher: /mcp (and /mcp/...) -> MCP (bearer auth), everything else -> FastAPI
    (optionally behind HTTP Basic auth when SAVEHUB_DASHBOARD_PASSWORD is set; /healthz stays open)."""
    if scope["type"] in ("http", "websocket"):
        path = scope.get("path", "")
        if DASH_PASS and path != "/healthz" and not (path == "/mcp" or path.startswith("/mcp/")) and not _basic_ok(scope):
            await send({"type": "http.response.start", "status": 401, "headers": [(b"www-authenticate", b'Basic realm="SaveHub"'), (b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"login required"}); return
        if path == "/mcp" or path.startswith("/mcp/"):
            if path != "/mcp":
                scope = dict(scope, path="/mcp")
            return await MCP_APP(scope, receive, send)
    return await webapp.app(scope, receive, send)

def main():
    import uvicorn
    deploy = bool(os.environ.get("PORT"))
    host = os.environ.get("SAVEHUB_HOST", "0.0.0.0" if deploy else "127.0.0.1")
    port = int(os.environ.get("PORT") or os.environ.get("SAVEHUB_PORT", 8765))
    print(f"[savehub] dashboard http://{host}:{port}/  ·  MCP http://{host}:{port}/mcp  ·  db {db.DB_PATH}", flush=True)
    uvicorn.run(app, host=host, port=port, proxy_headers=True, forwarded_allow_ips="*")

if __name__ == "__main__": main()
