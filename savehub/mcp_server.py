"""SaveHub MCP server (official Python MCP SDK, FastMCP).
stdio:            python -m savehub.mcp_server
streamable HTTP:  python -m savehub.mcp_server --http --port 8766   (endpoint /mcp; optional bearer token via SAVEHUB_MCP_TOKEN)
Browser collector: login_status / collect (subprocess, SQLite sink; sessions per platform+account; SAVEHUB_COLLECT_TIMEOUT).
Config/auth: SAVEHUB_ENABLED_PLATFORMS=x,instagram,xiaohongshu (limits what tools can read/write), SAVEHUB_DB=path.
"""
import argparse, asyncio, base64, glob, importlib.util, json, os, shutil, signal, sys
from pathlib import Path
from typing import Optional
from mcp.server.fastmcp import FastMCP
from . import db, importers, summary, sync, tagger

from mcp.server.transport_security import TransportSecuritySettings

def transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection: localhost always allowed, plus Render's host ($RENDER_EXTERNAL_HOSTNAME)
    and any extra hosts in SAVEHUB_ALLOWED_HOSTS (comma separated, e.g. 'savehub.example.com,my.host:8443')."""
    hosts = ["127.0.0.1", "localhost", "[::1]", "127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*", "http://127.0.0.1", "http://localhost"]
    extra = [h.strip() for h in os.environ.get("SAVEHUB_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if os.environ.get("RENDER_EXTERNAL_HOSTNAME"): extra.append(os.environ["RENDER_EXTERNAL_HOSTNAME"].strip())
    for h in extra:
        if h == "*":  # explicit opt-out
            return TransportSecuritySettings(enable_dns_rebinding_protection=False)
        hosts += [h, h + ":*"] if ":" not in h else [h]
        origins += [f"https://{h}", f"http://{h}"]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)

def mcp_token() -> str:
    """Bearer token for HTTP MCP: $SAVEHUB_MCP_TOKEN, else a generated one persisted in data/.mcp_token."""
    t = os.environ.get("SAVEHUB_MCP_TOKEN", "").strip()
    if t: return t
    import secrets
    f = db.DB_PATH.parent / ".mcp_token"
    if f.exists(): return f.read_text().strip()
    f.parent.mkdir(parents=True, exist_ok=True); t = secrets.token_urlsafe(32); f.write_text(t)
    print(f"[savehub] Generated MCP bearer token (saved to {f}): {t}", flush=True)
    return t

mcp = FastMCP("savehub", transport_security=transport_security(),
              stateless_http=os.environ.get("SAVEHUB_MCP_STATELESS", "1") != "0",
              instructions="Personal saved-items library (bookmarks/saves/favourites from X, Threads, Instagram, Facebook, Messenger, WhatsApp, WeChat, TikTok, Douyin, Xiaohongshu), contacts and accounts.")

def enabled(): return db.get_settings()["enabled_platforms"]

def _slim(it):
    return {k: it.get(k) for k in ("id", "platform", "title", "url", "author", "author_handle", "saved_at", "tags", "summary", "account_id", "account_name", "is_sample")}

# ---------------- browser collector bridge (sessions are created on the box with the CLI; never via MCP) ----------------
# The collector package is imported lazily and never pulls in Playwright here (runs happen in a subprocess), so the
# Render image (no Playwright/Chromium) keeps working and just reports the collector as unavailable.
COLLECT_TIMEOUT_S = int(os.environ.get("SAVEHUB_COLLECT_TIMEOUT", "900"))     # per account; the run is killed after this
_SECRET_ENV = ("PASSWORD", "SECRET", "TOKEN", "VAULT", "API_KEY", "PRIVATE")
_running: set = set()

def _col():
    from .collector import base, sessions, platforms  # noqa: F401  (registers every collector)
    return base, sessions

def collector_runtime() -> dict:
    """Can this server run the browser collector? Cheap checks only (no browser launch)."""
    on_render = bool(os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_HOSTNAME"))
    where = " This server looks like the Render deployment, which has no browser: run collection on the box instead." if on_render else ""
    if os.environ.get("SAVEHUB_COLLECTOR", "").lower() in ("0", "off", "false", "disabled"):
        return {"available": False, "reason": "browser collector disabled on this server (SAVEHUB_COLLECTOR=off)." + where}
    if importlib.util.find_spec("playwright") is None:
        return {"available": False, "reason": "Playwright is not installed on this server (pip install -r requirements-collector.txt && playwright install chromium)." + where}
    exe = os.environ.get("SAVEHUB_CHROME")
    if exe: return {"available": os.path.exists(exe), "browser": exe, **({} if os.path.exists(exe) else {"reason": f"SAVEHUB_CHROME={exe} does not exist." + where})}
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "", os.path.expanduser("~/.cache/ms-playwright"),
             os.path.expanduser("~/Library/Caches/ms-playwright"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright")]
    for r in filter(None, roots):
        hit = sorted(glob.glob(os.path.join(r, "chromium*")))
        if hit: return {"available": True, "browser": "playwright:" + os.path.basename(hit[-1])}
    from .collector.runner import SYSTEM_CHROMES
    for c in SYSTEM_CHROMES:
        if shutil.which(c) or os.path.exists(c): return {"available": True, "browser": c}
    return {"available": False, "reason": "No Chromium found on this server (run: playwright install chromium)." + where}

def _accounts_rows(platform: str) -> dict:
    """account key -> SaveHub account row (id/display name) for this platform."""
    out = {}
    for a in db.list_accounts("", platform, None, "platform", "asc", 1000)["accounts"]:
        h = (a.get("handle") or "").strip().lstrip("@").lower()
        if h: out.setdefault(h, {"account_id": a["id"], "display_name": a.get("display_name") or ""})
    return out

def _session_list(platform: str, account: Optional[str] = None) -> list:
    """Per-account session metadata (status only, never cookie values)."""
    _, sessions = _col()
    rows = _accounts_rows(platform)
    if account:
        k = sessions.account_key(account)
        found = [dict(platform=platform, account=k, **sessions.describe(sessions.session_path(platform, None, k)))]
    else:
        found = sessions.list_sessions(None, platform)
    out = []
    for s in found:
        k = s["account"]
        d = {"account": k if k == sessions.DEFAULT_ACCOUNT else "@" + k, "session": s["status"],
             "logged_in": s["status"] == "saved", "session_file": os.path.basename(s["file"])}
        if s.get("saved"): d["saved_at"] = s["saved"]
        if k != sessions.DEFAULT_ACCOUNT: d.update(rows.get(k, {"account_id": None}))
        out.append(d)
    return out

def _login_cmd(platform: str, account: str = "") -> str:
    k = account.strip().lstrip("@") if account and account != "default" else ""
    return f"python -m savehub.collector login {platform}" + (f" --account @{k}" if k else " --account @<handle>")

def _collector_info(platform: str) -> dict:
    base, _ = _col()
    cls = base.REGISTRY.get(platform)
    if cls is None: return {"supported": False, "implemented": False, "accounts": [], "logged_in_accounts": 0}
    accts = _session_list(platform)
    return {"supported": True, "implemented": bool(cls.implemented), "accounts": accts,
            "logged_in_accounts": sum(1 for a in accts if a["logged_in"])}

@mcp.tool()
def list_platforms() -> dict:
    """List supported platforms, whether each is enabled in this MCP config, its connection method (live_api vs import), item count,
    and browser-collector support with per-account session status (collector.accounts: account, session, logged_in, account_id)."""
    st = db.stats()["by_platform"]; en = enabled()
    return {"collector_runtime": collector_runtime(),
            "platforms": [{"id": k, "name": v["name"], "method": v["method"], "detail": v["detail"], "enabled": k in en, "count": st.get(k, 0),
                           "collector": _collector_info(k)} for k, v in db.PLATFORMS.items()]}

@mcp.tool()
def login_status(platform: str, account: Optional[str] = None) -> dict:
    """Report whether a saved browser session exists for a platform, per account (account = handle like '@me'; omit for all accounts).
    Read-only: login itself only happens on the box (python -m savehub.collector login <platform> --account @<handle>)."""
    base, sessions = _col()
    if platform not in base.REGISTRY: return {"error": f"unknown platform '{platform}'; one of {sorted(base.REGISTRY)}"}
    try: accts = _session_list(platform, account)
    except ValueError as e: return {"error": str(e)}
    ok = [a for a in accts if a["logged_in"]]
    r = {"platform": platform, "collector_implemented": bool(base.REGISTRY[platform].implemented), "logged_in": bool(ok),
         "accounts": accts, "collector_runtime": collector_runtime()}
    if not base.REGISTRY[platform].implemented:
        r["note"] = f"the '{platform}' collector is not implemented yet (only {[k for k, c in base.REGISTRY.items() if c.implemented]})"
    if not ok:
        r["fix"] = (f"No valid saved session for {platform}" + (f" account {account}" if account else "") +
                    f". Log in on the box (needs a desktop/display): {_login_cmd(platform, account or '')}")
    return r

def _child_env() -> dict:
    """Environment for the collector subprocess: no passwords/tokens/keys (the SQLite sink needs none)."""
    env = {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in _SECRET_ENV)}
    env["SAVEHUB_DB"] = str(db.DB_PATH); env["PYTHONUNBUFFERED"] = "1"
    return env

def _collect_cmd(platform: str, account_key: str, max_items: Optional[int], unsave: bool) -> list:
    _, sessions = _col()
    cmd = [sys.executable, "-m", "savehub.collector", "--log-level", "WARNING", "--sessions-dir", str(sessions.sessions_dir()),
           "collect", platform, "--sink", "sqlite", "--db", str(db.DB_PATH)]
    if account_key and account_key != sessions.DEFAULT_ACCOUNT: cmd += ["--account", account_key]
    if max_items: cmd += ["--max-items", str(int(max_items))]
    if unsave is True: cmd += ["--unsave", "--unsave-confirm"]      # only on an explicit unsave=true
    return cmd

async def _run_collect_proc(cmd: list, timeout: int) -> dict:
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(Path(__file__).resolve().parent.parent), env=_child_env(),
                                                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        try: os.killpg(proc.pid, signal.SIGKILL)          # kill the browser children too
        except Exception: proc.kill()
        await proc.wait()
        return {"status": "timeout", "error": f"collection did not finish within {timeout}s and was stopped (raise SAVEHUB_COLLECT_TIMEOUT or lower max_items)"}
    try: res = json.loads(out.decode() or "{}")
    except Exception: res = {"status": "error", "error": "collector produced no result", "log_tail": err.decode(errors="replace")[-800:]}
    res["exit_code"] = proc.returncode
    return res

@mcp.tool()
async def collect(platform: str, account: Optional[str] = None, max_items: Optional[int] = None, unsave: bool = False) -> dict:
    """Run the browser collector for a platform and store new saves in SaveHub, tagged with the account they came from
    (linked account_id + source=collector:<platform>:<handle>). account = handle like '@me'; omit to collect every logged-in account.
    max_items caps items per account (default 200). unsave=false (default) never touches the app; ONLY unsave=true un-saves items
    on the real app, and only after each one is confirmed stored. Needs a saved session (log in on the box first) and a browser."""
    base, sessions = _col()
    if platform not in base.REGISTRY: return {"error": f"unknown platform '{platform}'; one of {sorted(base.REGISTRY)}"}
    if platform not in enabled(): return {"error": f"platform '{platform}' is not enabled in this config"}
    if not base.REGISTRY[platform].implemented:
        return {"error": f"the '{platform}' collector is not implemented yet; only {[k for k, c in base.REGISTRY.items() if c.implemented]} can be collected. "
                         f"Use import_file with the app's data export instead.", "status": "not_implemented"}
    if max_items is not None and not (1 <= int(max_items) <= 2000): return {"error": "max_items must be between 1 and 2000"}
    try: accts = _session_list(platform, account)
    except ValueError as e: return {"error": str(e)}
    ready = [a for a in accts if a["logged_in"]]
    if not ready:
        return {"status": "need_login", "platform": platform, "accounts": accts,
                "error": f"No valid saved session for {platform}" + (f" account {account}" if account else "") +
                         f". Log in on the box first: {_login_cmd(platform, account or '')} (then call collect again)."}
    rt = collector_runtime()
    if not rt["available"]:
        return {"status": "browser_unavailable", "platform": platform, "error": "Cannot collect on this server: " + rt["reason"]}
    runs, unsave = [], unsave is True
    for a in ready:
        key = sessions.account_key(a["account"])
        lock = (platform, key)
        if lock in _running:
            runs.append({"account": a["account"], "status": "busy", "error": "a collection for this account is already running"}); continue
        _running.add(lock)
        try: res = await _run_collect_proc(_collect_cmd(platform, key, max_items, unsave), COLLECT_TIMEOUT_S)
        finally: _running.discard(lock)
        res.pop("session_file", None)
        if isinstance(res.get("items"), list): res["items"] = res["items"][:25]
        res.setdefault("account", a["account"])
        runs.append(res)
    tot = lambda k: sum(int(r.get(k) or 0) for r in runs)
    ok = all(r.get("status") == "ok" for r in runs)
    return {"platform": platform, "status": "ok" if ok else ("partial" if any(r.get("status") == "ok" for r in runs) else "error"),
            "unsave": "live" if unsave else "off", "accounts": len(runs), "collected": tot("collected"), "created": tot("created"),
            "duplicates": tot("duplicates"), "push_failed": tot("push_failed"), "runs": runs,
            "skipped_accounts": [a["account"] for a in accts if not a["logged_in"]]}

@mcp.tool()
def search_items(query: str, platforms: Optional[list[str]] = None, account_id: Optional[int] = None, limit: int = 20) -> dict:
    """Full-text search saved items (English + Chinese) across title, content, author, tags and AI summary. Ranked by relevance."""
    ps = [p for p in (platforms or enabled()) if p in enabled()]
    if not ps: return {"total": 0, "items": [], "note": "requested platforms are not enabled in this MCP config"}
    r = db.list_items(query, ps, None, account_id, "relevance", "desc", limit, 0, enabled_only=enabled())
    return {"total": r["total"], "items": [_slim(i) for i in r["items"]]}

@mcp.tool()
def list_items(platforms: Optional[list[str]] = None, tags: Optional[list[str]] = None, account_id: Optional[int] = None,
               sort: str = "saved_at", order: str = "desc", limit: int = 20, offset: int = 0, query: str = "") -> dict:
    """List items with filters. sort: saved_at | platform | author | title | created_at; order: asc | desc."""
    ps = [p for p in (platforms or enabled()) if p in enabled()]
    if not ps: return {"total": 0, "items": [], "note": "requested platforms are not enabled in this MCP config"}
    r = db.list_items(query, ps, tags, account_id, sort, order, limit, offset, enabled_only=enabled())
    return {"total": r["total"], "items": [_slim(i) for i in r["items"]]}

@mcp.tool()
def get_item(item_id: int) -> dict:
    """Get one saved item with full content."""
    it = db.get_item(item_id)
    if not it or it["platform"] not in enabled(): return {"error": "not found or platform disabled"}
    return it

@mcp.tool()
def add_item(url: str, title: str = "", content: str = "", platform: str = "", author: str = "", account_id: Optional[int] = None, tags: Optional[list[str]] = None) -> dict:
    """Save a new item (link). Platform is auto-detected from the URL if omitted. Auto-tagged unless tags given."""
    p = platform or importers.detect_platform(url)
    if p not in enabled(): return {"error": f"platform '{p}' is not enabled in this config"}
    iid, created = db.add_item({"url": url, "title": title or url, "content": content, "platform": p, "author": author, "account_id": account_id, "tags": tags or [], "source": "mcp"})
    return {"id": iid, "created": created, "item": _slim(db.get_item(iid))}

@mcp.tool()
def tag_items(item_ids: Optional[list[int]] = None, force: bool = False) -> dict:
    """(Re)run AI auto-tagging + summary. No ids = tag all untagged items (force=True retags everything)."""
    return {"retagged": tagger.retag(item_ids, force), "provider": tagger.provider_status()}

@mcp.tool()
def get_summary(days: int = 7, use_ai: bool = True) -> dict:
    """Digest of recent saves: counts by platform, top tags/authors, and an AI (or template) written summary."""
    s = summary.get_summary(days, use_ai, enabled_only=enabled())
    s["recent"] = [_slim(i) for i in s["recent"]]; return s

@mcp.tool()
def import_file(path: str = "", kind: str = "auto", text: str = "", content_base64: str = "", platform: str = "", account_id: Optional[int] = None) -> dict:
    """Import a data-export file or pasted links. Provide a local file `path`, OR `text`, OR `content_base64`.
    kind: auto | instagram_json | facebook_json | threads_json | messenger_json | tiktok_json | whatsapp_txt | bookmarks_html | html | csv | json | paste | vcf | contacts_csv | accounts_csv"""
    fn = ""
    if path:
        with open(os.path.expanduser(path), "rb") as f: data = f.read()
        fn = os.path.basename(path)
    elif content_base64: data = base64.b64decode(content_base64)
    else: data = text
    if not data: return {"error": "nothing to import"}
    return importers.import_auto(kind, data, platform or None, account_id, fn)

@mcp.tool()
def search_contacts(query: str = "", account_id: Optional[int] = None, limit: int = 50) -> dict:
    """Search imported contacts by name, phone, email, org or notes."""
    return db.search_contacts(query, account_id, None, "name", "asc", limit)

@mcp.tool()
def sync_now(target: str = "all") -> dict:
    """Run optional sync (target: all | notion | drive). Only runs targets enabled in Settings."""
    return sync.sync_now(target)

@mcp.tool()
def list_accounts(platform: str = "", status: str = "", sort: str = "platform", limit: int = 100) -> dict:
    """List the user's accounts (multiple per platform) with item/contact counts. No passwords are stored."""
    return db.list_accounts("", platform or None, status or None, sort, "asc", limit)

