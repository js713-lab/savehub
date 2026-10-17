"""CLI:  python -m savehub.collector {platforms,sessions,login,login-terminal,collect,autosync} ...

Exit codes: 0 ok · 1 error · 2 need login (session missing/expired) · 3 platform not implemented · 4 SaveHub push failed
            · 5 busy (another run holds this account) · 6 session locked (encrypted, no/wrong vault key).
The final result is printed to stdout as JSON; logs go to stderr.
"""
from __future__ import annotations
import argparse, json, logging, os, sys
from .base import CollectorError, NeedLogin, AccountBusy, RunConfig, EXIT_ERROR, EXIT_OK, EXIT_PUSH_FAILED, list_platforms
from .logutil import ev, scrub, setup
from . import sessions, platforms  # noqa: F401


def build_parser():
    p = argparse.ArgumentParser(prog="python -m savehub.collector",
                                description="Local browser collector for SaveHub (needs Playwright + Chromium; does not run on Render).")
    p.add_argument("--log-level", default=os.environ.get("SAVEHUB_LOG_LEVEL", "INFO"))
    p.add_argument("--log-json", action="store_true", help="structured JSON log lines on stderr")
    p.add_argument("--sessions-dir", help="dir for <platform>.json sessions (default data/sessions or $SAVEHUB_SESSIONS_DIR)")
    p.add_argument("--key-stdin", action="store_true", help=argparse.SUPPRESS)   # internal: vault key on stdin (never env/argv)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("platforms", help="list collectors and whether they are implemented")
    ss = sub.add_parser("sessions", help="list saved browser sessions per platform/account (metadata only)")
    ss.add_argument("platform", nargs="?", default=None)

    lg = sub.add_parser("login", help="open a headful browser so you can log in; saves the session")
    lg.add_argument("platform")
    lg.add_argument("--account", default="", help="handle of the account you are logging into (e.g. @me); saves <platform>@<handle>.json "
                                              "so several accounts per app can stay logged in. Omit = legacy single session")
    lg.add_argument("--timeout", type=int, default=600, help="seconds to wait for the login (default 600)")
    lg.add_argument("--wait-enter", action="store_true", help="wait for Enter in this terminal instead of auto-detecting")

    lt = sub.add_parser("login-terminal", help="open a terminal WINDOW on the box desktop running a terminal-mode login (icloud), "
                                               "to hand to the user like the browser logins; the password is typed only in that window")
    lt.add_argument("platform")
    lt.add_argument("--account", default="", help="handle for this login (e.g. @js.yip)")
    lt.add_argument("--display", help="X display of the desktop the user sees (default $DISPLAY)")
    lt.add_argument("--terminal", help="terminal emulator to use (default: first of xterm, xfce4-terminal, x-terminal-emulator, ...)")

    c = sub.add_parser("collect", help="scrape saved items and push them to SaveHub")
    c.add_argument("platform")
    c.add_argument("--config", help="JSON file with per-run options (keys of RunConfig, e.g. max_items, unsave)")
    c.add_argument("--max-items", type=int)
    c.add_argument("--scroll-delay", type=float)
    c.add_argument("--headful", action="store_true", default=None, help="show the browser window")
    c.add_argument("--sink", choices=["rest", "sqlite"], default="rest", help="rest (default) = POST to SaveHub; sqlite = local DB")
    c.add_argument("--savehub-url", help="SaveHub base URL (default $SAVEHUB_URL or http://127.0.0.1:8765)")
    c.add_argument("--user", help="dashboard user (default $SAVEHUB_DASHBOARD_USER or 'savehub'); password only via $SAVEHUB_DASHBOARD_PASSWORD")
    c.add_argument("--db", help="SQLite path for --sink sqlite (default $SAVEHUB_DB or data/savehub.db)")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--account-id", type=int, help="link items to this SaveHub account id")
    g.add_argument("--account", help="collect from this logged-in account (session <platform>@<handle>.json), tag items with it "
                                    "and link them to its SaveHub account row (created if missing)")
    c.add_argument("--no-push", action="store_true", help="scrape only, print items, push nothing")
    c.add_argument("--unsave", action="store_true", default=None,
                   help="after an item is confirmed stored in SaveHub, un-save it on the app. DRY-RUN unless --unsave-confirm")
    c.add_argument("--unsave-confirm", action="store_true", default=None, help="really un-save (requires --unsave)")
    c.add_argument("--max-unsave", type=int)
    c.add_argument("--incremental", action="store_true", help="stop paginating once already-stored items show up and push only "
                                                              "new ones (used by auto-sync; cannot be combined with --unsave)")
    c.add_argument("--stop-after-known", type=int, default=3, help="incremental: stop after this many already-stored items (default 3)")
    c.add_argument("--backend", help="auto | playwright | <library> (e.g. twscrape for x, instaloader for instagram); "
                                     "default = the per-platform setting (dashboard Accounts page). Un-save always uses playwright")

    au = sub.add_parser("autosync", help="keep every logged-in account in sync: incremental collect every N minutes (never un-saves)")
    au.add_argument("--interval", type=int, help="minutes between runs per account (default 10, min 2); saved as the new setting")
    au.add_argument("--platform", action="append", help="only these platforms (repeatable); default all implemented")
    au.add_argument("--once", action="store_true", help="one pass over every valid session now, then exit")
    au.add_argument("--timeout", type=int, help="per-account timeout in seconds (default 300 or $SAVEHUB_AUTOSYNC_TIMEOUT)")
    au.add_argument("--max-items", type=int, help="items per incremental run (default 30)")
    au.add_argument("--db", help="SaveHub SQLite DB (default $SAVEHUB_DB or data/savehub.db)")
    return p


