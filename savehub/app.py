"""SaveHub web app + REST API.

Security model (see SECURITY.md): HTTP Basic dashboard login when SAVEHUB_DASHBOARD_PASSWORD is set (enforced by the
ASGI dispatcher in server.py, by main() here, and again inside sensitive endpoints); same-origin check on every
state-changing /api request; security headers (CSP, X-Frame-Options DENY, nosniff, Referrer-Policy, ...) on every
response; no cookies are set at all (Basic auth only, so there is no session cookie to steal or forge).
"""
import ipaddress, os, re, time
from typing import Optional
from urllib.parse import urlsplit
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from . import auth, db, importers, summary, sync, tagger, vault, xoauth

app = FastAPI(title="SaveHub", version="1.0")
STATIC = db.ROOT / "static"

# ---- security headers + same-origin (CSRF) guard ----
# 'unsafe-inline' for scripts is still needed by the dashboard's inline on* handlers; everything else is locked to
# 'self' (no third-party scripts, no framing, no plugins, fetch/XHR only to this origin).
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
       "media-src 'self' https:; connect-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")
SEC_HEADERS = {"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
               "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
               "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Resource-Policy": "same-origin"}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

def same_origin(req) -> bool:
    """Browsers always send Origin (and Sec-Fetch-Site) on cross-site state-changing requests; non-browser clients
    (CLI collector, curl, tests) send neither and are governed by the login instead."""
    sfs = (req.headers.get("sec-fetch-site") or "").lower()
    if sfs and sfs not in ("same-origin", "none"): return False
    origin = req.headers.get("origin")
    if origin is None: return True
    if origin == "null": return False
    host = (req.headers.get("x-forwarded-host") or req.headers.get("host") or "").split(",")[0].strip().lower()
    return urlsplit(origin).netloc.lower() == host and bool(host)

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
    if request.method in UNSAFE_METHODS and path.startswith("/api/") and not same_origin(request):
        resp = JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
    else:
        resp = await call_next(request)
    for k, v in SEC_HEADERS.items(): resp.headers.setdefault(k, v)
    if not path.startswith(("/docs", "/redoc")): resp.headers.setdefault("Content-Security-Policy", CSP)
    if path.startswith("/api/"): resp.headers.setdefault("Cache-Control", "no-store")
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0].strip()
    if proto == "https": resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp

def _csv(v): return [x for x in (v or "").split(",") if x]

@app.get("/", response_class=HTMLResponse)
def index(): return FileResponse(STATIC / "index.html")

app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.get("/api/health")
def health(): return {"ok": True, "stats": db.stats(), "ai": tagger.provider_status()}

@app.get("/api/platforms")
def platforms():
    s = db.get_settings(); st = db.stats()["by_platform"]
    return [{"id": k, **v, "enabled": k in s["enabled_platforms"], "count": st.get(k, 0)} for k, v in db.PLATFORMS.items()]

# ---- items ----
@app.get("/api/items")
def items(q: str = "", platform: str = "", tag: str = "", account_id: str = "", sort: str = "saved_at", order: str = "desc",
          limit: int = 50, offset: int = 0, enabled_only: bool = False):
    en = db.get_settings()["enabled_platforms"] if enabled_only else None
    return db.list_items(q, _csv(platform), _csv(tag), account_id or None, sort, order, limit, offset, en)

@app.get("/api/items/{item_id}")
def item(item_id: int):
    it = db.get_item(item_id)
    if not it: raise HTTPException(404)
    return it

@app.post("/api/items")
async def add_item(req: Request):
    d = await req.json()
    if not d.get("url") and not d.get("content"): raise HTTPException(400, "url or content required")
    if d.get("url") and not d.get("platform"): d["platform"] = importers.detect_platform(d["url"])
    d.setdefault("title", d.get("url"))
    # browser collectors tag their pushes as "collector:<platform>"; anything else is a manual add
    src = str(d.get("source") or "")
    d["source"] = src if re.fullmatch(r"collector:[a-z0-9_]{1,32}(?::[a-z0-9][a-z0-9._-]{0,63})?", src) else "manual"
    iid, created = db.add_item(d)
    return {"id": iid, "created": created, "item": db.get_item(iid)}

