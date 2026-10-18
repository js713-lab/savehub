"""Near real-time auto-sync: the apps have no push API, so poll each logged-in account every N minutes with a small,
INCREMENTAL collect (stop paginating at the first already-stored items) and store anything new.

Guarantees
* NEVER un-saves: the child command is built here without --unsave and the CLI refuses --incremental + --unsave.
* One run per account at a time: in-process RUNNING set + the cross-process flock taken by the collector itself.
* Per-account timeout (process group killed), exponential backoff on repeated errors (capped), and an expired /
  rejected session is marked "needs_login" and NOT retried until its session file changes (i.e. you logged in again).
* State per account (last run, last success, items added, last error, next run) lives in the DB table sync_state;
  config (enabled, interval) + loop heartbeat in autosync_meta, so the dashboard / MCP / CLI daemon all see the same.
* Children get an allow-listed environment (runtime.child_env); the vault key, if any, goes over their stdin pipe.

Entry points: `python -m savehub.collector autosync [--interval MIN] [--platform p] [--once]`, start_background()
(combined server with SAVEHUB_AUTOSYNC=1), status() / set_config() / request_sync() for REST + MCP.
Imports no Playwright: safe on Render, where it simply reports the collector as unavailable.
"""
from __future__ import annotations
import json, logging, os, signal, socket, subprocess, sys, threading, time
from pathlib import Path
from typing import Callable, Optional
from .base import REGISTRY, EXIT_OK, EXIT_NEED_LOGIN, EXIT_NOT_IMPLEMENTED, EXIT_PUSH_FAILED, EXIT_BUSY, EXIT_LOCKED
from .logutil import ev, scrub
from . import sessions, runtime

DEFAULT_INTERVAL, MIN_INTERVAL, MAX_INTERVAL = 10, 2, 1440          # minutes
DEFAULT_MAX_ITEMS = int(os.environ.get("SAVEHUB_AUTOSYNC_MAX_ITEMS", "30"))         # per incremental run
FIRST_RUN_MAX_ITEMS = int(os.environ.get("SAVEHUB_AUTOSYNC_FIRST_MAX_ITEMS", "100"))  # first sync of an account
DEFAULT_TIMEOUT_S = int(os.environ.get("SAVEHUB_AUTOSYNC_TIMEOUT", "300"))          # per account
STOP_AFTER_KNOWN = 3
MAX_BACKOFF_S = 6 * 3600
BUSY_RETRY_S = 60
POLL_S = 15
HEARTBEAT_STALE_S = 90
SYNC_NOW_GAP_S = int(os.environ.get("SAVEHUB_SYNC_NOW_GAP", "60"))  # rate limit for "Sync now" (per account and global)
ROOT = Path(__file__).resolve().parent.parent.parent


