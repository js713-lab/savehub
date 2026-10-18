"""Orchestrates a collect run: browser + saved session -> collector -> sink -> (optional) un-save."""
from __future__ import annotations
import contextlib, logging, os, shutil, time
from datetime import datetime, timezone
from pathlib import Path
from .base import CollectorError, NeedLogin, NotImplementedPlatform, RunConfig, get_collector
from .logutil import ev, scrub
from . import sessions, runtime

SYSTEM_CHROMES = ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser"]
LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        raise CollectorError("Playwright is not installed. Run: pip install -r requirements-collector.txt && playwright install chromium") from None


def launch_browser(pw, headful=False):
    """Bundled Playwright Chromium first; fall back to a system Chrome/Chromium (or $SAVEHUB_CHROME)."""
    args = LAUNCH_ARGS + (["--no-sandbox"] if hasattr(os, "geteuid") and os.geteuid() == 0 else [])
    if headful and not os.environ.get("DISPLAY") and os.name == "posix" and not os.uname().sysname == "Darwin":
        raise CollectorError("--headful/login needs a display (DISPLAY is not set). Run it on a desktop session.")
    exe = os.environ.get("SAVEHUB_CHROME")
    if exe: return pw.chromium.launch(executable_path=exe, headless=not headful, args=args)
    try:
        return pw.chromium.launch(headless=not headful, args=args)
    except Exception as e:
        if "Executable doesn't exist" not in str(e) and "executable" not in str(e).lower(): raise
        for c in SYSTEM_CHROMES:
            if shutil.which(c) or os.path.exists(c):
                ev("browser.fallback", executable=c); return pw.chromium.launch(executable_path=c, headless=not headful, args=args)
        raise CollectorError("No Chromium found. Run: playwright install chromium") from e


def new_context(browser, storage_state=None):
    """storage_state: a dict (decrypted in memory by sessions.read_state) or None. Never a plaintext temp file."""
    tmp = browser.new_page(); ua = tmp.evaluate("navigator.userAgent"); tmp.context.close()
    # drop the "HeadlessChrome" token some sites block
    return browser.new_context(storage_state=storage_state, user_agent=ua.replace("HeadlessChrome", "Chrome"),
                               viewport={"width": 1280, "height": 1000}, locale="en-US")


def run_collect(platform: str, cfg: RunConfig, sink=None, sessions_dir=None, browser=None, context_hook=None) -> dict:
    """Run one collection. `sink=None` = scrape only (no push, therefore no un-save).
    `browser` / `context_hook(ctx)` let tests inject a browser and route network to fixtures."""
    col = get_collector(platform)
    if not col.implemented: col.collect(None, cfg)          # raises NotImplementedPlatform with the TODO
    try: akey = sessions.account_key(cfg.account_handle)
    except ValueError as e: raise CollectorError(str(e)) from None
    acct = "" if akey == sessions.DEFAULT_ACCOUNT else akey
    sfile = sessions.session_path(platform, sessions_dir, akey)
    if not sfile.exists(): raise NeedLogin(platform, sfile, "no saved session file", acct)
    if cfg.unsave and not col.supports_unsave:
        raise CollectorError(f"'{platform}' has no un-save action (its collector gathers {col.kind}); drop --unsave")
    if cfg.unsave and sink is None: raise CollectorError("--unsave needs a sink (it only un-saves items confirmed stored in SaveHub)")
    if cfg.unsave and cfg.stop_after_known: raise CollectorError("incremental mode never un-saves (drop --unsave or --incremental)")
    with runtime.account_lock(platform, akey, sessions_dir):           # one run per account, across processes
        return _run_collect_locked(col, platform, cfg, sink, sfile, acct, browser, context_hook)


