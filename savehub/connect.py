"""Short-lived, single-use connect links for the MCP connect flow.

An MCP client calls connect_apps(platforms): SaveHub creates pending account rows and a /connect/<token> URL with a
15-minute TTL. The token is stored only as a SHA-256 hash (table connect_tokens), scoped to those platform@handle
pairs, and burnt on first successful credential submit. The secure page collects username + password per app (over the
authenticated dashboard origin), writes them to the encrypted vault and triggers the automatic login - so no password
ever passes through the MCP client.
"""
from __future__ import annotations
import hashlib, json, secrets, time
from . import db

TTL_S = 15 * 60
TOKEN_BYTES = 32


def _table():
    c = db.conn()
    c.execute("CREATE TABLE IF NOT EXISTS connect_tokens(token_hash TEXT PRIMARY KEY, targets TEXT NOT NULL, "
              "created_at REAL, expires_at REAL, used_at REAL)")
    return c


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(targets: list[dict], ttl_s: int = TTL_S) -> dict:
    """targets: [{platform, account}]. Returns {token, path, expires_at}. Token is shown once; only its hash is stored."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = time.time()
    c = _table()
    c.execute("INSERT INTO connect_tokens(token_hash, targets, created_at, expires_at) VALUES(?,?,?,?)",
              (_hash(token), json.dumps(targets), now, now + ttl_s)); c.commit()
    return {"token": token, "path": f"/connect/{token}", "expires_at": now + ttl_s, "expires_in_s": ttl_s, "targets": targets}


def peek(token: str) -> dict | None:
    """Targets for a still-valid, unused token, or None (expired / used / unknown). Does NOT consume it."""
    r = _table().execute("SELECT targets, expires_at, used_at FROM connect_tokens WHERE token_hash=?", (_hash(token),)).fetchone()
    if not r: return None
    if r["used_at"] or r["expires_at"] < time.time(): return None
    return {"targets": json.loads(r["targets"])}


def consume(token: str) -> dict | None:
    """Atomically burn a valid token (single use). Returns its targets, or None if it was invalid/expired/already used."""
    c = _table(); now = time.time()
    cur = c.execute("UPDATE connect_tokens SET used_at=? WHERE token_hash=? AND used_at IS NULL AND expires_at>=?",
                    (now, _hash(token), now))
    c.commit()
    if cur.rowcount != 1: return None
    r = c.execute("SELECT targets FROM connect_tokens WHERE token_hash=?", (_hash(token),)).fetchone()
    return {"targets": json.loads(r["targets"])}


def purge_expired():
    c = _table(); c.execute("DELETE FROM connect_tokens WHERE expires_at < ?", (time.time() - 86400,)); c.commit()