@app.patch("/api/items/{item_id}")
async def patch_item(item_id: int, req: Request):
    db.update_item(item_id, await req.json()); return db.get_item(item_id)

@app.delete("/api/items/{item_id}")
def del_item(item_id: int): db.delete_item(item_id); return {"ok": True}

@app.post("/api/tag")
async def tag(req: Request):
    d = await req.json() if (await req.body()) else {}
    return {"retagged": tagger.retag(d.get("ids"), force=d.get("force", False)), "provider": tagger.provider_status()}

@app.get("/api/tags")
def tags(): return db.all_tags()

@app.get("/api/summary")
def get_summary(days: int = 7, ai: bool = True): return summary.get_summary(days, ai)

@app.post("/api/samples/clear")
def clear(): return db.clear_samples()

@app.post("/api/samples/seed")
def seed_samples():
    from . import seed; return seed.seed(force=True)

# ---- import ----
@app.post("/api/import")
async def do_import(kind: str = Form("auto"), platform: str = Form(""), account_id: str = Form(""),
                    file: Optional[UploadFile] = File(None), text: str = Form("")):
    data = await file.read() if file else text
    if not data: raise HTTPException(400, "file or text required")
    try:
        return importers.import_auto(kind, data, platform or None, int(account_id) if account_id else None, file.filename if file else "")
    except Exception as e:
        raise HTTPException(400, f"Import failed: {e}")

# ---- contacts ----
@app.get("/api/contacts")
def contacts(q: str = "", account_id: str = "", platform: str = "", sort: str = "name", order: str = "asc", limit: int = 200, offset: int = 0):
    return db.search_contacts(q, account_id or None, platform or None, sort, order, limit, offset)

@app.post("/api/contacts")
async def add_contact(req: Request):
    cid, c = db.add_contact(await req.json()); return {"id": cid, "created": c}

@app.delete("/api/contacts/{cid}")
def del_contact(cid: int): db.delete_contact(cid); return {"ok": True}

# ---- accounts ----
@app.get("/api/accounts")
def accounts(q: str = "", platform: str = "", status: str = "", sort: str = "platform", order: str = "asc", limit: int = 500, offset: int = 0):
    r = db.list_accounts(q, _csv(platform) or None, status or None, sort, order, limit, offset)
    sec = vault.ids_with_secret()   # only a boolean + timestamp; never the ciphertext or plaintext
    for a in r["accounts"]: a["has_password"] = a["id"] in sec; a["password_updated_at"] = sec.get(a["id"])
    return r

@app.post("/api/accounts")
async def add_account(req: Request):
    d = await req.json()
    if any("password" in k.lower() or "secret" in k.lower() for k in d): raise HTTPException(400, "SaveHub does not store passwords/secrets. Use a password manager.")
    aid, c = db.add_account(d); return {"id": aid, "created": c, "account": db.get_account(aid)}

@app.post("/api/accounts/bulk")
async def bulk_accounts(req: Request):
    """Bulk-add from pasted lines ('@foo', 'instagram @foo', 'ig @foo Display name', profile URLs). Never stores passwords."""
    d = await req.json()
    if any("password" in k.lower() or "secret" in k.lower() for k in d): raise HTTPException(400, "SaveHub does not store passwords/secrets. Use a password manager.")
    p = d.get("platform") or ""
    if p and p not in db.PLATFORMS: raise HTTPException(400, f"Unknown platform: {p}")
    if not (d.get("text") or "").strip(): raise HTTPException(400, "text required (one handle per line)")
    tags = d.get("tags") or []
    if isinstance(tags, str): tags = [t.strip() for t in tags.split(",") if t.strip()]
    return importers.bulk_add_accounts(d["text"], p, d.get("status") or "active", tags)

@app.post("/api/accounts/preview")
async def preview_accounts(req: Request):
    d = await req.json()
    parsed, errors = importers.parse_handle_lines(d.get("text") or "", d.get("platform") or "")
    return {"accounts": parsed, "errors": errors}

@app.get("/api/accounts/stats")
def account_stats():
    c = db.conn()
    return {"total": c.execute("SELECT count(*) FROM accounts").fetchone()[0],
            "by_platform": {r[0]: r[1] for r in c.execute("SELECT platform, count(*) FROM accounts GROUP BY platform")},
            "by_status": {(r[0] or ""): r[1] for r in c.execute("SELECT status, count(*) FROM accounts GROUP BY status")}}