def choose_plan(platform: str, cfg: RunConfig) -> tuple:
    """-> (choice, [backend names to try]). Un-save runs always use the Playwright collector (libraries never un-save)."""
    from . import backends
    choice = backends.validate(platform, cfg.backend) if cfg.backend else backends.get_choice(platform)
    if cfg.unsave:
        if choice not in (backends.AUTO, backends.PLAYWRIGHT): ev("backend.unsave_uses_playwright", requested=choice)
        return choice, [backends.PLAYWRIGHT]
    return choice, backends.plan(platform, choice)


def _library_collect(platform, choice, names, state, cfg, acct, summary):
    """Try the library backends in order. Returns items, or None = continue with Playwright (auto fallback)."""
    from . import backends
    for name in names:
        if name == backends.PLAYWRIGHT: return None
        try:
            mod = backends.load(platform, name)
            ev("backend.start", platform=platform, backend=name)
            items = mod.collect(state, cfg, acct)
            summary["backend"] = name
            return items
        except Exception as e:
            err = backends.fail(name, e)
            if choice != backends.AUTO:                         # explicitly chosen library: its error is the result
                raise CollectorError(str(err)) from None
            backends.log_fallback(platform, name, err)
            summary.setdefault("fallback", []).append({"backend": name, "error": scrub(str(err))[:300]})
    return None


def _run_collect_locked(col, platform, cfg, sink, sfile, acct, browser, context_hook) -> dict:
    state = sessions.read_state(sfile)                              # decrypts in memory; migrates plaintext -> encrypted
    try: choice, names = choose_plan(platform, cfg)
    except ValueError as e: raise CollectorError(str(e)) from None
    if not cfg.backend and (browser is not None or context_hook is not None):
        names = ["playwright"]                  # an injected browser / network hook (tests, fixtures) means: use the browser
    started = time.time(); collected_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    summary = {"platform": platform, "account": ("@" + acct) if acct else sessions.DEFAULT_ACCOUNT, "account_id": None,
               "status": "ok", "session_file": str(sfile), "unsave_mode": cfg.unsave_mode,
               "collected": 0, "created": 0, "duplicates": 0, "push_failed": 0, "unsave": {}, "items": [],
               "backend": "playwright", "backend_setting": choice,
               **({"incremental": True, "known_skipped": 0} if cfg.stop_after_known else {})}
    ev("run.start", platform=platform, account=summary["account"], max_items=cfg.max_items, unsave=cfg.unsave_mode, sink=getattr(sink, "kind", "none"))
    if sink is not None:
        h = sink.check(); ev("savehub.ok", items=(h.get("stats") or {}).get("items"))
        if cfg.account_id is None and acct:
            # link to the dashboard's account row for this handle; create it (connected_via=collector) if missing
            ensure = getattr(sink, "ensure_account", None) or sink.resolve_account
            cfg.account_id = ensure(platform, acct)
            if cfg.account_id is None: ev("account.not_found", logging.WARNING, handle=acct, note="items stored without account link (still tagged in source)")
            else: ev("account.linked", handle=acct, account_id=cfg.account_id)
    summary["account_id"] = cfg.account_id
    if not getattr(col, "uses_browser", True):                          # API collector (iCloud): no browser, never un-saves
        summary["backend"] = summary["backend_setting"] = getattr(col, "backend", "api")
        if sink is not None and hasattr(sink, "known_keys"):            # every stored version, not only the newest page:
            # iCloud file/contact versions are unique per library URL (UNIQUE platform+url): use every stored version so a
            # second Apple ID does not re-download the same unchanged files. Social apps stay per-account (see cli).
            cfg.known = sink.known_keys(platform, limit=200000, account="" if platform == "icloud" else acct)
        items, info = col.collect_api(state, cfg, sfile, acct)          # refreshes (or marks rejected) the session file itself
        summary.update({k: v for k, v in info.items() if k != "backend"})
        _push(col, items, cfg, sink, acct, collected_at, summary)
        state = None
        summary["elapsed_s"] = round(time.time() - started, 1)
        ev("run.done", **{k: v for k, v in summary.items() if k not in ("items",)})
        return summary
    items = _library_collect(platform, choice, names, state, cfg, acct, summary)
    if items is not None:                                               # library backend: no browser, never un-saves
        _push(col, items, cfg, sink, acct, collected_at, summary)
    else:
        with contextlib.ExitStack() as stack:
            if browser is None:
                pw = stack.enter_context(_require_playwright()())
                browser = launch_browser(pw, cfg.headful); stack.callback(browser.close)
            ctx = new_context(browser, state); stack.callback(ctx.close)
            if context_hook: context_hook(ctx)
            page = ctx.new_page(); page.set_default_timeout(cfg.page_timeout_ms)
            items = col.collect(page, cfg, sfile)
            results = _push(col, items, cfg, sink, acct, collected_at, summary)
            if cfg.unsave:
                summary["unsave"] = _unsave(col, page, [r for r in results if r.confirmed and not r.error and r.id], cfg, sfile, sink)
            sessions.save_state(ctx, sfile, col.needs_indexed_db)                   # keep refreshed cookies for next run
    state = None
    summary["elapsed_s"] = round(time.time() - started, 1)
    ev("run.done", **{k: v for k, v in summary.items() if k not in ("items",)})
    return summary


