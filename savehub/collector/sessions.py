"""Per-platform, per-account Playwright storage_state files (cookies + localStorage). No passwords are ever stored.

Layout (one file per (platform, account), so several accounts of the same app can be logged in at once):
    <dir>/<platform>@<account>.json    e.g. x@js_main.json, x@js_alt.json, instagram@js.food.json
    <dir>/<platform>.json              legacy single-session file = account "default" (not linked to an account row)
The account key is the handle, lower-cased, without '@', restricted to [a-z0-9._-] (also prevents path tricks).

Default dir: <repo>/data/sessions (git-ignored via data/*), override with SAVEHUB_SESSIONS_DIR or --sessions-dir.
Files are written atomically with mode 0600 (dir 0700, umask 077): they are equivalent to being logged in, keep them private.

Encryption at rest: when a vault key is configured (SAVEHUB_VAULT_KEY or the 0600 key file data/.vault_key) every write
is AES-256-GCM encrypted (savehub.vault.encrypt_session; AAD = "<platform>@<account>" so a file cannot be swapped onto
another account). Reads decrypt in memory and hand Playwright a dict (no plaintext temp file). Legacy plaintext files are
still read and are re-saved encrypted the first time a collector uses them. Metadata (describe/list) never returns cookie
values and never rewrites files.
"""
import json, os, re, time
from pathlib import Path
from .base import CollectorError, EXIT_LOCKED

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ACCOUNT = "default"
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_PLATFORM_RE = re.compile(r"^[a-z0-9_]{1,32}$")
ENC_MARK = "savehub_encrypted"


class SessionLocked(CollectorError):
    """The session file is encrypted and no (or the wrong) vault key is available."""
    exit_code = EXIT_LOCKED


def _vault():
    from savehub import vault        # lazy: keeps `import savehub.collector` light
    return vault


def encryption_enabled() -> bool:
    try: return _vault().session_key_available()
    except Exception: return False


def ensure_private_dir(d) -> Path:
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    try: os.chmod(d, 0o700)
    except OSError: pass
    return d


def harden(override_dir=None) -> dict:
    """chmod the sessions dir 0700 and every session file 0600 (permissions only; contents untouched)."""
    d = sessions_dir(override_dir); fixed = 0
    if not d.exists(): return {"dir": "missing", "fixed": 0}
    if (d.stat().st_mode & 0o777) != 0o700: os.chmod(d, 0o700); fixed += 1
    for f in d.iterdir():
        try:
            if f.is_file() and not f.is_symlink() and (f.stat().st_mode & 0o777) != 0o600: os.chmod(f, 0o600); fixed += 1
        except OSError: pass
    return {"dir": "0700", "fixed": fixed}


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
    if not _PLATFORM_RE.match(str(platform or "")): raise ValueError(f"invalid platform id '{platform}'")
    k = account_key(account)
    return sessions_dir(override_dir) / (f"{platform}.json" if k == DEFAULT_ACCOUNT else f"{platform}@{k}.json")


def parse_name(path) -> tuple:
    """'x@js_main.json' -> ('x', 'js_main'); 'x.json' -> ('x', 'default')."""
    stem = Path(path).stem
    p, _, a = stem.partition("@")
    return p, (a or DEFAULT_ACCOUNT)


def _aad(path) -> bytes:
    p, a = parse_name(path)
    return f"savehub:session:{p}@{a}".encode()


def is_encrypted(doc) -> bool:
    return isinstance(doc, dict) and doc.get(ENC_MARK) == 1


def write_state(path, state: dict) -> Path:
    """Atomic, private write; encrypted when a vault key is configured."""
    path = Path(path); ensure_private_dir(path.parent)
    data = json.dumps(state)
    if encryption_enabled():
        data = json.dumps(_vault().encrypt_session(data.encode(), _aad(path)))
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try: tmp.unlink()
    except FileNotFoundError: pass
    old = os.umask(0o077)
    try: fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    finally: os.umask(old)
    try:
        with os.fdopen(fd, "w") as f: f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try: tmp.unlink()
        except OSError: pass
        raise
    os.chmod(path, 0o600)
    return path


def read_state(path, migrate: bool = True) -> dict:
    """Playwright storage_state dict for a session file (decrypted in memory).
    migrate=True re-saves a legacy plaintext file encrypted when a key is configured."""
    path = Path(path)
    doc = json.loads(path.read_text())
    if is_encrypted(doc):
        v = _vault()
        try: return json.loads(v.decrypt_session(doc, _aad(path)))
        except v.VaultError as e:
            raise SessionLocked(f"session file {path.name} is encrypted and cannot be opened: {e}") from None
    if migrate and encryption_enabled() and isinstance(doc, dict):
        try: write_state(path, doc)
        except OSError: pass
    return doc


def save_state(context, path: Path, indexed_db: bool = False) -> Path:
    """indexed_db=True also stores IndexedDB (Playwright >= 1.51) - needed by apps such as WhatsApp Web whose login
    keys live there; Playwright restores it from the same file when the context is created."""
    if indexed_db:
        try: state = context.storage_state(indexed_db=True)
        except TypeError: state = context.storage_state()       # older Playwright: cookies + localStorage only
    else:
        state = context.storage_state()
    return write_state(path, state)


def describe(path: Path) -> dict:
    """Metadata only (never cookie values, never rewrites the file). status: missing | invalid | locked | expired | saved.
    'saved' means the file parses and still has unexpired cookies; whether the app accepts it is only known on the
    next collect (an expired login is reported there as need_login). 'locked' = encrypted and no/wrong key here."""
    p = Path(path)
    if not p.exists(): return {"file": str(p), "exists": False, "status": "missing"}
    try:
        doc = json.loads(p.read_text())
        enc = is_encrypted(doc)
        st = read_state(p, migrate=False) if enc else doc
    except SessionLocked as e:
        return {"file": str(p), "exists": True, "valid": True, "encrypted": True, "status": "locked", "error": str(e)[:200],
                "saved": time.strftime("%Y-%m-%d %H:%M:%S%z", time.localtime(p.stat().st_mtime))}
    except Exception as e:
        return {"file": str(p), "exists": True, "valid": False, "status": "invalid", "error": type(e).__name__}
    now = time.time()
    cookies = st.get("cookies", []) if isinstance(st, dict) else []
    exp = [c["expires"] for c in cookies if c.get("expires", -1) > 0]
    live = sum(1 for c in cookies if not (c.get("expires", -1) > 0) or c["expires"] > now)
    return {"file": str(p), "exists": True, "valid": True, "encrypted": enc, "status": "saved" if live else "expired", "cookies": len(cookies),
            "domains": sorted({c.get("domain", "").lstrip(".") for c in cookies}),
            "saved": time.strftime("%Y-%m-%d %H:%M:%S%z", time.localtime(p.stat().st_mtime)),
            "mtime": p.stat().st_mtime, "expired_cookies": sum(1 for e in exp if e < now)}


def list_sessions(override_dir=None, platform=None) -> list:
    d = sessions_dir(override_dir)
    if not d.exists(): return []
    out = []
    for f in sorted(d.glob("*.json")):
        if not f.is_file() or f.is_symlink(): continue
        p, a = parse_name(f)
        if not _PLATFORM_RE.match(p) or (a != DEFAULT_ACCOUNT and not _KEY_RE.match(a)): continue
        if platform and p != platform: continue
        out.append(dict(platform=p, account=a, **describe(f)))
    return out
