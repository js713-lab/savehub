"""Collector base class, run config, result types and the platform registry."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("savehub.collector")

# exit codes used by the CLI
EXIT_OK, EXIT_ERROR, EXIT_NEED_LOGIN, EXIT_NOT_IMPLEMENTED, EXIT_PUSH_FAILED = 0, 1, 2, 3, 4
EXIT_BUSY, EXIT_LOCKED = 5, 6      # another run holds this account's lock | encrypted session and no/wrong key


class CollectorError(Exception):
    exit_code = EXIT_ERROR


class NeedLogin(CollectorError):
    """The saved session is missing/expired: the app redirected to its login page."""
    exit_code = EXIT_NEED_LOGIN

    def __init__(self, platform: str, session_file: Path | str, detail: str = "", account: str = ""):
        self.platform, self.session_file, self.detail = platform, str(session_file), detail
        self.account = account if account and account != "default" else ""
        self.fix = f"python -m savehub.collector login {platform}" + (f" --account @{self.account}" if self.account else "")
        who = f"'{platform}' account @{self.account}" if self.account else f"'{platform}'"
        super().__init__(f"need login for {who}: {detail or 'not logged in'}. "
                         f"Create/refresh the session file {self.session_file} with: {self.fix}")


class NotImplementedPlatform(CollectorError, NotImplementedError):
    exit_code = EXIT_NOT_IMPLEMENTED


class AccountBusy(CollectorError):
    """Another collect run (any process: CLI, MCP, auto-sync) holds this account's lock."""
    exit_code = EXIT_BUSY


@dataclass
class CollectedItem:
    """One saved item scraped from an app (platform-agnostic)."""
    platform: str
    url: str
    external_id: str = ""
    author: str = ""
    author_handle: str = ""
    text: str = ""
    posted_at: str = ""          # when the post was published (ISO 8601) if the app shows it
    has_media: bool = False
    media_url: str = ""
    extra: dict = field(default_factory=dict)
    source_account: str = ""     # account key (handle w/o '@') of the session it was collected with; "" = default session
    title: str = ""              # explicit title (e.g. note/link title, contact name); default = first 120 chars of text

    def to_savehub(self, account_id: Optional[int] = None, collected_at: str = "") -> dict:
        """Payload for POST /api/items (also accepted by db.add_item)."""
        title = " ".join(self.title.split())[:120] or " ".join(self.text.split())[:120] or (f"@{self.author_handle} on {self.platform}" if self.author_handle else self.url)
        d = {"platform": self.platform, "url": self.url, "title": title, "content": self.text,
             "author": self.author, "author_handle": self.author_handle, "media_url": self.media_url or None,
             "external_id": self.external_id,
             "source": f"collector:{self.platform}" + (f":{self.source_account}" if self.source_account else ""),
             "raw": {"posted_at": self.posted_at, "has_media": self.has_media, "collected_at": collected_at, **self.extra,
                     **({"source_account": "@" + self.source_account} if self.source_account else {})}}
        if collected_at: d["saved_at"] = collected_at
        if account_id: d["account_id"] = int(account_id)
        return d

    def as_dict(self): return asdict(self)


@dataclass
class RunConfig:
    """Per-run options. Un-save is OFF by default and, even when on, DRY-RUN unless unsave_confirm is true."""
    max_items: int = 200            # hard cap of items collected per run
    scroll_delay: float = 1.8       # seconds between scrolls (plus jitter) - be polite
    scroll_jitter: float = 0.8
    idle_rounds: int = 4            # stop after this many scrolls without new items
    max_scrolls: int = 400          # absolute safety stop
    page_timeout_ms: int = 45000
    headful: bool = False
    unsave: bool = False            # opt-in: un-bookmark items on the app after they are confirmed stored
    unsave_confirm: bool = False    # opt-in #2: without it unsave only LOGS what it would do (dry run)
    max_unsave: int = 50            # cap of real un-saves per run
    unsave_delay: float = 2.5       # seconds between un-saves
    account_id: Optional[int] = None    # SaveHub account row to link items to (default: resolved/created from account_handle)
    account_handle: str = ""            # which logged-in account to collect from: selects session <platform>@<handle>.json,
                                        # tags items source=collector:<platform>:<handle> and links them to that account row
    known: set = field(default_factory=set, repr=False)   # incremental mode: urls + "id:<external_id>" already stored
    stop_after_known: int = 0           # incremental mode: stop paginating once this many already-stored items were seen (0 = off)

    @classmethod
    def from_dict(cls, d: dict | None) -> "RunConfig":
        d = d or {}
        kv = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        unknown = set(d) - set(kv)
        if unknown: log.warning("ignoring unknown config keys: %s", sorted(unknown))
        if "known" in kv: kv["known"] = set(kv["known"] or ())
        return cls(**kv)

    @property
    def unsave_mode(self) -> str:
        if not self.unsave: return "off"
        return "live" if self.unsave_confirm else "dry-run"


