"""SaveHub MCP server (official Python MCP SDK, FastMCP).
stdio:            python -m savehub.mcp_server
streamable HTTP:  python -m savehub.mcp_server --http --port 8766   (endpoint /mcp; optional bearer token via SAVEHUB_MCP_TOKEN)
Browser collector: login_status / collect (subprocess, SQLite sink; sessions per platform+account; SAVEHUB_COLLECT_TIMEOUT).
Config/auth: SAVEHUB_ENABLED_PLATFORMS=x,instagram,xiaohongshu (limits what tools can read/write), SAVEHUB_DB=path.
"""
import argparse, asyncio, base64, hmac, importlib.util, json, os, signal, stat, sys, time
from pathlib import Path
from typing import Optional
from mcp.server.fastmcp import Context, FastMCP
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
    """Bearer token for HTTP MCP: $SAVEHUB_MCP_TOKEN, else a generated one persisted in data/.mcp_token (mode 0600).
    The token value is never printed or logged; read it from that file."""
    t = os.environ.get("SAVEHUB_MCP_TOKEN", "").strip()
    if t: return t
    import secrets
    f = db.DB_PATH.parent / ".mcp_token"
    if f.exists():
        try:
            if os.stat(f).st_mode & 0o077: os.chmod(f, 0o600)
        except OSError: pass
        return f.read_text().strip()
    f.parent.mkdir(parents=True, exist_ok=True); t = secrets.token_urlsafe(32)
    old = os.umask(0o077)
    try: fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    finally: os.umask(old)
    with os.fdopen(fd, "w") as fh: fh.write(t)
    print(f"[savehub] Generated an MCP bearer token and saved it to {f} (mode 0600; not printed).", flush=True)
    return t

mcp = FastMCP("savehub", transport_security=transport_security(),
              stateless_http=os.environ.get("SAVEHUB_MCP_STATELESS", "1") != "0",
              instructions="Personal saved-items library (bookmarks/saves/favourites from X, Threads, Instagram, Facebook, Messenger, WhatsApp, WeChat, TikTok, Douyin, Xiaohongshu; iCloud Drive files + iCloud Contacts, read-only), contacts and accounts. "
                           "Logins (incl. the Apple ID password for iCloud) only happen on the box in a terminal; never ask the user for a password here.")

def enabled(): return db.get_settings()["enabled_platforms"]

def _slim(it):
    return {k: it.get(k) for k in ("id", "platform", "title", "url", "author", "author_handle", "saved_at", "tags", "summary", "account_id", "account_name", "is_sample")}

# ---------------- browser collector bridge (sessions are created on the box with the CLI; never via MCP) ----------------
# The collector package is imported lazily and never pulls in Playwright here (runs happen in a subprocess), so the
# Render image (no Playwright/Chromium) keeps working and just reports the collector as unavailable.
COLLECT_TIMEOUT_S = int(os.environ.get("SAVEHUB_COLLECT_TIMEOUT", "900"))     # per account; the run is killed after this

def _col():
    from .collector import base, sessions, platforms  # noqa: F401  (registers every collector)
    return base, sessions

def _rt():
    from .collector import runtime
    return runtime

def __getattr__(name):            # `_running` = the process-wide per-account run set shared with auto-sync
    if name == "_running": return _rt().RUNNING
    raise AttributeError(name)

def collector_runtime() -> dict:
    """Can this server run the browser collector? Cheap checks only (no browser launch)."""
    return _rt().collector_runtime()

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
    return {"supported": True, "implemented": bool(cls.implemented), "kind": cls.kind, "kinds": list(getattr(cls, "kinds", (cls.kind,))),
            "uses_browser": bool(getattr(cls, "uses_browser", True)), "login": getattr(cls, "login_mode", "browser"),
            "supports_unsave": bool(cls.supports_unsave and cls.implemented), "accounts": accts,
            "logged_in_accounts": sum(1 for a in accts if a["logged_in"]), "backend": _backend_info(platform),
            **({"runtime": _rt().runtime_for(platform)} if not getattr(cls, "uses_browser", True) else {})}