@app.put("/api/accounts/{aid}")
async def put_account(aid: int, req: Request):
    d = await req.json()
    if any("password" in k.lower() or "secret" in k.lower() for k in d): raise HTTPException(400, "Passwords go through the encrypted vault: PUT /api/accounts/{id}/secret")
    db.update_account(aid, d); return db.get_account(aid)

# ---- encrypted password vault (dashboard only; never exposed via MCP) ----
NO_STORE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}

def _vault_guard(req: Request):
    """Secrets endpoints: require a logged-in dashboard user (HTTP Basic verified here, not only by the dispatcher),
    a JSON request (forces a CORS preflight cross-site) and a same-origin Origin header when one is sent."""
    if not auth.login_configured(): raise HTTPException(503, "Password vault is off: dashboard login is not configured (SAVEHUB_DASHBOARD_PASSWORD).")
    if not auth.basic_ok(req.headers.get("authorization", "")): raise HTTPException(401, "login required", headers={"WWW-Authenticate": 'Basic realm="SaveHub"'})
    if "application/json" not in req.headers.get("content-type", ""): raise HTTPException(415, "application/json required")
    origin = req.headers.get("origin")
    if origin and origin.split("://", 1)[-1].rstrip("/") != req.headers.get("host", ""): raise HTTPException(403, "cross-origin request refused")

def _vault_call(fn, *a):
    try: return JSONResponse(fn(*a), headers=NO_STORE)
    except vault.VaultError as e: raise HTTPException(e.status, str(e))

@app.get("/api/vault/status")
def vault_status(): return vault.status()

@app.put("/api/accounts/{aid}/secret")
async def put_secret(aid: int, req: Request):
    _vault_guard(req); d = await req.json()
    return _vault_call(vault.set_secret, aid, d.get("password"))

@app.post("/api/accounts/{aid}/secret/reveal")
async def reveal_secret(aid: int, req: Request):
    _vault_guard(req); return _vault_call(vault.reveal, aid)

@app.delete("/api/accounts/{aid}/secret")
async def delete_secret(aid: int, req: Request):
    _vault_guard(req); return _vault_call(vault.delete_secret, aid)

@app.delete("/api/accounts/{aid}")
def del_account(aid: int): db.delete_account(aid); return {"ok": True}

