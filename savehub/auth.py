"""Dashboard HTTP Basic auth helpers (shared by the ASGI dispatcher and the vault endpoints).
Credentials come from env at call time: SAVEHUB_DASHBOARD_USER (default 'savehub') / SAVEHUB_DASHBOARD_PASSWORD."""
import base64, hmac, os

def creds():
    return os.environ.get("SAVEHUB_DASHBOARD_USER", "savehub"), os.environ.get("SAVEHUB_DASHBOARD_PASSWORD", "")

def login_configured():
    return bool(creds()[1])

def basic_ok(authorization):
    """True only if a dashboard password is configured AND the Authorization header matches it."""
    user, pw = creds()
    if not pw: return False
    h = authorization.decode() if isinstance(authorization, bytes) else (authorization or "")
    if not h.lower().startswith("basic "): return False
    try: u, _, p = base64.b64decode(h[6:]).decode().partition(":")
    except Exception: return False
    return hmac.compare_digest(u.encode(), user.encode()) & hmac.compare_digest(p.encode(), pw.encode())