def _backend_info(platform: str) -> dict:
    """auto | playwright | <library> setting + which libraries are installed (set it on the dashboard Accounts page)."""
    from .collector import backends
    try: return backends.describe(platform)
    except Exception as e: return {"backend": "playwright", "error": str(e)[:200]}

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
    """Report whether a saved session exists for a platform, per account (account = handle like '@me'; omit for all accounts);
    session = saved | expired (needs login) | missing | locked | invalid. Read-only: login itself only happens on the box
    (python -m savehub.collector login <platform> --account @<handle>; for icloud a terminal prompt for Apple ID, password and 2FA)."""
    base, sessions = _col()
    if platform not in base.REGISTRY: return {"error": f"unknown platform '{platform}'; one of {sorted(base.REGISTRY)}"}
    try: accts = _session_list(platform, account)
    except ValueError as e: return {"error": str(e)}
    ok = [a for a in accts if a["logged_in"]]
    cls = base.REGISTRY[platform]
    r = {"platform": platform, "collector_implemented": bool(cls.implemented), "logged_in": bool(ok),
         "accounts": accts, "collector_runtime": _rt().runtime_for(platform), "backend": _backend_info(platform),
         "login": getattr(cls, "login_mode", "browser")}
    for a in accts:
        if a["session"] == "expired": a["needs_login"] = True; a["fix"] = _login_cmd(platform, a["account"])
    if not base.REGISTRY[platform].implemented:
        r["note"] = f"the '{platform}' collector is not implemented yet (only {[k for k, c in base.REGISTRY.items() if c.implemented]})"
    if not ok:
        how = ("in a terminal on the box (or open a login window on the box desktop: python -m savehub.collector login-terminal "
               f"{platform} --account @<handle> --display :N); it asks for the Apple ID, password and 2FA code, and keeps only the trusted session"
               if getattr(cls, "login_mode", "browser") == "terminal" else "on the box (needs a desktop/display)")
        r["fix"] = (f"No valid saved session for {platform}" + (f" account {account}" if account else "") +
                    f". Log in {how}: {_login_cmd(platform, account or '')}")
    return r

def _child_env() -> dict:
    """Environment for the collector subprocess: allow-listed, no passwords/tokens/keys (the SQLite sink needs none).
    The vault key (to open encrypted session files) is written to the child's stdin instead (--key-stdin)."""
    return _rt().child_env(db.DB_PATH)

def _vault_key_for_child() -> str:
    from . import vault
    return vault.raw_key_for_child() if vault.session_key_available() else ""

def _collect_cmd(platform: str, account_key: str, max_items: Optional[int], unsave: bool) -> list:
    _, sessions = _col()
    cmd = [sys.executable, "-m", "savehub.collector", "--log-level", "WARNING"] + (["--key-stdin"] if _vault_key_for_child() else []) + \
          ["--sessions-dir", str(sessions.sessions_dir()), "collect", platform, "--sink", "sqlite", "--db", str(db.DB_PATH)]
    if account_key and account_key != sessions.DEFAULT_ACCOUNT: cmd += ["--account", account_key]
    if max_items: cmd += ["--max-items", str(int(max_items))]
    if unsave is True: cmd += ["--unsave", "--unsave-confirm"]      # only on an explicit unsave=true
    return cmd

async def _run_collect_proc(cmd: list, timeout: int) -> dict:
    key = _vault_key_for_child() if "--key-stdin" in cmd else ""
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(Path(__file__).resolve().parent.parent), env=_child_env(),
                                                stdin=asyncio.subprocess.PIPE if "--key-stdin" in cmd else asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        out, err = await asyncio.wait_for(proc.communicate((key + "\n").encode() if "--key-stdin" in cmd else None), timeout)
    except asyncio.TimeoutError:
        try: os.killpg(proc.pid, signal.SIGKILL)          # kill the browser children too
        except Exception: proc.kill()
        await proc.wait()
        return {"status": "timeout", "error": f"collection did not finish within {timeout}s and was stopped (raise SAVEHUB_COLLECT_TIMEOUT or lower max_items)"}
    try: res = json.loads(out.decode() or "{}")
    except Exception:
        from .collector.logutil import scrub
        res = {"status": "error", "error": "collector produced no result", "log_tail": scrub(err.decode(errors="replace")[-800:])}
    res["exit_code"] = proc.returncode
    return res

