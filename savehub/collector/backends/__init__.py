"""Optional library backends: open-source API clients that replace the Playwright scraper for some platforms.

Per-platform setting  backend = auto | playwright | <library>
  playwright  the browser collector (savehub/collector/platforms/*), the only path that can un-save
  <library>   only that library; an error is an error (no browser is launched)
  auto        the platform's libraries first (if installed), then the Playwright collector on any error
Platforms without a library behave as `playwright` whatever the setting says.

The setting is stored in the SaveHub DB (autosync_meta key "collector_backends"), set from the dashboard Accounts page
(PUT /api/collector/backends) and read by every collect run (CLI, MCP collect, auto-sync, "Sync now"); a run can override it
with `collect --backend`. Un-save always uses the Playwright path (library backends never un-save anything).

Security: a backend receives the session's Playwright storage_state already decrypted IN MEMORY and converts the cookies to
what its library needs (a dict / Cookie jar). Nothing is written to disk, except where a library insists on a file: then
`private_tempdir()` gives a 0700 dir with 0600 files that is removed in `finally`. Backends never take passwords, never
log cookie values and never write back to the session file.
"""
from __future__ import annotations
import contextlib, importlib, importlib.util, json, logging, os, shutil, tempfile, time
from dataclasses import dataclass
from typing import Optional
from ..base import CollectorError
from ..logutil import ev, scrub

PLAYWRIGHT, AUTO = "playwright", "auto"
META_KEY = "collector_backends"


class BackendError(CollectorError):
    """A library backend failed (auth refused, endpoint changed, rate limited, ...). auto -> falls back to Playwright."""


class BackendUnavailable(BackendError):
    """The library is not installed in this environment."""


@dataclass(frozen=True)
class BackendInfo:
    name: str               # id used in the setting, e.g. "twscrape"
    platform: str
    module: str             # savehub.collector.backends.<module>
    dist: str               # PyPI distribution
    import_name: str        # top-level import of the library
    pin: str                # version pinned in requirements-collector.txt
    license: str
    repo: str
    note: str = ""


# Only libraries that were clearly more robust than the scraper made it in (see README "Library backends").
BACKENDS: dict[str, list[BackendInfo]] = {
    "x": [BackendInfo("twscrape", "x", "twscrape", "twscrape", "twscrape", "0.20.1", "MIT", "https://github.com/vladkens/twscrape",
                      "X web GraphQL Bookmarks timeline with your session cookies (auth_token + ct0); no browser, JSON instead of DOM")],
    "instagram": [BackendInfo("instaloader", "instagram", "instaloader", "instaloader", "instaloader", "4.15.4", "MIT",
                              "https://github.com/instaloader/instaloader",
                              "saved posts via Instagram web GraphQL with your session cookies; no collection names (extra.collections) "
                              "and Instagram throttles this endpoint at times - auto falls back to the browser")],
}
# auto where the library is clearly better; Instagram stays on the browser unless you pick auto/instaloader (see README)
DEFAULTS: dict[str, str] = {"x": AUTO, "instagram": PLAYWRIGHT}

# API collectors: the client library IS the collector (no browser path, nothing to choose, read-only). Shown for status only.
NATIVE: dict[str, list[BackendInfo]] = {
    "icloud": [BackendInfo("pyicloud", "icloud", "-", "pyicloud", "pyicloud", "2026.10.1", "MIT", "https://github.com/timlaing/pyicloud",
                           "iCloud web API client (Apple ID SRP sign-in + 2FA, trusted session reuse); Drive + Contacts read calls only"),
               BackendInfo("icloud-docs-mcp", "icloud", "-", "icloud-docs-mcp", "icloud_docs_mcp", "git 1fdc95a", "MIT",
                           "https://github.com/js713-lab/icloud4u-mcp",
                           "your icloud4u-mcp, used as a library: Drive walking (DriveClient) + text extraction (pdf/docx/xlsx/txt)")],
}


def libraries(platform: str) -> list[BackendInfo]:
    return list(BACKENDS.get(platform, []))


def options(platform: str) -> list[str]:
    if platform in NATIVE: return [NATIVE[platform][0].name]
    libs = [b.name for b in libraries(platform)]
    return [AUTO, PLAYWRIGHT, *libs] if libs else [PLAYWRIGHT]


def default_choice(platform: str) -> str:
    if platform in NATIVE: return NATIVE[platform][0].name
    return DEFAULTS.get(platform, AUTO if libraries(platform) else PLAYWRIGHT)


def validate(platform: str, choice) -> str:
    c = str(choice or "").strip().lower()
    if c not in options(platform):
        raise ValueError(f"backend for '{platform}' must be one of {options(platform)}")
    return c


def info(platform: str, name: str) -> BackendInfo:
    for b in libraries(platform):
        if b.name == name: return b
    raise BackendUnavailable(f"no '{name}' backend for '{platform}'")


def installed(b: BackendInfo) -> bool:
    try: return importlib.util.find_spec(b.import_name) is not None
    except (ImportError, ValueError): return False


def version(b: BackendInfo) -> Optional[str]:
    try:
        from importlib.metadata import version as v
        return v(b.dist)
    except Exception:
        return None


# ---------------------------------------------------------------- setting (DB, shared by CLI / MCP / dashboard / auto-sync)
def _db():
    from savehub import db
    return db