class Collector:
    """Base class. Subclass, set the class attributes and implement the four page-level methods.

    The runner (savehub.collector.runner) owns the browser/session; collectors only drive a Playwright `page`.
    """
    platform: str = ""                 # SaveHub platform id (must exist in db.PLATFORMS)
    name: str = ""
    implemented: bool = True
    home_url: str = ""                 # page opened by `login`
    login_url: str = ""
    saved_url: str = ""                # the saved / bookmarks page
    notes: str = ""                    # TODO notes for stubs / caveats for implemented collectors
    kind: str = "saves"                # "saves" (saved/bookmarked posts) | "contacts" (chat/contact list collector)
    supports_unsave: bool = True       # False: the app has no un-save action for what is collected (--unsave refused)
    needs_indexed_db: bool = False     # True: the login lives in IndexedDB (e.g. WhatsApp Web) -> saved with the session

    def is_login_page(self, page) -> bool:
        raise NotImplementedError

    def is_logged_in(self, page) -> bool:
        """Positive signal that the session is authenticated (used by the `login` command)."""
        raise NotImplementedError

    def collect(self, page, cfg: RunConfig, session_file="") -> list[CollectedItem]:
        """Open saved_url, scroll politely, return de-duplicated items (at most cfg.max_items)."""
        raise NotImplementedError

    def unsave(self, page, item: CollectedItem, cfg: RunConfig, session_file="") -> str:
        """Un-save one item on the app. Return 'unsaved' only if the app confirms it is no longer saved,
        'already_unsaved' if it was not saved, else 'failed'."""
        raise NotImplementedError

    # ---- incremental mode (auto-sync): stop paginating once already-stored items show up ----
    @staticmethod
    def is_known(cfg: RunConfig, it: CollectedItem) -> bool:
        if not cfg.known: return False
        return it.url in cfg.known or bool(it.external_id and f"id:{it.external_id}" in cfg.known)

    def incremental_done(self, cfg: RunConfig, items) -> bool:
        """True once >= cfg.stop_after_known of `items` are already stored (saved lists are newest-first)."""
        if not cfg.stop_after_known or not cfg.known: return False
        return sum(1 for it in items if self.is_known(cfg, it)) >= cfg.stop_after_known

    def ensure_logged_in(self, page, session_file) -> None:
        if self.is_login_page(page):
            raise NeedLogin(self.platform, session_file, f"redirected to login page ({page.url})")

    def describe(self) -> dict:
        return {"platform": self.platform, "name": self.name, "implemented": self.implemented, "kind": self.kind,
                "supports_unsave": self.supports_unsave and self.implemented,
                "saved_url": self.saved_url, "login_url": self.login_url, "notes": self.notes}


class StubCollector(Collector):
    """Placeholder for platforms that are not implemented yet: every action raises a clear error."""
    implemented = False

    def _nope(self, *_a, **_k):
        raise NotImplementedPlatform(
            f"The '{self.platform}' collector is not implemented yet "
            f"(implemented: {', '.join(k for k, c in sorted(REGISTRY.items()) if c.implemented)}). "
            f"Saved-items page: {self.saved_url or 'n/a'}. TODO: {self.notes}")

    is_login_page = is_logged_in = collect = unsave = _nope


REGISTRY: dict[str, type[Collector]] = {}


def register(cls: type[Collector]) -> type[Collector]:
    if not cls.platform: raise ValueError("collector needs a platform id")
    REGISTRY[cls.platform] = cls
    return cls


def get_collector(platform: str) -> Collector:
    try: return REGISTRY[platform]()
    except KeyError:
        raise CollectorError(f"unknown platform '{platform}'. Known: {', '.join(sorted(REGISTRY))}") from None


def list_platforms() -> list[dict[str, Any]]:
    return [REGISTRY[k]().describe() for k in sorted(REGISTRY)]