def _sync_state(what: str, platform: str, account: str, **kw) -> None:
    """Keep the per-account auto-sync state (sync_status / dashboard) in step with MCP `collect` runs. Never raises."""
    try:
        from .collector import autosync
        if what == "start": autosync.mark_external_start(platform, account, "mcp")
        elif what == "record": autosync.record_external(platform, account, kw.get("res") or {}, kw.get("started"), "mcp")
        elif what == "needs_login": autosync.record_needs_login(platform, account, "saved session expired: log in again on the box", "mcp")
    except Exception as e:                        # pragma: no cover - state bookkeeping must not break a collection
        print(f"[savehub] sync-state update failed: {type(e).__name__}", file=sys.stderr)

@mcp.tool()
async def collect(platform: str, account: Optional[str] = None, max_items: Optional[int] = None, unsave: Optional[bool] = None) -> dict:
    """Run the collector for a platform and store new saves in SaveHub, tagged with the account they came from
    (linked account_id + source=collector:<platform>:<handle>). account = handle like '@me'; omit to collect every logged-in account.
    max_items caps items per account (default 500 on a first full collect; icloud: per kind, files and contacts). Un-save is OFF by default:
    unsave=true un-saves on the real app for this call; unsave=false never does; omitted = the per-account dashboard setting
    (Accounts page toggle, default off). Items are un-saved only after each one is confirmed stored, never by auto-sync, and it
    is refused for read-only collectors (icloud, messenger, whatsapp, wechat). Needs a saved session (sign in on the dashboard
    or on the box first) and a browser (icloud: no browser, pyicloud)."""
    base, sessions = _col()
    if platform not in base.REGISTRY: return {"error": f"unknown platform '{platform}'; one of {sorted(base.REGISTRY)}"}
    if platform not in enabled(): return {"error": f"platform '{platform}' is not enabled in this config"}
    if not base.REGISTRY[platform].implemented:
        return {"error": f"the '{platform}' collector is not implemented yet; only {[k for k, c in base.REGISTRY.items() if c.implemented]} can be collected. "
                         f"Use import_file with the app's data export instead.", "status": "not_implemented"}
    if max_items is not None and not (1 <= int(max_items) <= 2000): return {"error": "max_items must be between 1 and 2000"}
    if unsave is True and not base.REGISTRY[platform].supports_unsave and not getattr(base.REGISTRY[platform], "uses_browser", True):   # read-only API collectors (iCloud): refuse up front
        return {"error": f"'{platform}' is read-only in SaveHub (its collector gathers {base.REGISTRY[platform].kind}); nothing can be un-saved or deleted there",
                "status": "unsave_not_supported"}
    try: accts = _session_list(platform, account)
    except ValueError as e: return {"error": str(e)}
    ready = [a for a in accts if a["logged_in"]]
    for a in accts:                               # expired sessions -> needs_login in the shared sync state (dashboard / sync_status)
        if a.get("session") == "expired": _sync_state("needs_login", platform, a["account"])
    if not ready:
        return {"status": "need_login", "platform": platform, "accounts": accts,
                "error": f"No valid saved session for {platform}" + (f" account {account}" if account else "") +
                         f". Log in on the box first: {_login_cmd(platform, account or '')} (then call collect again)."}
    rt = _rt().runtime_for(platform)
    if not rt["available"]:
        return {"status": "browser_unavailable" if getattr(base.REGISTRY[platform], "uses_browser", True) else "collector_unavailable",
                "platform": platform, "error": "Cannot collect on this server: " + rt["reason"]}
    runs, explicit = [], unsave
    unsave = unsave is True
    rt_mod = _rt()
    if unsave and not base.REGISTRY[platform].supports_unsave:   # usage error, not an account problem: no run, no sync-state change
        err = f"'{platform}' has no un-save action (its collector gathers {base.REGISTRY[platform].kind}); call collect without unsave"
        ready, runs = [], [{"account": a["account"], "status": "CollectorError", "error": err, "unsave_mode": "refused"} for a in ready]
    for a in ready:
        key = sessions.account_key(a["account"])
        lock = (platform, key)
        with rt_mod.RUNNING_LOCK:                 # shared with auto-sync / "Sync now": one run per account at a time
            busy = lock in rt_mod.RUNNING
            if not busy: rt_mod.RUNNING.add(lock)
        if busy:
            runs.append({"account": a["account"], "status": "busy", "error": "a collection for this account is already running"}); continue
        started = time.time()
        # explicit unsave=true/false wins; omitted -> the per-account (else per-platform) dashboard toggle, default OFF
        acct_unsave = (explicit is True) if explicit is not None else (base.REGISTRY[platform].supports_unsave
                                                                        and db.unsave_enabled(platform, "" if key == sessions.DEFAULT_ACCOUNT else key))
        if acct_unsave: unsave = True
        _sync_state("start", platform, key)
        res = {"status": "error", "error": "collection failed to start"}
        try: res = await _run_collect_proc(_collect_cmd(platform, key, max_items, acct_unsave), COLLECT_TIMEOUT_S)
        finally:
            with rt_mod.RUNNING_LOCK: rt_mod.RUNNING.discard(lock)
            _sync_state("record", platform, key, res=res, started=started)
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

