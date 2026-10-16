"""Per-platform Playwright storage_state files (cookies + localStorage). No passwords are ever stored.

Default dir: <repo>/data/sessions (git-ignored via data/*), override with SAVEHUB_SESSIONS_DIR or --sessions-dir.
Files are written atomically with mode 0600 (dir 0700): they are equivalent to being logged in, keep them private.
"""
import json, os, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def sessions_dir(override=None) -> Path:
    return Path(override or os.environ.get("SAVEHUB_SESSIONS_DIR") or ROOT / "data" / "sessions")


def session_path(platform: str, override_dir=None) -> Path:
    return sessions_dir(override_dir) / f"{platform}.json"


def save_state(context, path: Path) -> Path:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    try: os.chmod(path.parent, 0o700)
    except OSError: pass
    tmp = path.with_suffix(".json.tmp")
    state = context.storage_state()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(state, f)
    os.replace(tmp, path); os.chmod(path, 0o600)
    return path


def describe(path: Path) -> dict:
    """Metadata only (never cookie values)."""
    p = Path(path)
    if not p.exists(): return {"file": str(p), "exists": False}
    try: st = json.loads(p.read_text())
    except Exception as e: return {"file": str(p), "exists": True, "valid": False, "error": str(e)}
    now = time.time()
    cookies = st.get("cookies", [])
    exp = [c["expires"] for c in cookies if c.get("expires", -1) > 0]
    return {"file": str(p), "exists": True, "valid": True, "cookies": len(cookies),
            "domains": sorted({c.get("domain", "").lstrip(".") for c in cookies}),
            "saved": time.strftime("%Y-%m-%d %H:%M:%S%z", time.localtime(p.stat().st_mtime)),
            "expired_cookies": sum(1 for e in exp if e < now)}


def list_sessions(override_dir=None) -> list:
    d = sessions_dir(override_dir)
    return [dict(platform=p.stem, **describe(p)) for p in sorted(d.glob("*.json"))] if d.exists() else []
