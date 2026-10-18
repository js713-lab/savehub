"""Automatic credential login orchestrator (on the box).

The dashboard form (or the MCP connect page) saves a username + password into the encrypted vault and enqueues a login
job for <platform>@<handle>. This module runs it headlessly: fill the app's own sign-in form (login_flows), handle a
code/captcha/QR challenge by keeping the SAME browser context alive (up to ~10 min) while the user types the code on the
dashboard, save the encrypted session, and immediately trigger the first collect. Auto-sync calls try_relogin() when a
session has expired so it re-logs in from the vault automatically, only surfacing needs_code when the app challenges.

Passwords never leave this process: they go straight from the vault to the app's form. Nothing here logs or returns one.
Job state lives in DB table login_jobs so the dashboard, REST and MCP all see the same status.
Statuses: signing_in | needs_code | needs_captcha | needs_qr | wrong_password | connected | syncing | done | error | no_credentials
"""
from __future__ import annotations
import base64, logging, os, threading, time
from pathlib import Path
from typing import Optional
from . import sessions, runtime
from .base import REGISTRY
from .logutil import ev, scrub
from . import login_flows

CODE_TTL_S = 600                     # keep the login context alive this long waiting for a code (≈10 min)
JOB_FIELDS = ("status", "detail", "qr", "items_added", "updated_at", "last_attempt")
TERMINAL = {"connected", "done", "wrong_password", "error", "needs_captcha", "no_credentials"}

_PENDING: dict = {}                  # (platform, account) -> {"event": Event, "code": str|None, "deadline": float}
_THREADS: dict = {}                  # (platform, account) -> Thread
_LOCK = threading.Lock()


def _db():
    from savehub import db
    return db


def _table():
    c = _db().conn()
    c.execute("CREATE TABLE IF NOT EXISTS login_jobs(platform TEXT NOT NULL, account TEXT NOT NULL, status TEXT, detail TEXT, "
              "qr TEXT, items_added INTEGER DEFAULT 0, last_attempt REAL, updated_at REAL, PRIMARY KEY(platform, account))")
    return c


def _set(platform, account, status, detail="", qr=None, items_added=None):
    c = _table()
    c.execute("INSERT INTO login_jobs(platform, account) VALUES(?,?) ON CONFLICT(platform, account) DO NOTHING", (platform, account))
    f = {"status": status, "detail": scrub(detail)[:400], "updated_at": time.time(), "last_attempt": time.time()}
    if qr is not None: f["qr"] = qr
    if items_added is not None: f["items_added"] = int(items_added)
    c.execute(f"UPDATE login_jobs SET {', '.join(k+'=?' for k in f)} WHERE platform=? AND account=?", [*f.values(), platform, account])
    c.commit()
    ev("autologin." + status, platform=platform, account=account, detail=scrub(detail)[:120] if detail else None)


def get_job(platform, account) -> dict:
    account = sessions.account_key(account)
    r = _table().execute("SELECT * FROM login_jobs WHERE platform=? AND account=?", (platform, account)).fetchone()
    return dict(r) if r else {}


def all_jobs() -> dict:
    return {(r["platform"], r["account"]): dict(r) for r in _table().execute("SELECT * FROM login_jobs")}


def _account_id(platform, account):
    return _db().find_account(platform, account)


def _creds(platform, account):
    """(username, password) from the vault for this account, or None. username falls back to the handle."""
    from savehub import vault
    aid = _account_id(platform, account)
    if aid is None: return None
    try:
        got = vault.get_login(aid)
    except Exception as e:
        ev("autologin.vault_error", logging.WARNING, platform=platform, account=account, error=scrub(str(e))[:120]); return None
    if not got: return None
    u, p = got
    return (u or account, p)


def submit_code(platform, account, code: str) -> dict:
    """The user typed a verification code on the dashboard: hand it to the live login thread waiting for it."""
    account = sessions.account_key(account)
    code = (code or "").strip()
    if not code: return {"ok": False, "error": "empty code"}
    with _LOCK:
        slot = _PENDING.get((platform, account))
    if not slot or slot["deadline"] < time.time():
        return {"ok": False, "error": "no login is waiting for a code (it may have timed out; start the login again)"}
    slot["code"] = code
    slot["event"].set()
    return {"ok": True, "status": "signing_in"}


def _wait_for_code_factory(platform, account):
    event = threading.Event()
    slot = {"event": event, "code": None, "deadline": time.time() + CODE_TTL_S}
    with _LOCK: _PENDING[(platform, account)] = slot

    def wait():
        _set(platform, account, "needs_code", "waiting for the verification code (enter it on the dashboard)")
        got = event.wait(timeout=CODE_TTL_S)
        with _LOCK: _PENDING.pop((platform, account), None)
        return slot["code"] if got else None
    return wait


