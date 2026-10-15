"""SaveHub MCP server (official Python MCP SDK, FastMCP).
stdio:            python -m savehub.mcp_server
streamable HTTP:  python -m savehub.mcp_server --http --port 8766   (endpoint /mcp; optional bearer token via SAVEHUB_MCP_TOKEN)
Config/auth: SAVEHUB_ENABLED_PLATFORMS=x,instagram,xiaohongshu (limits what tools can read/write), SAVEHUB_DB=path.
"""
import argparse, base64, os
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

@mcp.tool()
def list_platforms() -> dict:
    """List supported platforms, whether each is enabled in this MCP config, its connection method (live_api vs import) and item count."""
    st = db.stats()["by_platform"]; en = enabled()
    return {"platforms": [{"id": k, "name": v["name"], "method": v["method"], "detail": v["detail"], "enabled": k in en, "count": st.get(k, 0)} for k, v in db.PLATFORMS.items()]}

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
