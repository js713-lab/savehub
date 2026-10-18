"""Encrypted password vault for account logins.

* Algorithm: AES-256-GCM (cryptography's AESGCM), random 96-bit nonce per secret, and the account id as
  associated data (a ciphertext copied onto another account will not decrypt).
* Key: SAVEHUB_VAULT_KEY from the environment, else a key file (SAVEHUB_VAULT_KEY_FILE, default <db dir>/.vault_key,
  mode 0600, git-ignored), else a key handed to a collector subprocess on stdin (set_runtime_key). Never logged or returned.
  - a Fernet-style key (urlsafe-base64 of 32 random bytes; e.g. `Fernet.generate_key()`) -> HKDF-SHA256
  - any other passphrase (>= 16 chars)                                                 -> PBKDF2-HMAC-SHA256, 600k iterations
  The KDF salt is random per database (table vault_meta; not secret).
* Storage: table account_secrets(account_id, cipher) holds only "v1:<base64(nonce|ciphertext|tag)>".
* No key set (or no dashboard login configured) => vault disabled; nothing is ever stored in plaintext.
* The same master key also encrypts browser-session files at rest (encrypt_session / decrypt_session): a separate
  AES-256-GCM key is derived per file salt (HKDF info "savehub-session-aes256gcm-v1" / PBKDF2), independent of the DB,
  with the session's "<platform>@<account>" as associated data. This needs no dashboard login (sessions never leave the box).
"""
import base64, hashlib, os, stat, threading
from pathlib import Path
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from . import auth, db

ENV = "SAVEHUB_VAULT_KEY"
PREFIX = "v1:"
MAX_LEN = 1024
_CHECK = b"savehub-vault-key-check"
_cache, _lock = {}, threading.Lock()

class VaultError(Exception):
    status = 400
class VaultDisabled(VaultError):
    status = 503
class VaultKeyMismatch(VaultError):
    status = 409

def _meta(k, v=None):
    c = db.conn()
    if v is None:
        r = c.execute("SELECT v FROM vault_meta WHERE k=?", (k,)).fetchone(); return r[0] if r else None
    c.execute("INSERT INTO vault_meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v)); c.commit()

def _salt():
    s = _meta("kdf_salt")
    if not s:
        s = base64.b64encode(os.urandom(16)).decode(); _meta("kdf_salt", s)
    return base64.b64decode(s)

_runtime_key = ""

def set_runtime_key(k: str):
    """Key passed in-memory (collector subprocess reads it from stdin; it is never put in the child's environment)."""
    global _runtime_key
    _runtime_key = (k or "").strip()

def key_file_path() -> Path:
    return Path(os.environ.get("SAVEHUB_VAULT_KEY_FILE") or (Path(db.DB_PATH).parent / ".vault_key"))

def _read_key_file() -> str:
    p = key_file_path()
    try:
        st = os.stat(p)
    except OSError:
        return ""
    if not stat.S_ISREG(st.st_mode): return ""
    if st.st_mode & 0o077:                       # tighten a group/world-readable key file instead of using it as-is
        try: os.chmod(p, 0o600)
        except OSError: return ""
    try: return p.read_text().strip()
    except OSError: return ""

def key_source() -> str:
    """'env' | 'runtime' | 'file' | '' (no key). Never the key itself."""
    if os.environ.get(ENV, "").strip(): return "env"
    if _runtime_key: return "runtime"
    return "file" if _read_key_file() else ""

def _raw_key():
    return os.environ.get(ENV, "").strip() or _runtime_key or _read_key_file()

def raw_key_for_child() -> str:
    """The master key, only to be written to a collector subprocess's stdin pipe (never env/argv/logs)."""
    return _raw_key()

def generate_key_file(path=None) -> Path:
    """Create a fresh Fernet-style key file (mode 0600, O_EXCL so an existing key is never overwritten)."""
    from cryptography.fernet import Fernet
    p = Path(path) if path else key_file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    old = os.umask(0o077)
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    finally:
        os.umask(old)
    with os.fdopen(fd, "w") as f: f.write(Fernet.generate_key().decode() + "\n")
    os.chmod(p, 0o600)
    return p

def _fernet_bytes(raw):
    try:
        b = base64.urlsafe_b64decode(raw.encode())
        return b if len(b) == 32 and len(raw) == 44 else None
    except Exception:
        return None

def _key():
    raw = _raw_key()
    if not raw: raise VaultDisabled(f"Password vault is off: the admin must set {ENV} in the server environment (Render → Environment).")
    fb = _fernet_bytes(raw)
    if fb is None and len(raw) < 16: raise VaultDisabled(f"{ENV} is too short (use a generated Fernet key or a 16+ character passphrase).")
    salt = _salt()
    ck = hashlib.sha256(raw.encode() + b"|" + salt).digest()      # cache id only; the derived key stays in memory
    with _lock:
        if ck not in _cache:
            if fb is not None:
                k = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"savehub-vault-aes256gcm-v1").derive(fb)
            else:
                k = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000).derive(raw.encode())
            _cache.clear(); _cache[ck] = k
        return _cache[ck]