def _capture_qr(page, flow) -> str:
    try:
        loc = page.locator(flow.qr).first
        png = loc.screenshot(timeout=2000)
        return "data:image/png;base64," + base64.b64encode(png).decode()
    except Exception:
        return ""


def login_and_collect(platform, account, *, sessions_dir=None, browser=None, context_hook=None,
                      do_collect=True, collect_fn=None, icloud_factory=None, code_input=None, qr_ttl=None, qr_poll_s=2.0) -> dict:
    """Run one login (and, on success, the first collect) synchronously. Injectable for tests:
    browser/context_hook feed a fake app; collect_fn overrides the first collect; code_input() supplies an OTP."""
    account = sessions.account_key(account)
    cls = REGISTRY.get(platform)
    if cls is None: return {"status": "error", "detail": f"unknown platform '{platform}'"}
    qr_only = not login_flows.supports_password_login(platform)
    creds = None if qr_only else _creds(platform, account)
    if not creds and not qr_only:
        _set(platform, account, "no_credentials", "no saved login for this account (add it on the dashboard)")
        return {"status": "no_credentials"}
    username, password = creds or ("", "")
    from .logutil import register_secret, forget_secret
    register_secret(password)
    try:
        return _login_and_collect(platform, account, cls, username, password, qr_only, sessions_dir, browser, context_hook,
                                  do_collect, collect_fn, icloud_factory, code_input, qr_ttl, qr_poll_s)
    finally:
        forget_secret(password); password = None


def _login_and_collect(platform, account, cls, username, password, qr_only, sessions_dir, browser, context_hook,
                       do_collect, collect_fn, icloud_factory, code_input, qr_ttl, qr_poll_s) -> dict:
    injected = (browser, context_hook)
    _set(platform, account, "signing_in", qr="")
    sfile = sessions.session_path(platform, sessions_dir, account)
    if platform == "icloud":
        res = login_icloud(platform, account, username, password, sfile, code_input=code_input or _wait_for_code_factory(platform, account), factory=icloud_factory)
        password = None
        return _after_login(platform, account, res, sfile, sessions_dir, *injected, do_collect, collect_fn)
    import contextlib
    from . import runner
    flow = login_flows.FLOWS[platform]
    wait_for_code = code_input or _wait_for_code_factory(platform, account)
    with contextlib.ExitStack() as stack:
        if browser is None:
            pw = stack.enter_context(runner._require_playwright()())
            browser = runner.launch_browser(pw, headful=os.environ.get("SAVEHUB_HEADFUL", "").lower() in ("1", "true", "yes")); stack.callback(browser.close)
        prev = None
        if sfile.exists():
            try: prev = sessions.read_state(sfile)
            except Exception: prev = None
        ctx = runner.new_context(browser, prev); stack.callback(ctx.close)
        if context_hook: context_hook(ctx)
        page = ctx.new_page(); page.set_default_timeout(45000)
        try:
            st, detail, data = login_flows.run_browser_login(page, platform, username, password, wait_for_code=wait_for_code)
        except Exception as e:
            st, detail, data = "error", scrub(str(e))[:200], {}
        password = None
        qr = ""
        if st == "needs_qr":
            # QR sign-in (WeChat / WhatsApp / Douyin): show the live QR on the dashboard and keep this context open
            # until the phone scans it (refreshing the image), up to CODE_TTL_S
            deadline = time.time() + (qr_ttl if qr_ttl is not None else CODE_TTL_S)
            while time.time() < deadline:
                qr = _capture_qr(page, flow) or qr
                _set(platform, account, "needs_qr", "scan this QR code with the app on your phone", qr=qr)
                s2, d2, _ = login_flows._classify(page, flow)
                if s2 == "connected": st, detail = "connected", ""; break
                page.wait_for_timeout(int(qr_poll_s * 1000))
            else:
                _set(platform, account, "error", "the QR code was not scanned in time; start the sign-in again", qr="")
                return {"status": "error", "detail": "qr timeout"}
        if st == "connected":
            sessions.save_state(ctx, sfile, getattr(cls, "needs_indexed_db", False))
            _set(platform, account, "connected", "signed in; session saved", qr="")
        if st in login_flows.CHALLENGE and st != "needs_code":
            _set(platform, account, st, detail, qr=qr); return {"status": st, "detail": detail}
        if st != "connected":
            try:
                shot = Path(sessions_dir) / f"{platform}@{account}.login-debug.png"
                page.screenshot(path=str(shot), full_page=True)
            except Exception: pass
            return _after_login(platform, account, {"status": st, "detail": detail}, sfile, sessions_dir, *injected, do_collect, collect_fn)
    return _after_login(platform, account, {"status": "connected"}, sfile, sessions_dir, *injected, do_collect, collect_fn)