def _stored(read_db: bool = True) -> dict:
    if not read_db: return {}
    try:
        db = _db()
        if not db.DB_PATH.exists(): return {}            # never create a DB just to read a setting
        r = db.conn().execute("SELECT v FROM autosync_meta WHERE k=?", (META_KEY,)).fetchone()
        d = json.loads(r[0]) if r else {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def get_choice(platform: str, read_db: bool = True) -> str:
    v = _stored(read_db).get(platform)
    try: return validate(platform, v) if v else default_choice(platform)
    except ValueError: return default_choice(platform)


def set_choice(platform: str, choice) -> str:
    from ..base import REGISTRY
    if platform not in REGISTRY: raise ValueError("unknown platform")
    c = validate(platform, choice)
    cur = _stored(); cur[platform] = c
    conn = _db().conn()
    conn.execute("INSERT INTO autosync_meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (META_KEY, json.dumps(cur)))
    conn.commit()
    return c


def describe(platform: str, read_db: bool = True) -> dict:
    """Public status for list_platforms / login_status / dashboard (no secrets)."""
    choice = get_choice(platform, read_db)
    libs = [{"name": b.name, "library": b.dist, "pinned": b.pin, "installed": installed(b), "version": version(b),
             "license": b.license, "repo": b.repo, "note": b.note} for b in (NATIVE.get(platform) or libraries(platform))]
    return {"backend": choice, "default": default_choice(platform), "options": options(platform),
            "effective": plan(platform, choice), "libraries": libs,
            "unsave_backend": None if platform in NATIVE else PLAYWRIGHT, **({"native": True, "read_only": True} if platform in NATIVE else {})}


def plan(platform: str, choice: str) -> list[str]:
    """Order in which backends are tried for this choice. 'playwright' is always last for auto."""
    if platform in NATIVE: return [NATIVE[platform][0].name]
    libs = libraries(platform)
    if not libs or choice == PLAYWRIGHT: return [PLAYWRIGHT]
    if choice == AUTO: return [b.name for b in libs if installed(b)] + [PLAYWRIGHT]
    return [choice]


def load(platform: str, name: str):
    """The backend module (has collect(state, cfg, account) -> list[CollectedItem])."""
    b = info(platform, name)
    if not installed(b):
        raise BackendUnavailable(f"the '{b.name}' backend needs `pip install {b.dist}=={b.pin}` (see requirements-collector.txt)")
    return importlib.import_module(f"{__name__}.{b.module}")


# ---------------------------------------------------------------- helpers for backends
def _domain_ok(dom: str, domains) -> bool:
    dom = (dom or "").lstrip(".").lower()
    return any(dom == d or dom.endswith("." + d) for d in domains)


def cookie_dict(state: dict, domains, now: Optional[float] = None) -> dict:
    """{name: value} of the unexpired storage_state cookies for these registrable domains (in memory only).
    The most specific (longest) domain wins when a name appears twice."""
    now = time.time() if now is None else now
    best: dict = {}
    for c in (state or {}).get("cookies", []) or []:
        if not isinstance(c, dict) or not _domain_ok(c.get("domain", ""), domains): continue
        exp = c.get("expires", -1)
        if isinstance(exp, (int, float)) and exp > 0 and exp < now: continue
        name, val = c.get("name"), c.get("value")
        if not name or val is None: continue
        rank = len((c.get("domain") or "").lstrip("."))
        if name not in best or rank >= best[name][0]: best[name] = (rank, str(val))
    return {k: v for k, (_, v) in best.items()}


def require(cookies: dict, names, what: str) -> None:
    missing = [n for n in names if not cookies.get(n)]
    if missing:
        raise BackendError(f"{what}: the saved session has no {', '.join(missing)} cookie (log in again on the box)")


@contextlib.contextmanager
def private_tempdir(prefix: str = "savehub-backend-"):
    """0700 temp dir for libraries that insist on a file; everything in it is deleted in finally."""
    d = tempfile.mkdtemp(prefix=prefix)            # mkdtemp creates it 0700
    try:
        os.chmod(d, 0o700)
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def private_file(d: str, name: str) -> str:
    """Pre-create an empty 0600 file inside a private_tempdir()."""
    p = os.path.join(d, name)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    return p


def fail(name: str, e: BaseException) -> BackendError:
    """Wrap any library exception into a BackendError with a scrubbed, short message."""
    if isinstance(e, BackendError): return e
    return BackendError(f"{name}: {type(e).__name__}: {scrub(str(e))[:300]}")


def log_fallback(platform: str, name: str, err: BaseException) -> None:
    ev("backend.fallback", logging.WARNING, platform=platform, backend=name, to=PLAYWRIGHT, error=scrub(str(err))[:200])


class Accumulator:
    """De-dupe + max_items + incremental stop (same rule as Collector.incremental_done: N already-stored items seen)."""

    def __init__(self, cfg):
        self.cfg, self.items, self.seen, self.known = cfg, [], set(), 0

    def add(self, it) -> bool:
        """Add one CollectedItem; returns True when the run should stop paginating."""
        from ..base import Collector
        if it is not None and it.url:
            key = it.external_id or it.url
            if key not in self.seen and len(self.items) < self.cfg.max_items:
                self.seen.add(key); self.items.append(it)
                if Collector.is_known(self.cfg, it): self.known += 1
        return self.done()

    @property
    def incremental_stop(self) -> bool:
        return bool(self.cfg.stop_after_known and self.cfg.known and self.known >= self.cfg.stop_after_known)

    def done(self) -> bool:
        return len(self.items) >= self.cfg.max_items or self.incremental_stop