def _enc(key, plaintext: bytes, aad: bytes) -> str:
    n = os.urandom(12)
    return PREFIX + base64.b64encode(n + AESGCM(key).encrypt(n, plaintext, aad)).decode()

def _dec(key, token: str, aad: bytes) -> bytes:
    if not token.startswith(PREFIX): raise VaultError("unknown ciphertext format")
    blob = base64.b64decode(token[len(PREFIX):])
    return AESGCM(key).decrypt(blob[:12], blob[12:], aad)

def _aad(aid): return f"savehub:account:{int(aid)}".encode()

def _checked_key():
    k = _key()
    chk = _meta("key_check")
    if not chk:
        _meta("key_check", _enc(k, _CHECK, b"savehub:check"))
    else:
        try:
            if _dec(k, chk, b"savehub:check") != _CHECK: raise InvalidTag()
        except (InvalidTag, ValueError):
            raise VaultKeyMismatch(f"{ENV} does not match the key these passwords were encrypted with. Restore the original key; saved passwords cannot be decrypted with a different one.")
    return k

def _require_ready():
    if not auth.login_configured():
        raise VaultDisabled("Password vault is off: dashboard login is not configured (set SAVEHUB_DASHBOARD_PASSWORD) — secrets are only revealed to a logged-in user.")
    return _checked_key()

def status():
    persistent = os.environ.get("SAVEHUB_PERSISTENT_DISK", "") in ("1", "true", "yes")
    on_render = bool(os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_HOSTNAME") or os.environ.get("RENDER_SERVICE_ID"))
    st = {"enabled": False, "reason": "", "algorithm": "AES-256-GCM", "key_env": ENV, "login_configured": auth.login_configured(),
          "storage": "persistent" if persistent else ("ephemeral" if on_render else "local"), "count": count()}
    try: _require_ready(); st["enabled"] = True
    except VaultError as e: st["reason"] = str(e); st["key_mismatch"] = isinstance(e, VaultKeyMismatch)
    return st

def set_secret(aid, plaintext):
    if not isinstance(plaintext, str) or not plaintext: raise VaultError("password must be a non-empty string")
    if len(plaintext) > MAX_LEN: raise VaultError(f"password too long (max {MAX_LEN})")
    if not db.get_account(aid): raise VaultError("account not found")
    token = _enc(_require_ready(), plaintext.encode(), _aad(aid))
    c = db.conn()
    c.execute("INSERT INTO account_secrets(account_id,cipher,updated_at) VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE SET cipher=excluded.cipher, updated_at=excluded.updated_at",
              (int(aid), token, db.now_iso())); c.commit()
    return {"ok": True, "has_password": True, "updated_at": db.now_iso()}

def reveal(aid):
    k = _require_ready()
    r = db.conn().execute("SELECT cipher, updated_at FROM account_secrets WHERE account_id=?", (int(aid),)).fetchone()
    if not r: raise VaultError("no password saved for this account")
    try: return {"password": _dec(k, r[0], _aad(aid)).decode(), "updated_at": r[1]}
    except (InvalidTag, ValueError): raise VaultKeyMismatch("could not decrypt (wrong SAVEHUB_VAULT_KEY or tampered data)")

def delete_secret(aid):
    c = db.conn(); n = c.execute("DELETE FROM account_secrets WHERE account_id=?", (int(aid),)).rowcount; c.commit(); return {"ok": True, "deleted": n}

def ids_with_secret():
    return {r[0]: r[1] for r in db.conn().execute("SELECT account_id, updated_at FROM account_secrets")}

def count():
    return db.conn().execute("SELECT count(*) FROM account_secrets").fetchone()[0]


# ---------------- browser-session files at rest (no DB needed: the KDF salt is stored in each file) ----------------
SESSION_INFO = b"savehub-session-aes256gcm-v1"
_session_cache: dict = {}
_proc_salt = os.urandom(16)

def session_key_available() -> bool:
    raw = _raw_key()
    return bool(raw) and (_fernet_bytes(raw) is not None or len(raw) >= 16)

def _session_key(salt: bytes):
    raw = _raw_key()
    if not raw: raise VaultDisabled(f"session file is encrypted: set {ENV} (or the key file) to use it")
    fb = _fernet_bytes(raw)
    if fb is None and len(raw) < 16: raise VaultDisabled(f"{ENV} is too short to encrypt session files")
    ck = hashlib.sha256(b"session|" + raw.encode() + b"|" + salt).digest()
    with _lock:
        if ck not in _session_cache:
            if fb is not None:
                k, kdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=SESSION_INFO).derive(fb), "hkdf-sha256"
            else:
                k, kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000).derive(raw.encode()), "pbkdf2-sha256-600k"
            if len(_session_cache) > 32: _session_cache.clear()
            _session_cache[ck] = (k, kdf)
        return _session_cache[ck]