def login_icloud(platform, account, apple_id, password, sfile, *, code_input=None, factory=None) -> dict:
    """Non-interactive pyicloud login: password + (if challenged) a 2FA code typed on the dashboard. Reuses the
    iCloud collector's own sign-in (SRP + trust); the password only goes to Apple, never stored."""
    from .platforms import icloud as ic
    col = REGISTRY["icloud"]()
    def ask_password(_p=""): return password
    try:
        # terminal_login expects a tty; call its internals directly for the non-interactive path
        if not sessions.encryption_enabled():
            return {"status": "error", "detail": "iCloud login needs the vault key configured (encrypted session)"}
        fac = factory or ic._service_factory()
        china = False
        with ic.private_workdir() as d:
            try:
                api = fac(apple_id, password, cookie_directory=str(d), accept_terms=False, china_mainland=china)
            except Exception as e:
                if ic.is_auth_error(e): return {"status": "wrong_password", "detail": "Apple refused the Apple ID or password"}
                return {"status": "error", "detail": ic._short(e, [password])[:200]}
            if getattr(api, "requires_2fa", False):
                try: api.request_2fa_code()
                except Exception: pass
                code = (code_input() if code_input else None)
                if not code: return {"status": "needs_code", "detail": "Apple sent a 2FA code to your trusted devices"}
                try: ok = bool(api.validate_2fa_code(str(code).strip()))
                except Exception: ok = False
                if not ok: return {"status": "wrong_password", "detail": "the 2FA code was not accepted"}
                if not getattr(api, "is_trusted_session", False):
                    try: api.trust_session()
                    except Exception as e: return {"status": "error", "detail": ic._short(e, [password])[:200]}
            elif getattr(api, "requires_2sa", False):
                return {"status": "error", "detail": "this Apple ID uses legacy two-step verification; switch to 2FA"}
            state = ic.snapshot(d, apple_id, china, {})
            sessions.write_state(sfile, state)
        return {"status": "connected"}
    finally:
        password = None


def _after_login(platform, account, res, sfile, sessions_dir, browser, context_hook, do_collect, collect_fn) -> dict:
    st = res.get("status")
    if st == "needs_code":           # the code was never entered (timed out): the live context is gone -> fall back
        _set(platform, account, "error", "no verification code was entered within 10 minutes; start the sign-in again "
             f"(or log in on the box: python -m savehub.collector login {platform} --account @{account})")
        return {"status": "error", "detail": "code timeout"}
    if st in login_flows.CHALLENGE:
        _set(platform, account, st, res.get("detail", "")); return res
    if st != "connected":
        _set(platform, account, "wrong_password" if st == "wrong_password" else "error", res.get("detail", "")); return res
    _set(platform, account, "connected", "signed in; session saved")
    if not do_collect:
        return {"status": "connected"}
    _set(platform, account, "syncing", "collecting your saved items")
    try:
        if collect_fn is not None:
            out = collect_fn(platform, account)
        else:
            out = _default_collect(platform, account, sessions_dir, browser, context_hook)
        added = int((out or {}).get("created") or (out or {}).get("added") or 0)
        _set(platform, account, "done", f"connected · {added} new item(s)", items_added=added)
        return {"status": "done", "items_added": added}
    except Exception as e:
        _set(platform, account, "connected", "signed in; first sync failed: " + scrub(str(e))[:150])
        return {"status": "connected", "detail": "first sync failed"}


def _default_collect(platform, account, sessions_dir, browser, context_hook) -> dict:
    """First collect after a successful login. Browser platforms via the subprocess auto-sync path (fresh browser);
    tests inject browser+context_hook and we run in-process so the fake app is reachable."""
    from . import autosync
    if browser is not None or context_hook is not None:
        from .base import RunConfig
        from .client import SqliteSink
        from . import runner
        sink = SqliteSink(str(_db().DB_PATH))
        try:
            cfg = RunConfig(account_handle="" if account == sessions.DEFAULT_ACCOUNT else account, max_items=100)
            return runner.run_collect(platform, cfg, sink, sessions_dir, browser=browser, context_hook=context_hook)
        finally: sink.close()
    eng = autosync.Engine(sessions_dir=sessions_dir)
    return eng.run_account(platform, account, "autologin")