def _push(col, items, cfg, sink, acct, collected_at, summary) -> list:
    for it in items: it.source_account = acct               # attribution: source=collector:<platform>:<account>
    summary["collected"] = len(items)
    ev("collect.done", count=len(items), backend=summary.get("backend"))
    if cfg.stop_after_known:                                       # incremental: only push what is not stored yet
        new = [it for it in items if not col.is_known(cfg, it)]
        summary["known_skipped"] = len(items) - len(new); items = new
    results = []
    if sink is not None:
        for it in items:
            r = sink.push(it, cfg.account_id, collected_at); results.append(r)
            if r.error: summary["push_failed"] += 1; ev("push.failed", logging.WARNING, url=it.url, error=r.error)
            elif r.created: summary["created"] += 1
            elif getattr(r, "updated", False): summary["updated"] = summary.get("updated", 0) + 1   # changed file/contact refreshed
            else: summary["duplicates"] += 1
    summary["items"] = [{"url": it.url, "author_handle": it.author_handle, "posted_at": it.posted_at,
                         "has_media": it.has_media, "text": it.text[:140],
                         **({"savehub_id": r.id, "created": r.created, "confirmed": r.confirmed} if r else {})}
                        for it, r in zip(items, results or [None] * len(items))]
    return results


UNSAVE_STATES = ("unsaved", "already_unsaved", "failed")


def _unsave(col, page, confirmed, cfg, sfile, sink=None) -> dict:
    """Only items confirmed stored in SaveHub reach here (pushed, then read back). Dry run unless cfg.unsave_confirm.
    A failed un-save never touches the stored item; the outcome is recorded (summary + the item's raw.unsave)."""
    out = {"mode": cfg.unsave_mode, "would_unsave": [], "unsaved": [], "already_unsaved": [], "failed": [], "skipped_cap": 0, "errors": {}}
    record = getattr(sink, "record_unsave", None)

    def note(r, st, err=""):
        if record is None: return
        try: record(r.id, st, err)
        except Exception as e: ev("unsave.record_failed", logging.WARNING, savehub_id=r.id, error=scrub(str(e))[:200])

    for i, r in enumerate(confirmed):
        url = r.item.url
        if not cfg.unsave_confirm:
            out["would_unsave"].append(url); ev("unsave.dry_run", url=url, savehub_id=r.id, note="pass --unsave-confirm to actually un-save")
            continue
        if len(out["unsaved"]) + len(out["failed"]) >= cfg.max_unsave:
            out["skipped_cap"] = len(confirmed) - i; ev("unsave.cap_reached", max_unsave=cfg.max_unsave); break
        err = ""
        try: st = col.unsave(page, r.item, cfg, sfile)
        except NeedLogin as e:
            # the app logged us out mid-way: everything is still stored; stop un-saving, the run reports need_login
            out["failed"].append(url); out["errors"][url] = "need login"; note(r, "failed", "need login")
            out["skipped_cap"] = len(confirmed) - i - 1
            raise
        except Exception as e:
            st, err = "failed", scrub(f"{type(e).__name__}: {e}")[:200]; ev("unsave.error", logging.WARNING, url=url, error=err)
        if st not in UNSAVE_STATES:
            err = err or f"unexpected un-save result {st!r}"; st = "failed"
        out[st].append(url)
        if err: out["errors"][url] = err
        note(r, st, err)
        ev("unsave." + st, url=url, savehub_id=r.id)
        page.wait_for_timeout(int(1000 * cfg.unsave_delay))
    if not out["errors"]: out.pop("errors")
    return out