def encrypt_session(plaintext: bytes, aad: bytes) -> dict:
    k, kdf = _session_key(_proc_salt)
    return {"savehub_encrypted": 1, "enc": "AES-256-GCM", "kdf": kdf, "salt": base64.b64encode(_proc_salt).decode(),
            "data": _enc(k, plaintext, aad)}

def decrypt_session(doc: dict, aad: bytes) -> bytes:
    try: salt = base64.b64decode(doc["salt"])
    except Exception: raise VaultError("malformed encrypted session file")
    k, _ = _session_key(salt)
    try: return _dec(k, str(doc.get("data") or ""), aad)
    except (InvalidTag, ValueError):
        raise VaultKeyMismatch("cannot decrypt the session file: wrong SAVEHUB_VAULT_KEY, or the file was renamed/tampered with")


# ---------------- automatic-login credentials (dashboard form -> auto sign-in; NEVER returned by any API or MCP tool) --------
# Separate from the password-manager entries above (which a logged-in dashboard user may reveal): these are only ever
# decrypted in memory by the on-box login job (savehub.collector.autologin) and handed to the app's own sign-in form.
# Table login_credentials(account_id, cipher) holds "v1:<base64(nonce|AES-256-GCM(json{u,p})|tag)>", AAD per account.
def _login_aad(aid): return f"savehub:login:{int(aid)}".encode()

def _login_table():
    c = db.conn()
    c.execute("CREATE TABLE IF NOT EXISTS login_credentials(account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE, "
              "cipher TEXT NOT NULL, updated_at TEXT)")
    return c

def set_login(aid, username, password):
    """Store the sign-in name + password for the automatic login (dashboard form only). Returns metadata only."""
    import json as _json
    if not isinstance(password, str) or not password: raise VaultError("password must be a non-empty string")
    if not isinstance(username, str) or not username.strip(): raise VaultError("username must be a non-empty string")
    if len(password) > MAX_LEN or len(username) > 320: raise VaultError("username or password too long")
    if not db.get_account(aid): raise VaultError("account not found")
    token = _enc(_require_ready(), _json.dumps({"u": username.strip(), "p": password}).encode(), _login_aad(aid))
    c = _login_table()
    c.execute("INSERT INTO login_credentials(account_id,cipher,updated_at) VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE SET "
              "cipher=excluded.cipher, updated_at=excluded.updated_at", (int(aid), token, db.now_iso())); c.commit()
    return {"ok": True, "has_login": True, "updated_at": db.now_iso()}

def get_login(aid):
    """(username, password) for the on-box login job, or None. In-memory only: never put this in a response or a log.
    Needs only the master key (the auto-sync daemon on the box may run without the dashboard password in its env)."""
    import json as _json
    r = _login_table().execute("SELECT cipher FROM login_credentials WHERE account_id=?", (int(aid),)).fetchone()
    if not r: return None
    try: d = _json.loads(_dec(_checked_key(), r[0], _login_aad(aid)).decode())
    except (InvalidTag, ValueError): raise VaultKeyMismatch("could not decrypt the saved login (wrong SAVEHUB_VAULT_KEY or tampered data)")
    return d.get("u") or "", d.get("p") or ""

def has_login(aid) -> bool:
    try: return bool(_login_table().execute("SELECT 1 FROM login_credentials WHERE account_id=?", (int(aid),)).fetchone())
    except Exception: return False

def delete_login(aid):
    c = _login_table(); n = c.execute("DELETE FROM login_credentials WHERE account_id=?", (int(aid),)).rowcount; c.commit()
    return {"ok": True, "deleted": n}
