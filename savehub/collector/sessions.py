"""Per-platform, per-account Playwright storage_state files (cookies + localStorage). No passwords are ever stored.

Layout (one file per (platform, account), so several accounts of the same app can be logged in at once):
    <dir>/<platform>@<account>.json    e.g. x@js_main.json, x@js_alt.json, instagram@js.food.json
    <dir>/<platform>.json              legacy single-session file = account "default" (not linked to an account row)
The account key is the handle, lower-cased, without '@', restricted to [a-z0-9._-] (also prevents path tricks).

Default dir: <repo>/data/sessions (git-ignored via data/*), override with SAVEHUB_SESSIONS_DIR or --sessions-dir.
Files are written atomically with mode 0600 (dir 0700): they are equivalent to being logged in, keep them private.
"""
import json, os, re, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ACCOUNT = "default"
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def sessions_dir(override=None) -> Path:
    return Path(override or os.environ.get("SAVEHUB_SESSIONS_DIR") or ROOT / "data" / "sessions")


def account_key(account) -> str:
    """'@Js_Main ' -> 'js_main'. '' / None / 'default' -> 'default' (the legacy single-session file)."""
    k = str(account or "").strip().lstrip("@").strip().lower()
    if not k or k == DEFAULT_ACCOUNT: return DEFAULT_ACCOUNT
    if not _KEY_RE.match(k):
        raise ValueError(f"invalid account '{account}': use the handle (letters, digits, . _ -), e.g. @my_handle")
    return k


def session_path(platform: str, override_dir=None, account=None) -> Path:
    k = account_key(account)
    return sessions_dir(override_dir) / (f"{platform}.json" if k == DEFAULT_ACCOUNT else f"{platform}@{k}.json")


def parse_name(path) -> tuple:
    """'x@js_main.json' -> ('x', 'js_main'); 'x.json' -> ('x', 'default')."""
    stem = Path(path).stem
    p, _, a = stem.partition("@")
    return p, (a or DEFAULT_ACCOUNT)


def save_state(context, path: Path, indexed_db: bool = False) -> Path:
    """indexed_db=True also stores IndexedDB (Playwright >= 1.51) - needed by apps such as WhatsApp Web whose login
    keys live there; Playwright restores it from the same file when the context is created."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    try: os.chmod(path.parent, 0o700)
    except OSError: pass
    tmp = path.with_suffix(".json.tmp")
    if indexed_db:
        try: state = context.storage_state(indexed_db=True)
        except TypeError: state = context.storage_state()       # older Playwright: cookies + localStorage only
    else:
        state = context.storage_state()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(state, f)
    os.replace(tmp, path); os.chmod(path, 0o600)
    return path


def describe(path: Path) -> dict:
    """Metadata only (never cookie values). status: missing | invalid | expired | saved.
    'saved' means the file parses and still has unexpired cookies; whether the app accepts it is only known on the
    next collect (an expired login is reported there as need_login)."""
    p = Path(path)
    if not p.exists(): return {"file": str(p), "exists": False, "status": "missing"}
    try: st = json.loads(p.read_text())
    except Exception as e: return {"file": str(p), "exists": True, "valid": False, "status": "invalid", "error": str(e)[:200]}
    now = time.time()
    cookies = st.get("cookies", []) if isinstance(st, dict) else []
    exp = [c["expires"] for c in cookies if c.get("expires", -1) > 0]
    live = sum(1 for c in cookies if not (c.get("expires", -1) > 0) or c["expires"] > now)
    return {"file": str(p), "exists": True, "valid": True, "status": "saved" if live else "expired", "cookies": len(cookies),
            "domains": sorted({c.get("domain", "").lstrip(".") for c in cookies}),
            "saved": time.strftime("%Y-%m-%d %H:%M:%S%z", time.localtime(p.stat().st_mtime)),
            "expired_cookies": sum(1 for e in exp if e < now)}


def list_sessions(override_dir=None, platform=None) -> list:
    d = sessions_dir(override_dir)
    if not d.exists(): return []
    out = []
    for f in sorted(d.glob("*.json")):
        p, a = parse_name(f)
        if platform and p != platform: continue
        out.append(dict(platform=p, account=a, **describe(f)))
    return out