@app.get("/api/accounts/export.csv", response_class=PlainTextResponse)
def export_accounts():
    return PlainTextResponse(importers.export_accounts_csv(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=savehub_accounts.csv"})

# ---- auto-sync (browser collector, incremental, never un-saves) ----
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

def _is_loopback(h: str) -> bool:
    try: return ipaddress.ip_address((h or "").strip("[]")).is_loopback
    except ValueError: return False

def _host_only(h: str) -> str:
    h = h.strip().lower()
    if h.startswith("["): return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h

def _admin_guard(req: Request, write: bool = True):
    """Auto-sync endpoints: with a dashboard password -> HTTP Basic verified here (not only by the dispatcher);
    without one -> only from this machine (loopback client AND a loopback Host header, which also defeats DNS
    rebinding and makes them unusable on a password-less public deployment). Writes must be JSON + same-origin."""
    if auth.login_configured():
        if not auth.basic_ok(req.headers.get("authorization", "")): raise HTTPException(401, "login required", headers={"WWW-Authenticate": 'Basic realm="SaveHub"'})
    else:
        if not (_is_loopback(req.client.host if req.client else "") and _host_only(req.headers.get("host") or "") in _LOOPBACK_HOSTS):
            raise HTTPException(403, "auto-sync can only be managed from this machine unless a dashboard login is configured (SAVEHUB_DASHBOARD_PASSWORD)")
    if write:
        if "application/json" not in req.headers.get("content-type", ""): raise HTTPException(415, "application/json required")
        if not same_origin(req): raise HTTPException(403, "cross-origin request refused")

def _autosync():
    from .collector import autosync, platforms  # noqa: F401  (registers collectors; no Playwright import)
    return autosync

@app.get("/api/autosync")
def autosync_status(req: Request):
    _admin_guard(req, write=False)
    return _autosync().status()

@app.put("/api/autosync")
async def autosync_config(req: Request):
    _admin_guard(req)
    d = await req.json()
    if not isinstance(d, dict) or set(d) - {"enabled", "interval_minutes"}: raise HTTPException(400, "only 'enabled' and 'interval_minutes' can be set")
    try: _autosync().set_config(enabled=d.get("enabled"), interval_minutes=d.get("interval_minutes"))
    except ValueError as e: raise HTTPException(400, str(e))
    return _autosync().status()

@app.post("/api/autosync/run")
async def autosync_run(req: Request):
    """'Sync now' for one account ({platform, account}) or everything ({}). Rate-limited; un-save is impossible here."""
    _admin_guard(req)
    d = await req.json() if (await req.body()) else {}
    if not isinstance(d, dict) or set(d) - {"platform", "account"}: raise HTTPException(400, "only 'platform' and 'account' are accepted (un-save is never available here)")
    a = _autosync()
    try: r = a.request_sync(d.get("platform") or None, d.get("account") or None)
    except ValueError as e: raise HTTPException(400, str(e))
    except a.Unavailable as e: raise HTTPException(503, f"auto-sync unavailable on this server: {e}")
    except a.RateLimited as e: raise HTTPException(429, str(e), headers={"Retry-After": str(e.retry_after)})
    return JSONResponse(r, status_code=202)

# ---- settings ----
def _redact(s):
    s = dict(s); s.pop("x_tokens", None)
    for k, f in (("notion", "token"), ("x", "client_secret")):
        if s.get(k, {}).get(f): s[k] = {**s[k], f: "••••" + s[k][f][-4:]}
    return s

@app.get("/api/settings")
def settings(): return {"settings": _redact(db.get_settings()), "ai": tagger.provider_status(), "x": xoauth.status()}

@app.put("/api/settings")
async def put_settings(req: Request):
    d = await req.json(); cur = db.get_settings()
    for k, v in d.items():
        if k not in db.DEFAULT_SETTINGS: continue
        if isinstance(v, dict):
            v = {kk: vv for kk, vv in v.items() if not (isinstance(vv, str) and vv.startswith("••••"))}  # keep masked secrets
            v = {**cur.get(k, {}), **v}
        db.set_setting(k, v)
    return settings()

@app.post("/api/sync")
def do_sync(target: str = "all"): return sync.sync_now(target)

# ---- X OAuth ----
@app.get("/api/x/auth")
def x_auth():
    try: return RedirectResponse(xoauth.start_url())
    except ValueError as e: raise HTTPException(400, str(e))

@app.get("/api/x/callback")
def x_callback(code: str = "", state: str = "", error: str = ""):
    from urllib.parse import quote
    if error: return RedirectResponse(f"/#/connect?x_error={quote(error[:100])}")
    try: xoauth.handle_callback(code, state); return RedirectResponse("/#/connect?x=connected")
    except Exception as e: return RedirectResponse(f"/#/connect?x_error={quote(str(e)[:100])}")

@app.post("/api/x/sync")
def x_sync():
    try: return xoauth.sync_bookmarks()
    except Exception as e: raise HTTPException(400, f"X sync failed: {e}")

def basic_auth_gate(inner):
    """ASGI wrapper: when a dashboard password is set, every request except /healthz needs HTTP Basic auth.
    Used by main() so `python -m savehub.app` enforces the login exactly like the combined server does."""
    async def gate(scope, receive, send):
        if scope["type"] == "http" and auth.login_configured() and scope.get("path") != "/healthz":
            if not auth.basic_ok(dict(scope.get("headers") or []).get(b"authorization", b"")):
                await send({"type": "http.response.start", "status": 401, "headers": [(b"www-authenticate", b'Basic realm="SaveHub"'), (b"content-type", b"text/plain"),
                                                                                      (b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY")]})
                await send({"type": "http.response.body", "body": b"login required"}); return
        await inner(scope, receive, send)
    return gate

def main():
    import uvicorn
    from . import seed
    seed.seed()
    uvicorn.run(basic_auth_gate(app), host=os.environ.get("SAVEHUB_HOST", "127.0.0.1"), port=int(os.environ.get("SAVEHUB_PORT", 8765)))

if __name__ == "__main__": main()
