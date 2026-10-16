"""Encrypted password vault for account logins.

* Algorithm: AES-256-GCM (cryptography's AESGCM), random 96-bit nonce per secret, and the account id as
  associated data (a ciphertext copied onto another account will not decrypt).
* Key: derived ONLY from the env var SAVEHUB_VAULT_KEY (never stored, logged or returned).
  - a Fernet-style key (urlsafe-base64 of 32 random bytes; e.g. `Fernet.generate_key()`) -> HKDF-SHA256
  - any other passphrase (>= 16 chars)                                                 -> PBKDF2-HMAC-SHA256, 600k iterations
  The KDF salt is random per database (table vault_meta; not secret).
* Storage: table account_secrets(account_id, cipher) holds only "v1:<base64(nonce|ciphertext|tag)>".
* No key set (or no dashboard login configured) => vault disabled; nothing is ever stored in plaintext.
"""
import base64, hashlib, os, threading
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

def _raw_key():
    return os.environ.get(ENV, "").strip()

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