IMPORT_MAX_BYTES = 50_000_000

def _safe_import_path(path: str) -> Path:
    """import_file(path=...) may read local export files, but never SaveHub's own secrets or hidden/system files:
    no session files, DB, vault/MCP key files, nothing under a dot-directory (.ssh, .git, .config, ...), /proc, /sys,
    /dev, /etc; symlinks are resolved first; regular files only."""
    _, sessions = _col()
    p = Path(os.path.expanduser(str(path))).resolve()
    data = db.DB_PATH.resolve().parent
    deny_dirs = [sessions.sessions_dir().resolve(), Path("/proc"), Path("/sys"), Path("/dev"), Path("/etc")]
    deny_files = {db.DB_PATH.resolve(), data / ".vault_key", data / ".mcp_token"}
    try:
        from . import vault
        deny_files.add(vault.key_file_path().resolve())
    except Exception: pass
    bad = (any(p == d or d in p.parents for d in deny_dirs) or p in deny_files or p.name.startswith(str(db.DB_PATH.name) + "-")
           or any(part.startswith(".") for part in p.parts[1:]))
    if bad: raise ValueError("refusing to import from that path (SaveHub secrets, hidden or system files are off-limits)")
    try: st = os.stat(p)
    except OSError: raise ValueError("file not found") from None
    if not stat.S_ISREG(st.st_mode): raise ValueError("not a regular file")
    return p

@mcp.tool()
def import_file(path: str = "", kind: str = "auto", text: str = "", content_base64: str = "", platform: str = "", account_id: Optional[int] = None) -> dict:
    """Import a data-export file or pasted links. Provide a local file `path`, OR `text`, OR `content_base64`.
    kind: auto | instagram_json | facebook_json | threads_json | messenger_json | tiktok_json | whatsapp_txt | bookmarks_html | html | csv | json | paste | vcf | contacts_csv | accounts_csv"""
    fn = ""
    if path:
        try: p = _safe_import_path(path)
        except ValueError as e: return {"error": str(e)}
        with open(p, "rb") as f: data = f.read(IMPORT_MAX_BYTES + 1)
        if len(data) > IMPORT_MAX_BYTES: return {"error": f"file too large (max {IMPORT_MAX_BYTES // 1_000_000} MB)"}
        fn = p.name
    elif content_base64: data = base64.b64decode(content_base64)
    else: data = text
    if not data: return {"error": "nothing to import"}
    return importers.import_auto(kind, data, platform or None, account_id, fn)

@mcp.tool()
def search_contacts(query: str = "", account_id: Optional[int] = None, limit: int = 50) -> dict:
    """Search imported contacts by name, phone, email, org or notes."""
    return db.search_contacts(query, account_id, None, "name", "asc", limit)

@mcp.tool()
def sync_now(target: str = "all", platform: Optional[str] = None, account: Optional[str] = None) -> dict:
    """Run sync now. target: all | collector | notion | drive. 'collector' (also part of 'all') pulls new saved items from every
    signed-in account (or one platform / account) with an incremental collect in the background - it NEVER un-saves.
    Notion / Drive only run when enabled in Settings. Poll sync_status / connection_status for progress."""
    if target not in ("all", "collector", "notion", "drive"): return {"error": "target must be all | collector | notion | drive"}
    res = sync.sync_now(target) if target != "collector" else {}
    if target in ("all", "collector"):
        a = _autosync()
        try: res["collector"] = a.request_sync(platform or None, account or None)
        except ValueError as e: res["collector"] = {"error": str(e)}
        except a.Unavailable as e: res["collector"] = {"skipped": f"collector unavailable on this server: {e}"}
        except a.RateLimited as e: res["collector"] = {"skipped": str(e), "retry_after": e.retry_after}
    return res

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