@mcp.tool()
def search_accounts(query: str, platform: str = "", limit: int = 50) -> dict:
    """Search accounts by display name, handle, profile URL, linked email/phone, notes, tags."""
    return db.list_accounts(query, platform or None, None, "platform", "asc", limit)

@mcp.tool()
def add_account(platform: str, display_name: str = "", handle: str = "", profile_url: str = "", linked_email: str = "", linked_phone: str = "",
                notes: str = "", tags: Optional[list[str]] = None, status: str = "active", connected_via: str = "manual") -> dict:
    """Add an account record (do NOT pass passwords — they are never stored)."""
    if platform not in db.PLATFORMS: return {"error": f"unknown platform; one of {list(db.PLATFORMS)}"}
    aid, created = db.add_account({"platform": platform, "display_name": display_name, "handle": handle, "profile_url": profile_url, "linked_email": linked_email,
                                   "linked_phone": linked_phone, "notes": notes, "tags": tags or [], "status": status, "connected_via": connected_via})
    return {"id": aid, "created": created, "account": db.get_account(aid)}

class BearerAuth:
    def __init__(self, app, token): self.app, self.token = app, token
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.token:
            h = dict(scope.get("headers") or [])
            if h.get(b"authorization", b"").decode() != f"Bearer {self.token}":
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"}); return
        await self.app(scope, receive, send)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--http", action="store_true"); ap.add_argument("--port", type=int, default=int(os.environ.get("SAVEHUB_MCP_PORT", 8766)))
    ap.add_argument("--host", default="127.0.0.1"); a = ap.parse_args()
    from . import seed; seed.seed()
    if not a.http:
        mcp.run("stdio"); return
    import uvicorn
    extra = [h.strip() for h in os.environ.get("SAVEHUB_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if extra:
        from mcp.server.transport_security import TransportSecuritySettings
        mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*"] + extra, allowed_origins=["http://127.0.0.1:*", "http://localhost:*"] + ["https://" + h for h in extra])
    uvicorn.run(BearerAuth(mcp.streamable_http_app(), mcp_token()), host=a.host, port=a.port, log_level="warning")

if __name__ == "__main__": main()