def run_login(platform: str, sessions_dir=None, timeout_s: int = 600, wait_enter: bool = False, browser=None, context_hook=None,
              account: str = "") -> dict:
    """Open a HEADFUL browser at the platform's login page so a human can log in; save storage_state when done.
    Finishes when the collector detects a signed-in session (or Enter is pressed with wait_enter).
    `account` (handle) stores the session as <platform>@<handle>.json so several accounts can stay logged in."""
    col = get_collector(platform)
    if not col.implemented: col.is_logged_in(None)           # raises NotImplementedPlatform
    try: akey = sessions.account_key(account)
    except ValueError as e: raise CollectorError(str(e)) from None
    sfile = sessions.session_path(platform, sessions_dir, akey)
    if getattr(col, "login_mode", "browser") == "terminal":
        # no web-cookie flow (iCloud): the collector prompts in this terminal (account id, password, 2FA code). The password
        # only goes to the app's own sign-in; just the resulting trusted session is saved (encrypted with the vault key).
        info = col.terminal_login(sfile, akey)
        ev("login.saved", platform=platform, account=akey, session_file=str(sfile))
        return {"platform": platform, "account": akey if akey == sessions.DEFAULT_ACCOUNT else "@" + akey, **sessions.describe(sfile),
                **{k: v for k, v in (info or {}).items() if k in ("drive_check",)}, "status": "logged_in"}
    with contextlib.ExitStack() as stack:
        if browser is None:
            pw = stack.enter_context(_require_playwright()())
            browser = launch_browser(pw, headful=True); stack.callback(browser.close)
        try: prev = sessions.read_state(sfile) if sfile.exists() else None
        except sessions.SessionLocked: prev = None                  # unreadable old session: start a fresh login
        ctx = new_context(browser, prev); stack.callback(ctx.close)
        if context_hook: context_hook(ctx)
        page = ctx.new_page()
        page.goto(col.login_url or col.home_url, wait_until="domcontentloaded")
        ev("login.waiting", platform=platform, url=page.url, timeout_s=timeout_s,
           note="log in in the opened browser window (2FA ok); the session is saved automatically")
        deadline, ok = time.time() + timeout_s, False
        if wait_enter:
            input("Press Enter here after you have logged in... ")
            ok = col.is_logged_in(page)
        else:
            while time.time() < deadline:
                if col.is_logged_in(page): ok = True; break
                page.wait_for_timeout(2000)
        if not ok:
            raise NeedLogin(platform, sfile, "login not detected before timeout; nothing saved", akey)
        page.wait_for_timeout(3000)                            # let the app finish setting cookies
        sessions.save_state(ctx, sfile, col.needs_indexed_db)
    ev("login.saved", platform=platform, account=akey, session_file=str(sfile))
    return {"platform": platform, "account": akey if akey == sessions.DEFAULT_ACCOUNT else "@" + akey, **sessions.describe(sfile), "status": "logged_in"}