# ---------------- auto-sync (near real-time incremental collection; never un-saves) ----------------
def _autosync():
    _col()
    from .collector import autosync
    return autosync

@mcp.tool()
def sync_status() -> dict:
    """Auto-sync status: enabled, interval_minutes, whether this server can run a browser (available/reason), whether a sync
    loop is running, and per logged-in account: state (synced | syncing | pending | needs_login | error | locked), last_success,
    items_added_last_run/total, last_error, next_run. Read-only; never returns cookies or session file paths."""
    return _autosync().status()

@mcp.tool()
def set_autosync(enabled: bool, interval_minutes: Optional[int] = None) -> dict:
    """Turn auto-sync on/off and optionally set the interval (minutes, 2..1440). Auto-sync polls each logged-in account with an
    incremental collect (stops at already-stored items) and NEVER un-saves anything. The running loop (CLI daemon
    `python -m savehub.collector autosync` or the server with SAVEHUB_AUTOSYNC=1) picks the change up within ~15 s."""
    a = _autosync()
    if not isinstance(enabled, bool): return {"error": "enabled must be true or false"}
    try: cfg = a.set_config(enabled=enabled, interval_minutes=interval_minutes)
    except ValueError as e: return {"error": str(e)}
    st = a.status()
    r = {"enabled": cfg["enabled"], "interval_minutes": cfg["interval_minutes"], "available": st["available"], "loop": st["loop"], "unsave": "never"}
    if not st["available"]: r["note"] = "This server cannot run a browser, so nothing will sync here: " + (st.get("reason") or "")
    elif cfg["enabled"] and not st["loop"]["running"]:
        r["note"] = "No auto-sync loop is running: start `python -m savehub.collector autosync` on the box, or run the server with SAVEHUB_AUTOSYNC=1."
    return r

# ---------------- connect apps (MCP chooses the apps; credentials go ONLY through a single-use dashboard link) ----------------
def _public_base() -> str:
    b = os.environ.get("SAVEHUB_PUBLIC_URL") or (f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}" if os.environ.get("RENDER_EXTERNAL_HOSTNAME") else "")
    if not b: b = f"http://127.0.0.1:{os.environ.get('SAVEHUB_PORT') or os.environ.get('PORT') or 8765}"
    return b.rstrip("/")

def _connectable() -> list:
    base, _ = _col()
    from .collector import login_flows
    return [{"id": k, "name": db.PLATFORMS[k]["name"], "login": "qr" if not login_flows.supports_password_login(k) else
             ("apple_id" if k == "icloud" else "password")} for k in db.PLATFORMS if k in base.REGISTRY and base.REGISTRY[k].implemented]

def _client_elicitation(ctx):
    """The client's declared elicitation capability (None = not supported, so never send it a request)."""
    try: return ctx.request_context.session.client_params.capabilities.elicitation
    except Exception: return None

def _elicit_schema(ids):
    from pydantic import create_model, Field
    fields = {}
    for p in ids:
        fields[f"{p}"] = (bool, Field(default=False, description=f"Connect {db.PLATFORMS[p]['name']}"))
        fields[f"{p}_handle"] = (str, Field(default="", description=f"{db.PLATFORMS[p]['name']}: your handle / username (no password)"))
    return create_model("ConnectApps", **fields)