def make_config(a) -> RunConfig:
    d = {}
    if a.config:
        with open(a.config) as f: d = json.load(f)
    for k in ("max_items", "scroll_delay", "headful", "unsave", "unsave_confirm", "max_unsave", "account_id", "backend"):
        v = getattr(a, k, None)
        if v is not None: d[k] = v
    if a.account: d["account_handle"] = a.account
    cfg = RunConfig.from_dict(d)
    if cfg.unsave_confirm and not cfg.unsave:
        raise CollectorError("--unsave-confirm only makes sense together with --unsave (refusing to guess)")
    if cfg.max_items < 1: raise CollectorError("max_items must be >= 1")
    return cfg


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    setup(a.log_level, a.log_json)
    if a.key_stdin:                                    # the parent writes the vault key + newline to our stdin pipe
        from savehub import vault
        vault.set_runtime_key(sys.stdin.readline())
    out, code = {}, EXIT_OK
    try:
        if a.cmd == "platforms":
            from . import backends
            out = {"platforms": [{**p, "backends": backends.describe(p["platform"])} for p in list_platforms()]}
        elif a.cmd == "sessions": out = {"sessions_dir": str(sessions.sessions_dir(a.sessions_dir)), "sessions": sessions.list_sessions(a.sessions_dir, a.platform)}
        elif a.cmd == "login":
            from .runner import run_login
            out = run_login(a.platform, a.sessions_dir, a.timeout, a.wait_enter, account=a.account)
        elif a.cmd == "login-terminal":
            from .terminal import launch_login_terminal
            try: out = launch_login_terminal(a.platform, a.account, a.sessions_dir, a.display, a.terminal)
            except ValueError as e: raise CollectorError(str(e)) from None
        elif a.cmd == "collect":
            from .runner import run_collect
            from .client import RestSink, SqliteSink
            cfg = make_config(a)
            if a.incremental:
                if cfg.unsave: raise CollectorError("--incremental never un-saves: drop --unsave")
                if a.no_push: raise CollectorError("--incremental needs a sink (it compares against stored items)")
                if a.stop_after_known < 1: raise CollectorError("--stop-after-known must be >= 1")
            if a.no_push:
                if cfg.unsave: raise CollectorError("--no-push cannot be combined with --unsave")
                sink = None
            elif a.sink == "sqlite":
                sink = SqliteSink(a.db or os.environ.get("SAVEHUB_DB") or str(sessions.ROOT / "data" / "savehub.db"))
            else:
                sink = RestSink.from_env(a.savehub_url, a.user)
            if a.incremental:
                cfg.known = sink.known_keys(a.platform); cfg.stop_after_known = a.stop_after_known
            try: out = run_collect(a.platform, cfg, sink, a.sessions_dir)
            finally:
                if sink: sink.close()
            if out.get("push_failed"): code = EXIT_PUSH_FAILED; out["status"] = "partial"
        elif a.cmd == "autosync":
            from . import autosync
            if a.db:
                from savehub import db
                from pathlib import Path
                db.DB_PATH = Path(a.db)
            try:
                out = autosync.cli_main(a.interval, a.platform, a.once, a.timeout, a.max_items, a.sessions_dir)
            except ValueError as e:
                raise CollectorError(str(e)) from None
            if out.get("status") == "unavailable": code = EXIT_ERROR
    except NeedLogin as e:
        code = e.exit_code
        out = {"status": "need_login", "platform": e.platform, "account": ("@" + e.account) if e.account else "default",
               "session_file": e.session_file, "detail": e.detail, "fix": e.fix}
        ev("need_login", logging.ERROR, platform=e.platform, session_file=e.session_file, detail=e.detail)
    except AccountBusy as e:
        code = e.exit_code; out = {"status": "busy", "error": str(e)}
        ev("busy", logging.WARNING, error=str(e))
    except CollectorError as e:
        code = e.exit_code; out = {"status": type(e).__name__, "error": scrub(str(e))}
        ev("error", logging.ERROR, kind=type(e).__name__, error=str(e))
    except KeyboardInterrupt:
        code = 130; out = {"status": "interrupted"}
    except Exception as e:   # unexpected: keep the message, no secrets are in scope here
        code = EXIT_ERROR; out = {"status": "error", "error": scrub(f"{type(e).__name__}: {e}")}
        ev("error", logging.ERROR, kind=type(e).__name__, error=str(e)[:500])
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return code
