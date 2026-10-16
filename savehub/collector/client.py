"""Where collected items go.

RestSink (default): POST /api/items on a SaveHub instance (local or the live Render site), HTTP Basic auth with the
dashboard login. Each push is CONFIRMED by reading the item back (GET /api/items/{id}) before it may be un-saved.
SqliteSink: write straight into a local SaveHub SQLite DB (no server needed).
"""
from __future__ import annotations
import logging, os, time
from dataclasses import dataclass
from typing import Optional
import httpx
from .base import CollectedItem, CollectorError, EXIT_PUSH_FAILED
from .logutil import ev


class PushError(CollectorError):
    exit_code = EXIT_PUSH_FAILED


@dataclass
class PushResult:
    item: CollectedItem
    id: Optional[int] = None
    created: bool = False
    confirmed: bool = False      # read back from SaveHub with the same platform+url
    error: str = ""


class RestSink:
    kind = "rest"

    def __init__(self, base_url: str, user: str = "", password: str = "", timeout: float = 90.0, retries: int = 3):
        self.base = base_url.rstrip("/")
        auth = (user or "savehub", password) if password else None
        self.http = httpx.Client(base_url=self.base, auth=auth, timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": "savehub-collector/1"})
        self.retries = retries

    @classmethod
    def from_env(cls, base_url=None, user=None):
        return cls(base_url or os.environ.get("SAVEHUB_URL", "http://127.0.0.1:8765"),
                   user or os.environ.get("SAVEHUB_DASHBOARD_USER", "savehub"),
                   os.environ.get("SAVEHUB_DASHBOARD_PASSWORD", ""))

    def _req(self, method, path, **kw):
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                r = self.http.request(method, path, **kw)
                if r.status_code in (502, 503, 504) and attempt < self.retries:   # Render cold start / restart
                    last = f"HTTP {r.status_code}"; raise httpx.TransportError(last)
                if r.status_code == 401:
                    raise PushError(f"SaveHub at {self.base} refused the login (401). Set SAVEHUB_DASHBOARD_USER / SAVEHUB_DASHBOARD_PASSWORD.")
                return r
            except httpx.TransportError as e:
                last = str(e) or type(e).__name__
                if attempt == self.retries: break
                ev("savehub.retry", logging.WARNING, path=path, attempt=attempt, error=last)
                time.sleep(min(2 ** attempt, 20))
        raise PushError(f"SaveHub at {self.base} unreachable: {last}")

    def check(self) -> dict:
        r = self._req("GET", "/api/health")
        if r.status_code != 200: raise PushError(f"SaveHub health check failed: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    def resolve_account(self, platform: str, handle: str) -> Optional[int]:
        h = handle.lstrip("@").lower()
        r = self._req("GET", "/api/accounts", params={"platform": platform, "q": h})
        for a in r.json().get("accounts", []):
            if (a.get("handle") or "").lstrip("@").lower() == h: return a["id"]
        return None

    def ensure_account(self, platform: str, handle: str) -> Optional[int]:
        """Account row id for this handle; created (connected_via=collector) if the dashboard has none yet."""
        aid = self.resolve_account(platform, handle)
        if aid is not None: return aid
        h = "@" + handle.lstrip("@")
        r = self._req("POST", "/api/accounts", json={"platform": platform, "handle": h, "display_name": h, "connected_via": "collector"})
        return r.json().get("id") if r.status_code == 200 else None

    def push(self, item: CollectedItem, account_id=None, collected_at="") -> PushResult:
        res = PushResult(item)
        try:
            r = self._req("POST", "/api/items", json=item.to_savehub(account_id, collected_at))
            if r.status_code != 200: res.error = f"HTTP {r.status_code}: {r.text[:200]}"; return res
            j = r.json(); res.id, res.created = j.get("id"), bool(j.get("created"))
            g = self._req("GET", f"/api/items/{res.id}")
            res.confirmed = g.status_code == 200 and g.json().get("url") == item.url and g.json().get("platform") == item.platform
            if not res.confirmed: res.error = f"read-back mismatch (HTTP {g.status_code})"
        except PushError as e:
            res.error = str(e)
        return res

    def close(self): self.http.close()


class SqliteSink:
    kind = "sqlite"

    def __init__(self, db_path):
        from pathlib import Path
        from savehub import db
        if Path(db_path) != db.DB_PATH:       # point the db module at this file, dropping any cached connection
            c = getattr(db._local, "c", None)
            if c is not None: c.close(); db._local.c = None
            db.DB_PATH = Path(db_path)
        self.db = db

    def check(self): return {"ok": True, "stats": self.db.stats()}

    def resolve_account(self, platform, handle):
        h = handle.lstrip("@").lower()
        for a in self.db.list_accounts(h, platform)["accounts"]:
            if (a.get("handle") or "").lstrip("@").lower() == h: return a["id"]
        return None

    def ensure_account(self, platform, handle):
        aid = self.resolve_account(platform, handle)
        if aid is not None: return aid
        h = "@" + handle.lstrip("@")
        return self.db.add_account({"platform": platform, "handle": h, "display_name": h, "connected_via": "collector"})[0]

    def push(self, item, account_id=None, collected_at="") -> PushResult:
        res = PushResult(item)
        try:
            res.id, res.created = self.db.add_item(item.to_savehub(account_id, collected_at))
            got = self.db.get_item(res.id)
            res.confirmed = bool(got and got["url"] == item.url and got["platform"] == item.platform)
        except Exception as e:
            res.error = str(e)
        return res

    def close(self): pass