def enqueue(platform, account, sessions_dir=None, do_collect=True, **kw) -> dict:
    """Start a login job in a background thread (dashboard/MCP). Returns immediately with status signing_in.
    The thread keeps its browser context alive while it waits for a code (needs_code), up to CODE_TTL_S."""
    account = sessions.account_key(account)
    cls = REGISTRY.get(platform)
    if cls is None: return {"status": "error", "detail": "unknown platform"}
    if login_flows.supports_password_login(platform) and _creds(platform, account) is None:
        _set(platform, account, "no_credentials", "no saved login for this account"); return {"status": "no_credentials"}
    key = (platform, account)
    with _LOCK:
        t = _THREADS.get(key)
        if t and t.is_alive(): return {"status": get_job(platform, account).get("status") or "signing_in", "already": True}
    _set(platform, account, "signing_in", qr="")

    def work():
        try: login_and_collect(platform, account, sessions_dir=sessions_dir, do_collect=do_collect, **kw)
        except Exception as e: _set(platform, account, "error", scrub(str(e))[:200])
        finally:
            with _LOCK: _THREADS.pop(key, None)
    th = threading.Thread(target=work, name=f"savehub-login-{platform}-{account}", daemon=True)
    with _LOCK: _THREADS[key] = th
    th.start()
    return {"status": "signing_in"}


def wait_job(platform, account, timeout=60.0, until=("connected", "done", "wrong_password", "error", "needs_code",
                                                       "needs_captcha", "needs_qr", "no_credentials")) -> str:
    end = time.time() + timeout
    while time.time() < end:
        st = get_job(platform, account).get("status") or ""
        if st in until: return st
        time.sleep(0.2)
    return get_job(platform, account).get("status") or ""


RELOGIN_WAIT_S = 120


def try_relogin(platform, account, sessions_dir=None, **kw) -> Optional[str]:
    """Called by auto-sync when a session expired / was rejected: with vault credentials, re-login automatically.
    Runs in the background job thread (so a code challenge keeps waiting for the dashboard without blocking the loop)
    and waits up to RELOGIN_WAIT_S for an outcome. Returns the status, or None when there are no credentials."""
    account = sessions.account_key(account)
    if _creds(platform, account) is None: return None
    ev("autologin.relogin", platform=platform, account=account)
    r = enqueue(platform, account, sessions_dir, do_collect=False, **kw)
    if r.get("status") == "no_credentials": return None
    return wait_job(platform, account, RELOGIN_WAIT_S)


def is_active(platform, account) -> bool:
    with _LOCK:
        t = _THREADS.get((platform, sessions.account_key(account)))
        return bool(t and t.is_alive())


LABELS = {"signing_in": "Signing in…", "connected": "Connected", "done": "Connected", "syncing": "Syncing",
          "needs_code": "Needs code", "needs_captcha": "Needs captcha", "needs_qr": "Scan QR code",
          "wrong_password": "Wrong password", "error": "Error", "no_credentials": "No saved login"}


def account_status(platform, account) -> dict:
    """Dashboard / MCP view of one account: login job + session + auto-sync state. Never includes credentials."""
    from . import autosync
    from savehub import vault
    account = sessions.account_key(account)
    job = get_job(platform, account)
    st = autosync.get_state(platform, account)
    sess = sessions.describe(sessions.session_path(platform, None, account))
    aid = _account_id(platform, account)
    status = job.get("status") or ""
    running = bool(st.get("running"))
    if running: status = "syncing"
    elif not status or status in ("connected", "done"):
        if sess.get("status") == "saved":
            status = "connected" if st.get("status") in (None, "ok") else ("error" if st.get("status") == "error" else "connected")
            if st.get("status") == "needs_login": status = "needs_login"
        elif sess.get("status") == "expired": status = "needs_login"
        elif not status: status = "not_connected"
    label = LABELS.get(status, {"needs_login": "Needs sign-in", "not_connected": "Not connected"}.get(status, status))
    out = {"platform": platform, "account": "@" + account if account != sessions.DEFAULT_ACCOUNT else account, "account_id": aid,
           "status": status, "label": label, "detail": (job.get("detail") or st.get("last_error") or "") if status not in ("connected",) else (job.get("detail") or ""),
           "has_login": bool(aid and vault.has_login(aid)), "session": sess.get("status"),
           "last_sync": autosync._iso(st.get("last_success")), "items_added_total": int(st.get("total_added") or 0) + int(job.get("items_added") or 0),
           "has_qr": bool(job.get("qr")) and status == "needs_qr", "password_login": login_flows.supports_password_login(platform)}
    if status == "needs_code": out["code_url"] = f"/#/accounts?code={platform}@{account}"
    return out


def qr_image(platform, account) -> str:
    job = get_job(platform, account)
    return job.get("qr") or "" if job.get("status") == "needs_qr" else ""
