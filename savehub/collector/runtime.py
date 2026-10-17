"""Process-level plumbing shared by the MCP bridge, the dashboard and auto-sync (never imports Playwright):

* collector_runtime()  cheap "can this host run a browser?" check (Render: no -> reported, never crashes)
* child_env()          allow-listed environment for collector subprocesses (no passwords/tokens/keys)
* account_lock()       cross-process, non-blocking per-account lock (flock) so only ONE run per account at a time,
                       whoever starts it (CLI collect, MCP collect, auto-sync, dashboard "Sync now")
* RUNNING              the in-process set of (platform, account) runs (same purpose, cheaper, shared by MCP + auto-sync)
"""
from __future__ import annotations
import contextlib, glob, importlib.util, os, shutil, threading
from .base import AccountBusy
from . import sessions

RUNNING: set = set()
RUNNING_LOCK = threading.Lock()

# names that look secret are never passed to a child, even if allow-listed
SECRET_MARKERS = ("PASSWORD", "PASSWD", "SECRET", "TOKEN", "VAULT", "API_KEY", "APIKEY", "PRIVATE", "CREDENTIAL", "COOKIE", "AUTH")
ENV_ALLOW = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TEMP", "TMP",
             "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "DBUS_SESSION_BUS_ADDRESS",
             "PLAYWRIGHT_BROWSERS_PATH", "SAVEHUB_CHROME", "SAVEHUB_LOG_LEVEL", "SAVEHUB_SESSIONS_DIR", "SAVEHUB_ENABLED_PLATFORMS",
             "VIRTUAL_ENV", "PYTHONPATH", "PYTHONIOENCODING", "SSL_CERT_FILE", "SSL_CERT_DIR",
             # iCloud collector limits (non-secret); the Apple ID password is never in any environment
             "SAVEHUB_ICLOUD_ROOT", "SAVEHUB_ICLOUD_CONTACTS", "SAVEHUB_ICLOUD_MAX_FOLDERS", "SAVEHUB_ICLOUD_MAX_DEPTH",
             "SAVEHUB_ICLOUD_EXTRACT_MAX_MB", "SAVEHUB_ICLOUD_TEXT_CHARS", "ICLOUD_CHINA",
             "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
             "SYSTEMROOT", "COMSPEC", "PATHEXT", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "WINDIR"}


def is_secret_name(k: str) -> bool:
    return any(m in k.upper() for m in SECRET_MARKERS)


def child_env(db_path) -> dict:
    """Allow-list (plus LC_*) minus anything secret-looking; proxy URLs carrying credentials are dropped too.
    The vault key, if any, goes to the child on its stdin pipe (--key-stdin), never in env/argv."""
    env = {k: v for k, v in os.environ.items()
           if (k in ENV_ALLOW or k.startswith("LC_")) and not is_secret_name(k) and not ("PROXY" in k.upper() and "@" in v)}
    env["SAVEHUB_DB"] = str(db_path); env["PYTHONUNBUFFERED"] = "1"
    return env


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
    from .runner import SYSTEM_CHROMES
    for c in SYSTEM_CHROMES:
        if shutil.which(c) or os.path.exists(c): return {"available": True, "browser": c}
    return {"available": False, "reason": "No Chromium found on this server (run: playwright install chromium)." + where}


# API collectors (no browser): platform -> import names they need
API_COLLECTOR_LIBS = {"icloud": ("pyicloud", "icloud_docs_mcp")}


def runtime_for(platform: str) -> dict:
    """Like collector_runtime(), but per platform: API collectors (iCloud) need their libraries, not a browser."""
    libs = API_COLLECTOR_LIBS.get(platform)
    if not libs: return collector_runtime()
    if os.environ.get("SAVEHUB_COLLECTOR", "").lower() in ("0", "off", "false", "disabled"):
        return {"available": False, "reason": "collector disabled on this server (SAVEHUB_COLLECTOR=off)."}
    missing = [m for m in libs if importlib.util.find_spec(m) is None]
    if missing:
        return {"available": False, "reason": f"{platform} collector needs {', '.join(missing)} (pip install -r requirements-collector.txt)."}
    return {"available": True, "browser": None, "api": platform}


try:
    import fcntl
except ImportError:          # Windows: fall back to the in-process lock only
    fcntl = None


@contextlib.contextmanager
def account_lock(platform: str, account: str, sessions_dir=None):
    """Exclusive, non-blocking lock for one (platform, account). Raises AccountBusy if another run holds it."""
    key = sessions.account_key(account)
    sessions.session_path(platform, sessions_dir, key)                 # validates platform + account (no path tricks)
    tag = (platform, key)
    with RUNNING_LOCK:
        if tag in RUNNING: raise AccountBusy(f"a collection for {platform} @{key} is already running")
        RUNNING.add(tag)
    fh = None
    try:
        if fcntl is not None:
            d = sessions.ensure_private_dir(sessions.sessions_dir(sessions_dir) / ".locks")
            fd = os.open(d / f"{platform}@{key}.lock", os.O_RDWR | os.O_CREAT, 0o600)
            fh = os.fdopen(fd, "r+")
            try: fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise AccountBusy(f"a collection for {platform} @{key} is already running (another process)") from None
        yield
    finally:
        if fh is not None:
            try: fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError: pass
            fh.close()
        with RUNNING_LOCK: RUNNING.discard(tag)
