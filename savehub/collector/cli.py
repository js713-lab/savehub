"""CLI:  python -m savehub.collector {platforms,sessions,login,collect} ...

Exit codes: 0 ok · 1 error · 2 need login (session missing/expired) · 3 platform not implemented · 4 SaveHub push failed.
The final result is printed to stdout as JSON; logs go to stderr.
"""
from __future__ import annotations
import argparse, json, logging, os, sys
from .base import CollectorError, NeedLogin, RunConfig, EXIT_ERROR, EXIT_OK, EXIT_PUSH_FAILED, list_platforms
from .logutil import ev, setup
from . import sessions, platforms  # noqa: F401


def build_parser():
    p = argparse.ArgumentParser(prog="python -m savehub.collector",
                                description="Local browser collector for SaveHub (needs Playwright + Chromium; does not run on Render).")
    p.add_argument("--log-level", default=os.environ.get("SAVEHUB_LOG_LEVEL", "INFO"))
    p.add_argument("--log-json", action="store_true", help="structured JSON log lines on stderr")
    p.add_argument("--sessions-dir", help="dir for <platform>.json sessions (default data/sessions or $SAVEHUB_SESSIONS_DIR)")
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
    return p


def make_config(a) -> RunConfig:
    d = {}
    if a.config:
        with open(a.config) as f: d = json.load(f)
    for k in ("max_items", "scroll_delay", "headful", "unsave", "unsave_confirm", "max_unsave", "account_id"):
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
    out, code = {}, EXIT_OK
    try:
        if a.cmd == "platforms": out = {"platforms": list_platforms()}
        elif a.cmd == "sessions": out = {"sessions_dir": str(sessions.sessions_dir(a.sessions_dir)), "sessions": sessions.list_sessions(a.sessions_dir, a.platform)}
        elif a.cmd == "login":
            from .runner import run_login
            out = run_login(a.platform, a.sessions_dir, a.timeout, a.wait_enter, account=a.account)
        elif a.cmd == "collect":
            from .runner import run_collect
            from .client import RestSink, SqliteSink
            cfg = make_config(a)
            if a.no_push:
                if cfg.unsave: raise CollectorError("--no-push cannot be combined with --unsave")
                sink = None
            elif a.sink == "sqlite":
                sink = SqliteSink(a.db or os.environ.get("SAVEHUB_DB") or str(sessions.ROOT / "data" / "savehub.db"))
            else:
                sink = RestSink.from_env(a.savehub_url, a.user)
            try: out = run_collect(a.platform, cfg, sink, a.sessions_dir)
            finally:
                if sink: sink.close()
            if out.get("push_failed"): code = EXIT_PUSH_FAILED; out["status"] = "partial"
    except NeedLogin as e:
        code = e.exit_code
        out = {"status": "need_login", "platform": e.platform, "account": ("@" + e.account) if e.account else "default",
               "session_file": e.session_file, "detail": e.detail, "fix": e.fix}
        ev("need_login", logging.ERROR, platform=e.platform, session_file=e.session_file, detail=e.detail)
    except CollectorError as e:
        code = e.exit_code; out = {"status": type(e).__name__, "error": str(e)}
        ev("error", logging.ERROR, kind=type(e).__name__, error=str(e))
    except KeyboardInterrupt:
        code = 130; out = {"status": "interrupted"}
    except Exception as e:   # unexpected: keep the message, no secrets are in scope here
        code = EXIT_ERROR; out = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        ev("error", logging.ERROR, kind=type(e).__name__, error=str(e)[:500])
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return code