@mcp.tool()
async def connect_apps(platforms: Optional[list[str]] = None, handles: Optional[dict[str, str]] = None, ctx: Context = None) -> dict:
    """Connect apps for automatic syncing. Without platforms: asks the user which apps (MCP elicitation where the client supports
    it, else returns the list to ask). With platforms (+ optional handles {platform: '@handle'}): creates pending accounts and
    returns a single-use secure link (15 min) to SaveHub's own page, where the user types username + password per app. NEVER
    send or ask for a password here: passwords go only into that page -> encrypted vault -> automatic sign-in -> first sync.
    Then poll connection_status()."""
    from .collector import sessions as _s
    from . import connect as cl
    apps = _connectable(); ids = [a["id"] for a in apps]
    handles = dict(handles or {})
    cap = _client_elicitation(ctx) if ctx is not None else None
    if not platforms and cap is not None:
        try:
            r = await ctx.elicit("Which apps should SaveHub connect? (no passwords here: you will get a secure link)", _elicit_schema(ids))
            if r.action == "accept" and r.data is not None:
                d = r.data.model_dump()
                platforms = [p for p in ids if d.get(p)]
                handles.update({p: d.get(f"{p}_handle") or "" for p in platforms if d.get(f"{p}_handle")})
        except Exception:
            platforms = platforms or None                      # client has no elicitation: fall back to returning the list
    if not platforms:
        return {"step": "choose_apps", "apps": apps, "note": "Ask the user which apps to connect (and their handle per app, never a password), "
                "then call connect_apps(platforms=[...], handles={...})."}
    bad = [p for p in platforms if p not in ids]
    if bad: return {"error": f"cannot connect: {bad}; one of {ids}"}
    if any(isinstance(v, str) and len(v) > 64 for v in handles.values()): return {"error": "handles must be short usernames"}
    targets = []
    for p in dict.fromkeys(platforms):
        try: k = _s.account_key(handles.get(p) or "me")
        except ValueError as e: return {"error": str(e)}
        if k == _s.DEFAULT_ACCOUNT: k = "me"
        aid, _ = db.add_account({"platform": p, "handle": "@" + k, "display_name": "@" + k, "connected_via": "autologin", "status": "pending"})
        targets.append({"platform": p, "account": "@" + k, "account_id": aid})
    link = cl.create([{"platform": t["platform"], "account": t["account"]} for t in targets])
    url = _public_base() + link["path"]
    opened = False
    if cap is not None and getattr(cap, "url", None) is not None:
        try:
            import secrets as _sec
            r = await ctx.elicit_url("Open SaveHub's secure page to enter your logins (they never pass through this chat).", url, _sec.token_hex(8))
            opened = getattr(r, "action", "") == "accept"
        except Exception:
            opened = False
    return {"step": "open_link", "url": url, "expires_in_s": link["expires_in_s"], "single_use": True, "opened_by_client": opened,
            "accounts": targets, "note": "Give the user this link (or it was opened for them). Credentials are entered ONLY on that page "
            "(never through MCP); SaveHub then signs in automatically and starts the first sync. Poll connection_status()."}

@mcp.tool()
def connection_status(platform: Optional[str] = None) -> dict:
    """Progress of connected accounts: Signing in… | Connected | Syncing | Needs code (with code_url to enter it on the dashboard) |
    Scan QR code | Wrong password | Error, plus last_sync and items collected. Never returns passwords, cookies or tokens."""
    _col()
    from .collector import autologin, sessions as _s
    out, seen = [], set()
    for a in db.list_accounts("", platform or None, None, "platform", "asc", 5000)["accounts"]:
        try: k = _s.account_key(db.norm_handle(a.get("handle")))
        except ValueError: continue
        if k == _s.DEFAULT_ACCOUNT or a["platform"] == "web": continue
        if a.get("connected_via") not in ("autologin", "collector") and not _s.session_path(a["platform"], None, k).exists(): continue
        seen.add((a["platform"], k))
        st = autologin.account_status(a["platform"], k)
        st["items"] = db.list_items("", [a["platform"]], None, a["id"], limit=1)["total"]
        if st.get("code_url"): st["code_url"] = _public_base() + st["code_url"]
        st["done"] = st["status"] in ("connected", "done") and bool(st.get("last_sync") or st["items"])
        out.append(st)
    return {"accounts": out, "all_done": bool(out) and all(x["done"] for x in out)}

class BearerAuth:
    """Every request to the MCP endpoint (all tools, incl. collect / set_autosync / sync_status) needs the bearer token
    (constant-time compare). WebSockets are refused outright."""
    def __init__(self, app, token): self.app, self.token = app, token
    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008}); return
        if scope["type"] == "http":
            got = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not self.token or not hmac.compare_digest(got, f"Bearer {self.token}".encode()):
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer"),
                                                                                      (b"x-content-type-options", b"nosniff"), (b"cache-control", b"no-store")]})
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
