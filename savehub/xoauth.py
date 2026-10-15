"""X (Twitter) OAuth 2.0 Authorization Code + PKCE, and bookmarks fetch (GET /2/users/:id/bookmarks).
Implemented per X API v2 docs; NOT tested end-to-end (needs the user's X developer app Client ID and an API tier with bookmark access)."""
import base64, hashlib, secrets, time
from urllib.parse import urlencode
import httpx
from . import db

AUTH_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
API = "https://api.x.com/2"
SCOPES = "tweet.read users.read bookmark.read offline.access"
_pending = {}  # state -> verifier

def start_url():
    cfg = db.get_settings()["x"]
    if not cfg.get("client_id"): raise ValueError("Set your X Client ID in Settings (or env X_CLIENT_ID) first.")
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16); _pending[state] = verifier
    return AUTH_URL + "?" + urlencode({"response_type": "code", "client_id": cfg["client_id"], "redirect_uri": cfg["redirect_uri"],
                                       "scope": SCOPES, "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})

def _token_request(data):
    cfg = db.get_settings()["x"]
    auth = (cfg["client_id"], cfg["client_secret"]) if cfg.get("client_secret") else None
    if not auth: data["client_id"] = cfg["client_id"]
    r = httpx.post(TOKEN_URL, data=data, auth=auth, timeout=30); r.raise_for_status()
    t = r.json(); t["expires_at"] = time.time() + t.get("expires_in", 7200) - 60
    db.set_setting("x_tokens", t); return t

def handle_callback(code, state):
    verifier = _pending.pop(state, None)
    if not verifier: raise ValueError("Unknown/expired OAuth state")
    cfg = db.get_settings()["x"]
    t = _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": cfg["redirect_uri"], "code_verifier": verifier})
    me = httpx.get(API + "/users/me", headers={"Authorization": f"Bearer {t['access_token']}"}, timeout=30).json()["data"]
    db.set_setting("x_user", me)
    aid, _ = db.add_account({"platform": "x", "display_name": me.get("name"), "handle": me.get("username"), "profile_url": f"https://x.com/{me.get('username')}", "connected_via": "x_oauth2", "status": "connected"})
    return me

def _access_token():
    t = db.get_settings().get("x_tokens")
    if not t: raise ValueError("X not connected")
    if time.time() > t.get("expires_at", 0) and t.get("refresh_token"):
        t = _token_request({"grant_type": "refresh_token", "refresh_token": t["refresh_token"]})
    return t["access_token"]

def sync_bookmarks(max_pages=8):
    tok = _access_token(); me = db.get_settings().get("x_user") or {}
    acct = db.conn().execute("SELECT id FROM accounts WHERE platform='x' AND lower(handle)=lower(?)", (me.get("username", ""),)).fetchone()
    h = {"Authorization": f"Bearer {tok}"}; params = {"max_results": 100, "tweet.fields": "created_at,author_id,text,entities", "expansions": "author_id", "user.fields": "username,name"}
    created = seen = 0
    for _ in range(max_pages):
        r = httpx.get(f"{API}/users/{me['id']}/bookmarks", headers=h, params=params, timeout=30); r.raise_for_status(); j = r.json()
        users = {u["id"]: u for u in j.get("includes", {}).get("users", [])}
        for tw in j.get("data", []):
            u = users.get(tw.get("author_id"), {}); seen += 1
            _, c = db.add_item({"platform": "x", "url": f"https://x.com/{u.get('username','i')}/status/{tw['id']}", "external_id": tw["id"],
                                "title": tw["text"][:120], "content": tw["text"], "author": u.get("name"), "author_handle": u.get("username"),
                                "saved_at": tw.get("created_at"), "source": "x_api", "account_id": acct["id"] if acct else None, "raw": tw})
            created += c
        nt = j.get("meta", {}).get("next_token")
        if not nt: break
        params["pagination_token"] = nt
    return {"fetched": seen, "created": created}

def status():
    s = db.get_settings()
    return {"client_id_set": bool(s["x"].get("client_id")), "connected": bool(s.get("x_tokens")), "user": s.get("x_user")}
