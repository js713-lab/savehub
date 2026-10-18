"""Where collected items go.

RestSink (default): POST /api/items on a SaveHub instance (local or the live Render site), HTTP Basic auth with the
dashboard login. Each push is CONFIRMED by reading the item back (GET /api/items/{id}) before it may be un-saved.
SqliteSink: write straight into a local SaveHub SQLite DB (no server needed).
"""
from __future__ import annotations
import json, logging, os, time
from dataclasses import dataclass
from typing import Optional
import httpx
from .base import CollectedItem, CollectorError, EXIT_PUSH_FAILED
from .logutil import ev


def _akey(account) -> str:
    """'' for the legacy default session, else the normalised handle."""
    k = str(account or "").strip().lstrip("@").strip().lower()
    return "" if k in ("", "default") else k


def _keys(rows) -> set:
    out = set()
    for r in rows:
        if r["url"]: out.add(r["url"])
        if r["external_id"]: out.add("id:" + str(r["external_id"]))
    return out


class PushError(CollectorError):
    exit_code = EXIT_PUSH_FAILED


@dataclass
class PushResult:
    item: CollectedItem
    id: Optional[int] = None
    created: bool = False
    confirmed: bool = False      # read back from SaveHub with the same platform+url
    error: str = ""
    updated: bool = False        # already stored, but a newer version (e.g. a changed iCloud file) refreshed it


def versioned(item: CollectedItem) -> bool:
    """Items whose external_id carries a version (extra.version, e.g. iCloud files/contacts): a changed version updates
    the stored item (same platform+url) instead of being skipped as a duplicate. Never deletes anything."""
    return bool(item.external_id and (item.extra or {}).get("version"))


def update_fields(item: CollectedItem, account_id=None, collected_at="") -> dict:
    d = item.to_savehub(account_id, collected_at)
    from savehub import media
    out = {k: d[k] for k in ("title", "content", "author", "external_id", "media_url", *media.FIELDS) if k in d}
    out["raw"] = json.dumps(d.get("raw") or {}, ensure_ascii=False)[:20000]
    return out


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
        h = _akey(handle)
        if not h: return None
        r = self._req("GET", "/api/accounts", params={"platform": platform, "q": h})
        for a in r.json().get("accounts", []):
            if _akey(a.get("handle")) == h: return a["id"]
        return None

    def ensure_account(self, platform: str, handle: str) -> Optional[int]:
        """Account row id for this handle; created (connected_via=collector) if the dashboard has none yet."""
        aid = self.resolve_account(platform, handle)
        if aid is not None: return aid
        h = "@" + handle.lstrip("@")
        r = self._req("POST", "/api/accounts", json={"platform": platform, "handle": h, "display_name": h, "connected_via": "collector"})
        return r.json().get("id") if r.status_code == 200 else None

    def known_keys(self, platform: str, limit: int = 500, account: str = "") -> set:
        """Incremental mode: urls (+ "id:<external_id>") of the newest stored items of this platform. With `account`
        (a handle) only that account's items count (linked account row or source tag), so items another account of the
        same app already stored never stop this account's incremental run early."""
        h = _akey(account)
        params = {"platform": platform, "sort": "created_at", "order": "desc", "limit": int(limit)}
        aid = self.resolve_account(platform, h) if h else None
        if aid is not None: params["account_id"] = aid
        r = self._req("GET", "/api/items", params=params)
        if r.status_code != 200: raise PushError(f"could not read stored items for incremental sync: HTTP {r.status_code}")
        items = r.json().get("items", [])
        if h:
            src = f"collector:{platform}:{h}"
            items = [it for it in items if (aid is not None and it.get("account_id") == aid) or it.get("source") == src]
        return _keys(items)

    def push(self, item: CollectedItem, account_id=None, collected_at="") -> PushResult:
        res = PushResult(item)
        try:
            r = self._req("POST", "/api/items", json=item.to_savehub(account_id, collected_at))
            if r.status_code != 200: res.error = f"HTTP {r.status_code}: {r.text[:200]}"; return res
            j = r.json(); res.id, res.created = j.get("id"), bool(j.get("created"))
            g = self._req("GET", f"/api/items/{res.id}")
            res.confirmed = g.status_code == 200 and g.json().get("url") == item.url and g.json().get("platform") == item.platform
            if not res.confirmed: res.error = f"read-back mismatch (HTTP {g.status_code})"
            elif not res.created and versioned(item) and g.json().get("external_id") != item.external_id:
                u = self._req("PATCH", f"/api/items/{res.id}", json=update_fields(item, account_id, collected_at))
                if u.status_code == 200: res.updated = True
                else: res.error = f"update failed: HTTP {u.status_code}"
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
        return self.db.find_account(platform, handle)

    def ensure_account(self, platform, handle):
        aid = self.resolve_account(platform, handle)
        if aid is not None: return aid
        h = "@" + handle.lstrip("@")
        return self.db.add_account({"platform": platform, "handle": h, "display_name": h, "connected_via": "collector"})[0]

    def known_keys(self, platform: str, limit: int = 5000, account: str = "") -> set:
        """See RestSink.known_keys: per account when `account` is given, else every stored item of the platform."""
        h = _akey(account)
        if h:
            aid = self.resolve_account(platform, h)
            rows = self.db.conn().execute("SELECT url, external_id FROM items WHERE platform=? AND (account_id=? OR source=?) ORDER BY id DESC LIMIT ?",
                                          (platform, aid if aid is not None else -1, f"collector:{platform}:{h}", int(limit)))
        else:
            rows = self.db.conn().execute("SELECT url, external_id FROM items WHERE platform=? ORDER BY id DESC LIMIT ?", (platform, int(limit)))
        return _keys(rows)

    def record_unsave(self, item_id, status: str, error: str = "") -> None:
        """Remember the outcome of an un-save attempt on the stored item (raw.unsave); the item itself is never removed."""
        from datetime import datetime, timezone
        self.db.merge_item_raw(item_id, {"unsave": {"status": status, "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                                                    **({"error": error[:300]} if error else {})}})

    def push(self, item, account_id=None, collected_at="") -> PushResult:
        res = PushResult(item)
        try:
            res.id, res.created = self.db.add_item(item.to_savehub(account_id, collected_at))
            got = self.db.get_item(res.id)
            res.confirmed = bool(got and got["url"] == item.url and got["platform"] == item.platform)
            if res.confirmed and not res.created and versioned(item) and got.get("external_id") != item.external_id:
                self.db.update_item(res.id, update_fields(item, account_id, collected_at)); res.updated = True
        except Exception as e:
            res.error = str(e)
        return res

    def close(self): pass