class Unavailable(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = max(1, int(retry_after)); super().__init__(f"sync was requested too recently; retry in {self.retry_after}s")


def _db():
    from savehub import db
    return db


# ---------------------------------------------------------------- config + meta
def _meta_get(k, default=None):
    r = _db().conn().execute("SELECT v FROM autosync_meta WHERE k=?", (k,)).fetchone()
    if not r: return default
    try: return json.loads(r[0])
    except Exception: return default


def _meta_set(k, v):
    c = _db().conn()
    c.execute("INSERT INTO autosync_meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v))); c.commit()


def validate_interval(v) -> int:
    if isinstance(v, bool): raise ValueError("interval_minutes must be an integer")
    if isinstance(v, float) and v.is_integer(): v = int(v)
    if isinstance(v, str) and v.strip().isdigit(): v = int(v.strip())
    if not isinstance(v, int): raise ValueError("interval_minutes must be an integer")
    if not (MIN_INTERVAL <= v <= MAX_INTERVAL): raise ValueError(f"interval_minutes must be between {MIN_INTERVAL} and {MAX_INTERVAL}")
    return v


def get_config() -> dict:
    c = _meta_get("config") or {}
    try: iv = validate_interval(c.get("interval_minutes", DEFAULT_INTERVAL))
    except ValueError: iv = DEFAULT_INTERVAL
    return {"enabled": bool(c.get("enabled", False)), "interval_minutes": iv, "configured": bool(c)}


def set_config(enabled: Optional[bool] = None, interval_minutes=None) -> dict:
    cur = get_config()
    if enabled is not None:
        if not isinstance(enabled, bool): raise ValueError("enabled must be true or false")
        cur["enabled"] = enabled
    if interval_minutes is not None: cur["interval_minutes"] = validate_interval(interval_minutes)
    _meta_set("config", {"enabled": cur["enabled"], "interval_minutes": cur["interval_minutes"], "updated_at": time.time()})
    return get_config()


def loop_info(now=None) -> dict:
    hb = _meta_get("loop") or {}
    now = now or time.time()
    alive = bool(hb) and now - float(hb.get("heartbeat", 0)) < HEARTBEAT_STALE_S
    if alive and hb.get("host") == socket.gethostname() and hb.get("pid"):
        try: os.kill(int(hb["pid"]), 0)
        except ProcessLookupError: alive = False
        except PermissionError: pass
    return {"running": alive, **({"where": hb.get("where"), "pid": hb.get("pid"), "since": _iso(hb.get("started_at")),
                                  "heartbeat": _iso(hb.get("heartbeat"))} if alive else {})}


# ---------------------------------------------------------------- per-account state
STATE_FIELDS = ("status", "running", "last_run", "last_success", "last_added", "total_added", "last_collected", "last_error",
                "consecutive_errors", "next_run", "session_mtime", "last_reason")


def get_state(platform, account) -> dict:
    r = _db().conn().execute("SELECT * FROM sync_state WHERE platform=? AND account=?", (platform, account)).fetchone()
    return dict(r) if r else {}


def all_states() -> dict:
    return {(r["platform"], r["account"]): dict(r) for r in _db().conn().execute("SELECT * FROM sync_state")}


def _update_state(platform, account, **f):
    f = {k: v for k, v in f.items() if k in STATE_FIELDS}
    f["updated_at"] = time.time()
    c = _db().conn()
    c.execute("INSERT INTO sync_state(platform, account) VALUES(?,?) ON CONFLICT(platform, account) DO NOTHING", (platform, account))
    c.execute(f"UPDATE sync_state SET {', '.join(k + '=?' for k in f)} WHERE platform=? AND account=?",   # nosec B608 - column names are a fixed allow-list
              [*f.values(), platform, account])
    c.commit()


def backoff_s(errors: int, interval_min: int) -> float:
    """1st error: retry after one interval, then 2x, 4x, ... capped at 6 h."""
    return min(interval_min * 60 * (2 ** max(0, errors - 1)), MAX_BACKOFF_S)


def next_due(st: dict, interval_min: int, session_mtime: Optional[float]):
    """Epoch when this account is due again, or None = not until the user logs in again."""
    status = st.get("status")
    if status in ("needs_login", "locked", "disabled"):
        return 0.0 if (session_mtime and st.get("session_mtime") and session_mtime > float(st["session_mtime"]) + 1e-6) else None
    last = st.get("last_run")
    if not last: return 0.0
    if status == "error": return float(last) + backoff_s(int(st.get("consecutive_errors") or 0), interval_min)
    if status == "busy": return float(last) + BUSY_RETRY_S
    return float(last) + interval_min * 60


def _iso(t):
    if not t: return None
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(float(t)))


def eligible_sessions(platforms=None, sessions_dir=None) -> list:
    """Saved sessions on implemented collectors (and enabled platforms), with metadata only."""
    try: enabled = set(_db().get_settings()["enabled_platforms"])
    except Exception: enabled = set(REGISTRY)
    out = []
    for s in sessions.list_sessions(sessions_dir):
        p = s["platform"]; cls = REGISTRY.get(p)
        if cls is None or not cls.implemented or p not in enabled: continue
        if platforms and p not in platforms: continue
        out.append(s)
    return out


# ---------------------------------------------------------------- running one account
def build_cmd(platform: str, account: str, max_items: int, sessions_dir=None, with_key: bool = False) -> list:
    """The child command. Deliberately has no un-save flags (auto-sync never un-saves)."""
    db = _db()
    cmd = [sys.executable, "-m", "savehub.collector", "--log-level", "WARNING"]
    if with_key: cmd.append("--key-stdin")
    cmd += ["--sessions-dir", str(sessions.sessions_dir(sessions_dir)), "collect", platform, "--sink", "sqlite", "--db", str(db.DB_PATH),
            "--incremental", "--stop-after-known", str(STOP_AFTER_KNOWN), "--max-items", str(int(max_items))]
    if account != sessions.DEFAULT_ACCOUNT: cmd += ["--account", account]
    if "--unsave" in cmd or "--unsave-confirm" in cmd: raise RuntimeError("auto-sync must never un-save")  # invariant (not an assert: survives -O)
    return cmd


_CURRENT: dict = {}           # thread id -> Popen (so a stopping loop can kill its child)


def _default_relogin(platform, account, sessions_dir=None):
    """Re-login from the encrypted vault (savehub.collector.autologin) unless the last attempt was a wrong password."""
    from . import autologin, login_flows
    if not login_flows.supports_password_login(platform): return None
    if (autologin.get_job(platform, account).get("status") or "") == "wrong_password": return None
    return autologin.try_relogin(platform, account, sessions_dir)


def spawn_collect(platform: str, account: str, max_items: int, timeout: int, sessions_dir=None) -> dict:
    """Run one incremental collect in a subprocess (own process group, killed on timeout)."""
    from savehub import vault
    key = vault.raw_key_for_child() if vault.session_key_available() else ""
    cmd = build_cmd(platform, account, max_items, sessions_dir, with_key=bool(key))
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=runtime.child_env(_db().DB_PATH), stdin=subprocess.PIPE if key else subprocess.DEVNULL,  # nosec B603 - fixed argv, no shell
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    _CURRENT[threading.get_ident()] = proc
    try:
        try:
            out, err = proc.communicate(input=(key + "\n").encode() if key else None, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            return {"status": "timeout", "exit_code": None, "error": f"no result within {timeout}s (stopped)"}
    finally:
        _CURRENT.pop(threading.get_ident(), None)
        key = ""
    try: res = json.loads(out.decode() or "{}")
    except Exception: res = {"status": "error", "error": "collector produced no result: " + scrub(err.decode(errors="replace")[-300:])}
    res["exit_code"] = proc.returncode
    return res


def _kill(proc):
    try: os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try: proc.kill()
        except Exception: pass
    try: proc.wait(timeout=10)
    except Exception: pass


class Engine:
    """Schedules and runs incremental collects. run_fn/clock are injectable for tests."""

    def __init__(self, sessions_dir=None, platforms=None, run_fn: Optional[Callable] = None, clock: Callable = time.time,
                 timeout: int = DEFAULT_TIMEOUT_S, max_items: int = DEFAULT_MAX_ITEMS, first_max_items: int = FIRST_RUN_MAX_ITEMS,
                 relogin_fn: Optional[Callable] = None):
        self.sessions_dir, self.platforms = sessions_dir, set(platforms or ()) or None
        self.run_fn = run_fn or spawn_collect
        self.relogin_fn = relogin_fn if relogin_fn is not None else _default_relogin
        self.clock, self.timeout, self.max_items, self.first_max_items = clock, timeout, max_items, first_max_items

    def interval(self) -> int:
        return get_config()["interval_minutes"]

    def due(self, force: bool = False) -> list:
        now, iv, out = self.clock(), self.interval(), []
        for s in eligible_sessions(self.platforms, self.sessions_dir):
            st = get_state(s["platform"], s["account"])
            nd = next_due(st, iv, s.get("mtime"))
            if s["status"] in ("missing", "invalid"): continue
            if nd is None: continue                                     # needs login / locked: wait for a new session file
            if force or nd <= now: out.append((s["platform"], s["account"]))
        return out

    def run_account(self, platform: str, account: str, reason: str = "schedule") -> dict:
        cls = REGISTRY.get(platform)
        if cls is None or not cls.implemented: raise ValueError(f"no implemented collector for '{platform}'")
        account = sessions.account_key(account)
        path = sessions.session_path(platform, self.sessions_dir, account)
        tag = (platform, account)
        with runtime.RUNNING_LOCK:
            if tag in runtime.RUNNING: return {"platform": platform, "account": account, "status": "busy"}
            runtime.RUNNING.add(tag)
        try:
            return self._run(platform, account, path, reason)
        finally:
            with runtime.RUNNING_LOCK: runtime.RUNNING.discard(tag)

    def _run(self, platform, account, path, reason) -> dict:
        now, iv = self.clock(), self.interval()
        st = get_state(platform, account)
        d = sessions.describe(path)
        base = {"platform": platform, "account": account}
        if d["status"] == "missing": return {**base, "status": "skipped", "reason": "no session"}
        mt = d.get("mtime") or (path.stat().st_mtime if path.exists() else None)
        if d["status"] == "expired" and self._relogin(platform, account) == "connected":
            d = sessions.describe(path); mt = d.get("mtime") or mt               # fresh session from the vault login
        if d["status"] == "expired":
            _update_state(platform, account, status="needs_login", running=0, last_run=now, session_mtime=mt, next_run=None,
                          last_error="session cookies expired: log in again on the box", last_reason=reason)
            ev("autosync.needs_login", logging.WARNING, platform=platform, account=account, reason="expired cookies")
            return {**base, "status": "needs_login"}
        if d["status"] in ("locked", "invalid"):
            _update_state(platform, account, status="locked" if d["status"] == "locked" else "error", running=0, last_run=now,
                          session_mtime=mt, next_run=None, last_error=scrub(d.get("error") or d["status"])[:300], last_reason=reason)
            return {**base, "status": d["status"]}
        first = not st.get("last_success")
        cap = self.first_max_items if first else self.max_items
        _update_state(platform, account, running=1, last_run=now, session_mtime=mt, last_reason=reason)
        ev("autosync.run", platform=platform, account=account, max_items=cap, reason=reason)
        res = self._call(platform, account, cap)
        if (res.get("exit_code") == EXIT_NEED_LOGIN or res.get("status") == "need_login") and self._relogin(platform, account) == "connected":
            res = self._call(platform, account, cap)                     # the app logged us out: re-login from the vault, retry once
        return {**base, **self._record(platform, account, res, now, iv, mt, reason)}

    def _call(self, platform, account, cap) -> dict:
        try:
            return self.run_fn(platform, account, cap, self.timeout, self.sessions_dir) or {}
        except Exception as e:                                          # never let one account kill the loop
            return {"status": "error", "error": f"{type(e).__name__}: {e}", "exit_code": None}

    def _relogin(self, platform, account):
        """Expired / rejected session + saved vault login -> automatic re-login (never un-saves). None = no credentials."""
        if not self.relogin_fn: return None
        try: st = self.relogin_fn(platform, account, self.sessions_dir)
        except Exception as e:
            ev("autosync.relogin_error", logging.WARNING, platform=platform, account=account, error=scrub(str(e))[:200]); return "error"
        if st: ev("autosync.relogin", platform=platform, account=account, result=st)
        if st and st != "connected":
            _update_state(platform, account, last_error=f"automatic re-login: {st.replace('_', ' ')}")
        return st

    def _record(self, platform, account, res, started, iv, mt, reason) -> dict:
        code, status = res.get("exit_code"), res.get("status")
        try: mt = sessions.session_path(platform, self.sessions_dir, account).stat().st_mtime   # after a possible re-encrypt
        except OSError: pass
        st = get_state(platform, account); errors = int(st.get("consecutive_errors") or 0)
        added = int(res.get("created") or 0)
        done = self.clock()
        if code == EXIT_OK and status == "ok":
            _update_state(platform, account, status="ok", running=0, last_success=done, last_added=added,
                          total_added=int(st.get("total_added") or 0) + added, last_collected=int(res.get("collected") or 0),
                          last_error=None, consecutive_errors=0, next_run=done + iv * 60)
            ev("autosync.ok", platform=platform, account=account, added=added, collected=res.get("collected"), known_skipped=res.get("known_skipped"))
            return {"status": "ok", "added": added, "collected": res.get("collected", 0)}
        if code == EXIT_NEED_LOGIN or status == "need_login":
            _update_state(platform, account, status="needs_login", running=0, last_added=0, next_run=None, session_mtime=mt,
                          last_error=scrub(res.get("detail") or "the app asked for a login")[:300])
            ev("autosync.needs_login", logging.WARNING, platform=platform, account=account)
            return {"status": "needs_login"}
        if code == EXIT_BUSY or status == "busy" or status == "AccountBusy":
            _update_state(platform, account, status="busy", running=0, next_run=done + BUSY_RETRY_S)
            return {"status": "busy"}
        if code == EXIT_LOCKED or status == "SessionLocked":
            _update_state(platform, account, status="locked", running=0, next_run=None, session_mtime=mt,
                          last_error=scrub(res.get("error") or "encrypted session, no key")[:300])
            return {"status": "locked"}
        if code == EXIT_NOT_IMPLEMENTED:
            _update_state(platform, account, status="disabled", running=0, next_run=None, session_mtime=mt, last_error="collector not implemented")
            return {"status": "disabled"}
        errors += 1
        msg = res.get("error") or (f"push failed for {res.get('push_failed')} item(s)" if code == EXIT_PUSH_FAILED else f"exit code {code}")
        nxt = done + backoff_s(errors, iv)
        _update_state(platform, account, status="error", running=0, last_added=added, consecutive_errors=errors,
                      last_error=scrub(str(msg))[:300], next_run=nxt,
                      total_added=int(st.get("total_added") or 0) + added)
        ev("autosync.error", logging.WARNING, platform=platform, account=account, errors=errors, retry_in_s=int(nxt - done), error=scrub(str(msg))[:200])
        return {"status": "error", "error": scrub(str(msg))[:300], "consecutive_errors": errors, "retry_in_s": int(nxt - done)}

    def tick(self, force: bool = False, reason: str = "schedule") -> list:
        return [self.run_account(p, a, reason) for p, a in self.due(force)]


# ---------------------------------------------------------------- the loop
class _LoopLock:
    """Only one auto-sync loop per DB (across processes): flock on <db dir>/.autosync.lock."""
    def __init__(self):
        self.fh = None

    def acquire(self) -> bool:
        try: import fcntl
        except ImportError: return True
        p = Path(_db().DB_PATH).parent / ".autosync.lock"
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = os.fdopen(os.open(p, os.O_RDWR | os.O_CREAT, 0o600), "r+")
        try: fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close(); return False
        self.fh = fh; return True

    def release(self):
        if self.fh:
            try: self.fh.close()
            finally: self.fh = None


def run_loop(stop: threading.Event, engine: Optional[Engine] = None, where: str = "cli", poll: float = POLL_S) -> None:
    engine = engine or Engine()
    lock = _LoopLock()
    waiting_logged = False
    while not stop.is_set() and not lock.acquire():
        if not waiting_logged: ev("autosync.waiting", note="another auto-sync loop holds the lock; standing by"); waiting_logged = True
        stop.wait(60)
    if stop.is_set(): return
    started = time.time()
    try:
        sessions.harden(engine.sessions_dir); _db().harden_files()
        # a crashed previous loop may have left running=1 behind
        c = _db().conn(); c.execute("UPDATE sync_state SET running=0 WHERE running=1"); c.commit()
        ev("autosync.start", where=where, pid=os.getpid(), interval_min=engine.interval())
        while not stop.is_set():
            cfg = get_config()
            _meta_set("loop", {"pid": os.getpid(), "host": socket.gethostname(), "where": where, "started_at": started,
                               "heartbeat": time.time(), "enabled": cfg["enabled"]})
            if cfg["enabled"]:
                for p, a in engine.due():
                    if stop.is_set(): break
                    engine.run_account(p, a, "schedule")
                    _meta_set("loop", {"pid": os.getpid(), "host": socket.gethostname(), "where": where, "started_at": started,
                                       "heartbeat": time.time(), "enabled": cfg["enabled"]})
            stop.wait(poll)
    finally:
        for proc in list(_CURRENT.values()): _kill(proc)
        try: _meta_set("loop", {})
        except Exception: pass
        lock.release()
        ev("autosync.stop", where=where)



# ---------------------------------------------------------------- runs started outside the Engine (MCP `collect`)
def mark_external_start(platform: str, account: str, reason: str = "mcp") -> None:
    """A collect started elsewhere (MCP `collect`) is now running for this account: show it as "syncing"."""
    _update_state(platform, sessions.account_key(account), running=1, last_run=time.time(), last_reason=reason)


def record_external(platform: str, account: str, res: dict, started: Optional[float] = None, reason: str = "mcp",
                    sessions_dir=None) -> dict:
    """Record the outcome of a collect that did not go through the Engine (MCP `collect`) in the same per-account
    sync_state the auto-sync loop and the dashboard use: ok -> synced (counts, next run pushed out by one interval),
    need_login -> needs_login (not retried until the session file changes), errors -> error + backoff, etc."""
    account = sessions.account_key(account)
    started = started or time.time()
    eng = Engine(sessions_dir=sessions_dir)
    try: iv = eng.interval()
    except Exception: iv = DEFAULT_INTERVAL
    try: mt = sessions.session_path(platform, sessions_dir, account).stat().st_mtime
    except OSError: mt = None
    out = eng._record(platform, account, dict(res or {}), started, iv, mt, reason)
    _update_state(platform, account, running=0, last_run=started, last_reason=reason)
    return out


def record_needs_login(platform: str, account: str, detail: str = "session expired: log in again on the box",
                       reason: str = "mcp", sessions_dir=None) -> None:
    """An account whose saved session is already expired / rejected (found by MCP `collect` / login checks)."""
    account = sessions.account_key(account)
    try: mt = sessions.session_path(platform, sessions_dir, account).stat().st_mtime
    except OSError: mt = None
    _update_state(platform, account, status="needs_login", running=0, last_run=time.time(), next_run=None, session_mtime=mt,
                  last_added=0, last_error=scrub(detail)[:300], last_reason=reason)
    ev("autosync.needs_login", logging.WARNING, platform=platform, account=account, reason=reason)


_BG: dict = {}


def start_background(where: str = "server", engine: Optional[Engine] = None) -> dict:
    """Start the loop in a daemon thread (combined server). Never raises: returns {started|reason}."""
    try:
        rt = runtime.collector_runtime()
        if not rt["available"]:
            ev("autosync.unavailable", logging.WARNING, reason=rt.get("reason"))
            return {"started": False, "reason": rt.get("reason")}
        if _BG.get("thread") and _BG["thread"].is_alive(): return {"started": True, "already": True}
        if not get_config()["configured"]: set_config(enabled=True)
        stop = threading.Event()
        t = threading.Thread(target=run_loop, args=(stop, engine, where), name="savehub-autosync", daemon=True)
        t.start(); _BG.update(thread=t, stop=stop)
        return {"started": True}
    except Exception as e:                                              # never crash the server
        ev("autosync.start_failed", logging.ERROR, error=scrub(str(e))[:200])
        return {"started": False, "reason": f"{type(e).__name__}: {e}"}


def stop_background(timeout: float = 5):
    if _BG.get("stop"): _BG["stop"].set()
    t = _BG.get("thread")
    if t: t.join(timeout)
    _BG.clear()


# ---------------------------------------------------------------- status / manual sync (REST + MCP)
def _account_rows() -> dict:
    out = {}
    try:
        for a in _db().list_accounts("", None, None, "platform", "asc", 5000)["accounts"]:
            h = (a.get("handle") or "").strip().lstrip("@").lower()
            if h: out.setdefault((a["platform"], h), a["id"])
    except Exception:
        pass
    return out


def _label(st: dict, sess_status: str, running: bool, now: float) -> str:
    if running: return "syncing"
    if sess_status == "expired": return "needs_login"
    if sess_status == "locked": return "locked"
    s = st.get("status")
    if s in ("needs_login", "locked", "error", "disabled", "busy"): return s
    if s == "ok": return "synced"
    return "pending"


def status(sessions_dir=None) -> dict:
    now = time.time(); cfg = get_config(); iv = cfg["interval_minutes"]
    states, rows, accts = all_states(), _account_rows(), []
    for s in eligible_sessions(None, sessions_dir):
        p, a = s["platform"], s["account"]
        st = states.get((p, a), {})
        running = bool(st.get("running")) and now - float(st.get("last_run") or 0) < DEFAULT_TIMEOUT_S + 60
        nd = next_due(st, iv, s.get("mtime")) if s["status"] not in ("expired",) else None
        lbl = _label(st, s["status"], running, now)
        accts.append({"platform": p, "account": a if a == sessions.DEFAULT_ACCOUNT else "@" + a, "account_id": rows.get((p, a)),
                      "session": s["status"], "encrypted": bool(s.get("encrypted")), "state": lbl,
                      "last_run": _iso(st.get("last_run")), "last_success": _iso(st.get("last_success")),
                      "last_success_ago_s": int(now - st["last_success"]) if st.get("last_success") else None,
                      "items_added_last_run": int(st.get("last_added") or 0), "items_added_total": int(st.get("total_added") or 0),
                      "last_error": st.get("last_error") if lbl in ("error", "needs_login", "locked", "disabled") else None,
                      "consecutive_errors": int(st.get("consecutive_errors") or 0),
                      "next_run": _iso(max(nd, now)) if (nd is not None and cfg["enabled"]) else None,
                      **({"fix": f"python -m savehub.collector login {p}" + (f" --account @{a}" if a != sessions.DEFAULT_ACCOUNT else "")}
                         if lbl == "needs_login" else {})})
    rt = runtime.collector_runtime()
    from . import backends
    bk = {}
    for p in sorted(REGISTRY):
        if not REGISTRY[p].implemented: continue
        try: bk[p] = backends.describe(p)
        except Exception as e: bk[p] = {"backend": "playwright", "options": ["playwright"], "error": scrub(str(e))[:200]}
    return {"enabled": cfg["enabled"], "interval_minutes": iv, "min_interval_minutes": MIN_INTERVAL, "backends": bk,
            "available": bool(rt["available"]), **({"reason": rt.get("reason")} if not rt["available"] else {}),
            "loop": loop_info(now), "unsave": "never", "max_items_per_run": DEFAULT_MAX_ITEMS,
            "session_encryption": _enc_state(), "accounts": accts}


def _enc_state() -> str:
    try:
        from savehub import vault
        return "on" if vault.session_key_available() else "off"
    except Exception:
        return "off"


_trigger_lock = threading.Lock()
_last_trigger: dict = {}


def request_sync(platform: Optional[str] = None, account: Optional[str] = None, sessions_dir=None,
                 engine: Optional[Engine] = None, wait: bool = False) -> dict:
    """'Sync now' (dashboard): validates input, rate-limits, then runs incremental collects (un-save is never possible)
    in a background thread. Raises ValueError (bad input), Unavailable (no browser here) or RateLimited."""
    if platform is not None:
        cls = REGISTRY.get(platform)
        if cls is None: raise ValueError("unknown platform")
        if not cls.implemented: raise ValueError(f"the '{platform}' collector is not implemented")
    akey = sessions.account_key(account) if account else None          # raises ValueError on bad handles (regex)
    if akey and not platform: raise ValueError("account needs a platform")
    rt = runtime.runtime_for(platform) if platform else runtime.collector_runtime()
    if not rt["available"]: raise Unavailable(rt.get("reason") or "browser collector unavailable on this server")
    engine = engine or Engine(sessions_dir=sessions_dir)
    if akey:
        if not sessions.session_path(platform, engine.sessions_dir, akey).exists(): raise ValueError("no saved session for that account")
        targets = [(platform, akey)]
    else:
        targets = [(s["platform"], s["account"]) for s in eligible_sessions({platform} if platform else None, engine.sessions_dir)
                   if s["status"] == "saved"]
    tkey = (platform or "*", akey or "*")
    now = time.time()
    with _trigger_lock:
        for k in (tkey, ("*", "*")):
            last = _last_trigger.get(k)
            if last and now - last < SYNC_NOW_GAP_S: raise RateLimited(SYNC_NOW_GAP_S - (now - last))
        _last_trigger[tkey] = now
    busy = [f"{p}@{a}" for p, a in targets if (p, a) in runtime.RUNNING]
    todo = [(p, a) for p, a in targets if (p, a) not in runtime.RUNNING]

    def work():
        for p, a in todo:
            try: engine.run_account(p, a, "manual")
            except Exception as e: ev("autosync.manual_error", logging.WARNING, platform=p, account=a, error=scrub(str(e))[:200])

    if wait: work()
    elif todo: threading.Thread(target=work, name="savehub-sync-now", daemon=True).start()
    return {"started": [f"{p}@{a}" if a != sessions.DEFAULT_ACCOUNT else p for p, a in todo], "busy": busy, "unsave": "off"}


# ---------------------------------------------------------------- CLI
def cli_main(interval=None, platforms=None, once=False, timeout=None, max_items=None, sessions_dir=None) -> dict:
    """`python -m savehub.collector autosync`. Persists --interval and enables auto-sync, then loops until SIGTERM/^C.
    --once: one pass over every valid session now (ignores the schedule, still skips needs-login), then exit."""
    if interval is not None: validate_interval(interval)
    if platforms:
        bad = [p for p in platforms if p not in REGISTRY or not REGISTRY[p].implemented]
        if bad: raise ValueError(f"no implemented collector for: {', '.join(bad)}")
    eng = Engine(sessions_dir=sessions_dir, platforms=platforms, timeout=timeout or DEFAULT_TIMEOUT_S, max_items=max_items or DEFAULT_MAX_ITEMS)
    if once:
        if interval is not None: set_config(interval_minutes=interval)
        sessions.harden(sessions_dir)
        res = eng.tick(force=True, reason="once")
        return {"status": "ok", "mode": "once", "runs": res, "unsave": "never"}
    rt = runtime.collector_runtime()
    if not rt["available"]: return {"status": "unavailable", "reason": rt.get("reason")}
    cfg = set_config(enabled=True, interval_minutes=interval)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try: signal.signal(sig, lambda *_: stop.set())
        except (ValueError, OSError): pass
    ev("autosync.config", enabled=cfg["enabled"], interval_min=cfg["interval_minutes"], platforms=",".join(platforms or []) or "all")
    run_loop(stop, eng, "cli")
    return {"status": "stopped"}
