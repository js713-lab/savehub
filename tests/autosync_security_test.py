"""Auto-sync + security hardening tests (throwaway keys, temp DBs/session dirs; no real social-network traffic).

Run:  .venv/bin/python -m pytest -q tests/autosync_security_test.py
"""
import asyncio, base64, json, logging, os, pathlib, stat, subprocess, sys, tempfile, threading, time
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from cryptography.fernet import Fernet
from savehub import db, vault
from savehub.collector import RunConfig, runner, sessions, autosync, runtime
from savehub.collector.base import AccountBusy, CollectorError, CollectedItem, EXIT_NEED_LOGIN, EXIT_OK
from savehub.collector.client import SqliteSink
from savehub.collector.platforms._common import FeedCollector
from savehub.collector.logutil import scrub, ScrubFilter

COOKIE = "dummy-cookie-SECRETVALUE-123"


def _sess_doc(expires=-1):
    return {"cookies": [{"name": "auth_token", "value": COOKIE, "domain": ".x.com", "path": "/", "expires": expires,
                         "httpOnly": True, "secure": True, "sameSite": "None"}], "origins": []}


@pytest.fixture()
def tdb(tmp_path, monkeypatch):
    """Temp DB + temp sessions dir; restores the db module afterwards."""
    old = db.DB_PATH
    c = getattr(db._local, "c", None)
    if c is not None: c.close(); db._local.c = None
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    sd = tmp_path / "sessions"
    monkeypatch.setenv("SAVEHUB_SESSIONS_DIR", str(sd))
    monkeypatch.delenv("SAVEHUB_VAULT_KEY", raising=False)
    monkeypatch.setenv("SAVEHUB_VAULT_KEY_FILE", str(tmp_path / "no.key"))
    for k in ("RENDER", "RENDER_EXTERNAL_HOSTNAME", "SAVEHUB_COLLECTOR", "SAVEHUB_DASHBOARD_PASSWORD"): monkeypatch.delenv(k, raising=False)
    vault.set_runtime_key("")
    autosync._last_trigger.clear()
    yield sd
    c = getattr(db._local, "c", None)
    if c is not None: c.close(); db._local.c = None
    db.DB_PATH = old
    vault.set_runtime_key("")


@pytest.fixture()
def key(monkeypatch):
    k = Fernet.generate_key().decode()        # throwaway, per test
    monkeypatch.setenv("SAVEHUB_VAULT_KEY", k)
    return k


# ======================================================================== incremental stop
class FakePage:
    """Minimal page for FeedCollector.scroll_loop: every scroll reveals the next batch of items."""
    url = "https://example.test/saved"

    def __init__(self, batches): self.batches, self.shown, self.scrolls = batches, 1, 0
    def evaluate(self, js, arg=None):
        if js == "EXTRACT": return [r for b in self.batches[: self.shown] for r in b]
        self.scrolls += 1; self.shown = min(len(self.batches), self.shown + 1); return None
    def locator(self, sel):
        class L:
            def count(self_inner): return 0
        return L()
    def wait_for_timeout(self, ms): pass


class FakeFeed(FeedCollector):
    platform, name = "x", "fake feed"
    EXTRACT_JS = "EXTRACT"
    SEL = {"item": "article"}

    def embedded_raws(self, page): return []


def _batches(n_batches=6, per=3):
    return [[{"id": f"p{b}{i}", "url": f"https://example.test/p/{b}{i}", "text": f"post {b}{i}"} for i in range(per)] for b in range(n_batches)]


def test_incremental_stop_feedcollector():
    fast = dict(scroll_delay=0, scroll_jitter=0, idle_rounds=3, max_scrolls=50)
    col = FakeFeed(); col._responses = []
    # no known items -> scrolls to the end of the list
    pg = FakePage(_batches()); items = {}
    col.scroll_loop(pg, RunConfig(**fast), "", "", items)
    assert len(items) == 18 and pg.scrolls >= 5
    # newest 2 batches are new, from batch 2 on everything is already stored -> stop after 3 known items
    known = {f"https://example.test/p/{b}{i}" for b in range(2, 6) for i in range(3)}
    pg = FakePage(_batches()); items = {}
    col.scroll_loop(pg, RunConfig(**fast, known=known, stop_after_known=3), "", "", items)
    new = [it for it in items.values() if not col.is_known(RunConfig(known=known), it)]
    assert pg.scrolls == 2 and len(new) == 6 and len(items) == 9, (pg.scrolls, len(items))
    # matching by external id works too
    assert col.is_known(RunConfig(known={"id:p00"}), CollectedItem(platform="x", url="https://other", external_id="p00"))


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = runner.launch_browser(p, headful=False)
        yield b
        b.close()


def test_incremental_collect_end_to_end_x(browser, tdb, tmp_path):
    from collector_test import FakeX, IDS, FAST
    sd = tdb; sessions.write_state(sd / "x.json", _sess_doc())
    first = runner.run_collect("x", RunConfig(**FAST), None, sd, browser=browser, context_hook=FakeX().hook)
    urls = [i["url"] for i in first["items"]]; assert len(urls) == 7
    sink = SqliteSink(str(tmp_path / "inc.db"))
    for u in urls[:3]: sink.db.add_item({"platform": "x", "url": u, "title": "stored before"})
    cfg = RunConfig(**FAST, known=sink.known_keys("x"), stop_after_known=3)
    s = runner.run_collect("x", cfg, sink, sd, browser=browser, context_hook=FakeX().hook)
    # the first screen shows 4 bookmarks: 3 already stored -> stop paginating, push only the 1 new one
    assert s["incremental"] and s["known_skipped"] == 3 and s["created"] == 1 and s["duplicates"] == 0, s
    assert [i["url"] for i in s["items"]] == [urls[3]]
    with pytest.raises(CollectorError):                      # incremental never un-saves
        runner.run_collect("x", RunConfig(**FAST, known={"a"}, stop_after_known=3, unsave=True), sink, sd, browser=browser)


def test_cli_incremental_refuses_unsave(tdb, tmp_path):
    sessions.write_state(tdb / "x.json", _sess_doc())
    r = subprocess.run([sys.executable, "-m", "savehub.collector", "--sessions-dir", str(tdb), "collect", "x", "--sink", "sqlite",
                        "--db", str(tmp_path / "c.db"), "--incremental", "--unsave"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "never un-saves" in json.loads(r.stdout)["error"]


# ======================================================================== scheduler: backoff / expired / toggle / busy
class Clock:
    def __init__(self, t=1_800_000_000.0): self.t = t
    def __call__(self): return self.t


def _engine(sd, results, clock, calls):
    def run_fn(p, a, cap, timeout, sdir):
        calls.append((p, a, cap)); r = results.pop(0) if results else {"status": "ok", "exit_code": 0, "created": 0}
        return r
    return autosync.Engine(sessions_dir=sd, run_fn=run_fn, clock=clock, timeout=5)


def test_backoff_and_recovery(tdb):
    sessions.write_state(tdb / "x@alice.json", _sess_doc())
    autosync.set_config(enabled=True, interval_minutes=10)
    clk, calls = Clock(), []
    err = {"status": "error", "exit_code": 1, "error": "boom with token ghp_" + "a" * 30}
    eng = _engine(tdb, [dict(err), dict(err), dict(err), {"status": "ok", "exit_code": 0, "created": 4, "collected": 6}], clk, calls)
    assert eng.due() == [("x", "alice")]
    gaps = []
    for _ in range(3):
        r = eng.run_account("x", "alice")
        st = autosync.get_state("x", "alice"); gaps.append(st["next_run"] - clk.t)
        assert r["status"] == "error" and st["status"] == "error" and "ghp_" not in st["last_error"]     # error text scrubbed
        assert eng.due() == []                                              # not due before the backoff expires
        clk.t = st["next_run"] + 1
        assert eng.due() == [("x", "alice")]
    assert gaps == [600, 1200, 2400]                                         # exponential backoff
    assert autosync.backoff_s(20, 10) == autosync.MAX_BACKOFF_S              # capped
    r = eng.run_account("x", "alice"); st = autosync.get_state("x", "alice")
    assert r["status"] == "ok" and st["consecutive_errors"] == 0 and st["last_added"] == 4 and st["total_added"] == 4
    assert calls[0][2] == autosync.FIRST_RUN_MAX_ITEMS and calls[-1][2] == autosync.FIRST_RUN_MAX_ITEMS
    r = eng.run_account("x", "alice"); assert calls[-1][2] == autosync.DEFAULT_MAX_ITEMS     # small cap after the first success


def test_expired_session_needs_login_no_retry(tdb):
    autosync.set_config(enabled=True, interval_minutes=2)
    sessions.write_state(tdb / "x@old.json", _sess_doc(expires=1000))        # cookies expired long ago
    sessions.write_state(tdb / "instagram@me.json", _sess_doc())
    clk, calls = Clock(), []
    eng = _engine(tdb, [{"status": "need_login", "exit_code": EXIT_NEED_LOGIN, "detail": "redirected to login"}], clk, calls)
    res = {(r["platform"], r["account"]): r["status"] for r in eng.tick()}
    assert res == {("x", "old"): "needs_login", ("instagram", "me"): "needs_login"}
    assert calls == [("instagram", "me", autosync.FIRST_RUN_MAX_ITEMS)]        # expired cookies: no browser launched at all
    for _ in range(5):                                                         # no retry loop, however long we wait
        clk.t += 3600; assert eng.due() == []
    st = {a["account"]: a for a in autosync.status(tdb)["accounts"]}
    assert st["@me"]["state"] == "needs_login" and "login instagram --account @me" in st["@me"]["fix"]
    time.sleep(0.05); sessions.write_state(tdb / "instagram@me.json", _sess_doc())  # user logged in again -> new file
    assert eng.due() == [("instagram", "me")]


def test_scheduler_toggle_loop_and_interval_validation(tdb):
    sessions.write_state(tdb / "x@alice.json", _sess_doc())
    for bad in (1, 0, -5, 1441, "x", True, 2.5):
        with pytest.raises(ValueError): autosync.set_config(interval_minutes=bad)
    assert autosync.set_config(enabled=False, interval_minutes=2) == {"enabled": False, "interval_minutes": 2, "configured": True}
    calls = []
    eng = autosync.Engine(sessions_dir=tdb, run_fn=lambda p, a, cap, t, s: calls.append(p) or {"status": "ok", "exit_code": 0}, timeout=5)
    stop = threading.Event()
    t = threading.Thread(target=autosync.run_loop, args=(stop, eng, "test", 0.05), daemon=True); t.start()
    time.sleep(0.4); assert calls == []                                        # disabled -> nothing runs
    assert autosync.loop_info()["running"] and autosync.loop_info()["where"] == "test"
    autosync.set_config(enabled=True); time.sleep(0.5); assert calls == ["x"]  # enabled -> runs; then waits for the interval
    autosync.set_config(enabled=False); stop.set(); t.join(5)
    assert not t.is_alive() and not autosync.loop_info()["running"]
    # a second loop on the same DB cannot run at the same time (cross-process loop lock)
    l1, l2 = autosync._LoopLock(), autosync._LoopLock()
    assert l1.acquire() and not l2.acquire(); l1.release(); assert l2.acquire(); l2.release()


def test_unavailable_on_render_never_crashes(tdb, monkeypatch):
    monkeypatch.setenv("RENDER", "true"); monkeypatch.setenv("SAVEHUB_COLLECTOR", "off")
    r = autosync.start_background("server"); assert r["started"] is False and "Render" in r["reason"]
    st = autosync.status(); assert st["available"] is False and "Render" in st["reason"]
    with pytest.raises(autosync.Unavailable): autosync.request_sync()
    r = subprocess.run([sys.executable, "-m", "savehub.collector", "autosync", "--interval", "1"], cwd=ROOT, capture_output=True, text=True, timeout=60,
                       env={**os.environ, "SAVEHUB_DB": str(db.DB_PATH)})
    assert r.returncode == 1 and "between 2 and" in json.loads(r.stdout)["error"]
    r = subprocess.run([sys.executable, "-m", "savehub.collector", "autosync"], cwd=ROOT, capture_output=True, text=True, timeout=60,
                       env={**os.environ, "SAVEHUB_DB": str(db.DB_PATH), "RENDER": "true", "SAVEHUB_COLLECTOR": "off"})
    assert r.returncode == 1 and json.loads(r.stdout)["status"] == "unavailable"


def test_cli_once_no_sessions_and_never_unsaves(tdb):
    r = subprocess.run([sys.executable, "-m", "savehub.collector", "--sessions-dir", str(tdb), "autosync", "--once", "--interval", "5"],
                       cwd=ROOT, capture_output=True, text=True, timeout=60, env={**os.environ, "SAVEHUB_DB": str(db.DB_PATH)})
    out = json.loads(r.stdout); assert r.returncode == 0 and out["runs"] == [] and out["unsave"] == "never"
    cmd = autosync.build_cmd("x", "alice", 30, tdb, with_key=True)
    assert "--incremental" in cmd and "--key-stdin" in cmd and not any("unsave" in c for c in cmd)
    assert cmd[cmd.index("--account") + 1] == "alice" and cmd[cmd.index("--sink") + 1] == "sqlite"


def test_one_run_per_account(tdb):
    sessions.write_state(tdb / "x@alice.json", _sess_doc())
    with runtime.account_lock("x", "alice", tdb):
        with pytest.raises(AccountBusy): 
            with runtime.account_lock("x", "@Alice", tdb): pass
        eng = autosync.Engine(sessions_dir=tdb, run_fn=lambda *a: pytest.fail("must not run"))
        assert eng.run_account("x", "alice")["status"] == "busy"
    # another process holding the flock -> busy here
    code = ("import sys,time; sys.path.insert(0, %r); from savehub.collector import runtime; "
            "cm = runtime.account_lock('x','alice',%r); cm.__enter__(); print('locked', flush=True); time.sleep(30)") % (str(ROOT), str(tdb))
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "locked"
        with pytest.raises(AccountBusy):
            with runtime.account_lock("x", "alice", tdb): pass
    finally: p.kill(); p.wait()
    with runtime.account_lock("x", "alice", tdb): pass                       # released when the holder dies
    for bad in ("../../etc", "a/b"):
        with pytest.raises(ValueError):
            with runtime.account_lock("x", bad, tdb): pass
    with pytest.raises(ValueError):
        with runtime.account_lock("../x", "alice", tdb): pass


# ======================================================================== session files: permissions + encryption
def _mode(p): return stat.S_IMODE(os.stat(p).st_mode)


def test_session_file_permissions_plaintext(tdb):
    old = os.umask(0)                                                          # even with a permissive umask
    try: p = sessions.write_state(tdb / "x@alice.json", _sess_doc())
    finally: os.umask(old)
    assert _mode(p) == 0o600 and _mode(tdb) == 0o700 and json.loads(p.read_text())["cookies"][0]["value"] == COOKIE
    os.chmod(p, 0o644); os.chmod(tdb, 0o755)
    assert sessions.harden(tdb)["fixed"] == 2 and _mode(p) == 0o600 and _mode(tdb) == 0o700
    assert COOKIE not in json.dumps(sessions.list_sessions(tdb))
    assert not list(tdb.glob("*.tmp*"))


def test_session_encryption_roundtrip_and_migration(tdb, key):
    p = sessions.write_state(tdb / "x@alice.json", _sess_doc())
    raw = p.read_text(); doc = json.loads(raw)
    assert doc["savehub_encrypted"] == 1 and doc["enc"] == "AES-256-GCM" and COOKIE not in raw and "auth_token" not in raw
    assert _mode(p) == 0o600 and sessions.read_state(p) == _sess_doc()
    d = sessions.describe(p); assert d["status"] == "saved" and d["encrypted"] and d["cookies"] == 1 and COOKIE not in json.dumps(d)
    assert json.loads(sessions.write_state(tdb / "x@bob.json", _sess_doc()).read_text())["data"] != doc["data"]   # random nonce
    # legacy plaintext file: read fine, re-saved encrypted (describe alone never rewrites)
    legacy = tdb / "instagram@me.json"; legacy.write_text(json.dumps(_sess_doc()))
    assert sessions.describe(legacy)["encrypted"] is False and COOKIE in legacy.read_text()
    assert sessions.read_state(legacy) == _sess_doc()
    assert sessions.is_encrypted(json.loads(legacy.read_text())) and _mode(legacy) == 0o600
    # renamed onto another account -> AAD mismatch -> refused
    (tdb / "x@mallory.json").write_text(raw)
    assert sessions.describe(tdb / "x@mallory.json")["status"] == "locked"
    with pytest.raises(sessions.SessionLocked): sessions.read_state(tdb / "x@mallory.json")


def test_session_locked_without_or_with_wrong_key(tdb, key, monkeypatch):
    p = sessions.write_state(tdb / "x@alice.json", _sess_doc())
    monkeypatch.setenv("SAVEHUB_VAULT_KEY", Fernet.generate_key().decode())
    assert sessions.describe(p)["status"] == "locked"
    monkeypatch.delenv("SAVEHUB_VAULT_KEY")
    d = sessions.describe(p); assert d["status"] == "locked" and d["encrypted"]
    with pytest.raises(sessions.SessionLocked) as e: sessions.read_state(p)
    assert e.value.exit_code == 6
    # key from a 0600 key file works the same (and a loose key file is tightened)
    kf = pathlib.Path(os.environ["SAVEHUB_VAULT_KEY_FILE"]); kf.write_text(key); os.chmod(kf, 0o644)
    assert sessions.read_state(p) == _sess_doc() and _mode(kf) == 0o600 and vault.key_source() == "file"
    # passphrase keys (PBKDF2) work too
    monkeypatch.setenv("SAVEHUB_VAULT_KEY", "a long throwaway passphrase for tests")
    q = sessions.write_state(tdb / "x@pp.json", _sess_doc()); assert json.loads(q.read_text())["kdf"].startswith("pbkdf2")
    assert sessions.read_state(q) == _sess_doc()


def test_generate_key_file_never_overwrites(tmp_path):
    p = vault.generate_key_file(tmp_path / ".vault_key")
    k = p.read_text().strip(); assert _mode(p) == 0o600 and len(base64.urlsafe_b64decode(k)) == 32
    with pytest.raises(FileExistsError): vault.generate_key_file(p)
    assert p.read_text().strip() == k


def test_key_reaches_child_on_stdin_not_env(tdb, key):
    sessions.write_state(tdb / "x@alice.json", _sess_doc())
    env = runtime.child_env(db.DB_PATH)
    assert "SAVEHUB_VAULT_KEY" not in env and key not in json.dumps(env)
    base = [sys.executable, "-m", "savehub.collector", "--sessions-dir", str(tdb)]
    r = subprocess.run(base + ["sessions"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert json.loads(r.stdout)["sessions"][0]["status"] == "locked"                 # child has no key in its env
    r = subprocess.run(base[:3] + ["--key-stdin"] + base[3:] + ["sessions"], cwd=ROOT, env=env, input=key + "\n", capture_output=True, text=True, timeout=60)
    out = json.loads(r.stdout); assert out["sessions"][0]["status"] == "saved" and key not in r.stdout + r.stderr


def test_sessions_git_ignored():
    for f in ("data/sessions/x@alice.json", "data/sessions/x.json", "data/.vault_key", "data/.mcp_token", "data/.autosync.lock",
              "data/sessions/.locks/x@alice.lock"):
        assert subprocess.run(["git", "check-ignore", "-q", f], cwd=ROOT).returncode == 0, f


def test_child_env_allowlist(monkeypatch):
    for k, v in {"GITHUB_TOKEN": "ghp_x", "SAVEHUB_DASHBOARD_PASSWORD": "pw", "SAVEHUB_MCP_TOKEN": "t", "OPENAI_API_KEY": "k",
                 "AWS_SECRET_ACCESS_KEY": "s", "SAVEHUB_VAULT_KEY_FILE": "/k", "RANDOM_THING": "x", "HTTPS_PROXY": "http://u:p@proxy:1"}.items():
        monkeypatch.setenv(k, v)
    env = runtime.child_env("/tmp/x.db")
    assert not {"GITHUB_TOKEN", "SAVEHUB_DASHBOARD_PASSWORD", "SAVEHUB_MCP_TOKEN", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY",
                "SAVEHUB_VAULT_KEY_FILE", "RANDOM_THING", "HTTPS_PROXY"} & set(env)
    assert env["SAVEHUB_DB"] == "/tmp/x.db" and "PATH" in env


# ======================================================================== logs
def test_log_scrubbing(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_" + "Z" * 36); monkeypatch.setenv("SAVEHUB_DASHBOARD_PASSWORD", "hunter2-is-long")
    s = scrub(f"push with {os.environ['GITHUB_TOKEN']} pw=hunter2-is-long Authorization: Bearer abcdef123456 "
              f'{{"name": "auth_token", "value": "{COOKIE}"}} https://user:pass@github.com/x')
    for bad in ("Z" * 36, "hunter2-is-long", "abcdef123456", COOKIE, "user:pass"): assert bad not in s, s
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s"', ("127.0.0.1", "GET", "/x?t=hunter2-is-long"), None)
    ScrubFilter().filter(rec); assert isinstance(rec.args, tuple) and "hunter2-is-long" not in rec.getMessage()


# ======================================================================== REST: auth, CSRF, headers, rate limit, validation
J = {"Content-Type": "application/json"}
DASH = ("savehub", "dash-test-pw-autosync")


@pytest.fixture()
def client(tdb, monkeypatch):
    from fastapi.testclient import TestClient
    from savehub import app as appmod
    sessions.write_state(tdb / "x@alice.json", _sess_doc())
    ran = []
    monkeypatch.setattr(autosync, "spawn_collect", lambda p, a, cap, t, s: ran.append((p, a)) or {"status": "ok", "exit_code": 0, "created": 1})
    monkeypatch.setattr(autosync.Engine.__init__, "__defaults__", (None, None, None, time.time, 5, 30, 100))
    monkeypatch.setattr(runtime, "collector_runtime", lambda: {"available": True, "browser": "test"})
    return TestClient(appmod.app), TestClient(appmod.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5555)), ran


def test_autosync_endpoints_require_login_when_configured(client, monkeypatch):
    remote, local, ran = client
    monkeypatch.setenv("SAVEHUB_DASHBOARD_PASSWORD", DASH[1])
    for c in (remote, local):
        assert c.get("/api/autosync").status_code == 401
        assert c.put("/api/autosync", json={"enabled": True}).status_code == 401
        assert c.post("/api/autosync/run", json={}).status_code == 401
        assert c.post("/api/autosync/run", json={}, auth=("savehub", "wrong")).status_code == 401
    st = remote.get("/api/autosync", auth=DASH).json()
    assert st["unsave"] == "never" and st["accounts"][0]["account"] == "@alice" and COOKIE not in json.dumps(st)
    assert "session_file" not in json.dumps(st) and str(autosync.sessions.sessions_dir()) not in json.dumps(st)
    r = remote.put("/api/autosync", json={"enabled": True, "interval_minutes": 5}, auth=DASH)
    assert r.status_code == 200 and r.json()["enabled"] and r.json()["interval_minutes"] == 5
    r = remote.post("/api/autosync/run", json={"platform": "x", "account": "@alice"}, auth=DASH)
    assert r.status_code == 202 and r.json()["started"] == ["x@alice"] and r.json()["unsave"] == "off"
    time.sleep(0.3); assert ran == [("x", "alice")]


def test_autosync_endpoints_local_only_without_login(client):
    remote, local, _ = client
    assert remote.get("/api/autosync").status_code == 403                                    # not loopback
    assert remote.put("/api/autosync", json={"enabled": True}).status_code == 403
    assert local.get("/api/autosync").status_code == 200
    assert local.get("/api/autosync", headers={"Host": "evil.example.com"}).status_code == 403   # DNS rebinding
    assert local.put("/api/autosync", json={"enabled": True}).status_code == 200
    # CSRF / content-type / validation
    assert local.put("/api/autosync", json={"enabled": False}, headers={"Origin": "https://evil.example.com"}).status_code == 403
    assert local.put("/api/autosync", json={"enabled": False}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert local.put("/api/autosync", content='{"enabled": false}', headers={"Content-Type": "text/plain"}).status_code == 415
    assert local.put("/api/autosync", json={"interval_minutes": 1}).status_code == 400
    assert local.put("/api/autosync", json={"enabled": "yes"}).status_code == 400
    assert local.post("/api/autosync/run", json={"platform": "x", "unsave": True}).status_code == 400
    assert local.post("/api/autosync/run", json={"platform": "../../etc"}).status_code == 400
    assert local.post("/api/autosync/run", json={"platform": "x", "account": "../../etc/passwd"}).status_code == 400
    assert local.post("/api/autosync/run", json={"platform": "wechat"}).status_code == 400
    assert local.post("/api/autosync/run", json={"platform": "x", "account": "@nobody"}).status_code == 400
    assert local.put("/api/autosync", json={"enabled": True}, headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200


def test_sync_now_rate_limited(client):
    _, local, ran = client
    r = local.post("/api/autosync/run", json={}); assert r.status_code == 202 and r.json()["started"] == ["x@alice"]
    r = local.post("/api/autosync/run", json={}); assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    r = local.post("/api/autosync/run", json={"platform": "x", "account": "@alice"}); assert r.status_code == 429   # global gap applies too
    autosync._last_trigger.clear()
    assert local.post("/api/autosync/run", json={"platform": "x", "account": "alice"}).status_code == 202


def test_sync_now_unavailable_on_render(client, monkeypatch):
    _, local, _ = client
    monkeypatch.setattr(runtime, "collector_runtime", lambda: {"available": False, "reason": "Render: no browser"})
    assert local.post("/api/autosync/run", json={}).status_code == 503
    assert local.get("/api/autosync").json()["available"] is False


def test_security_headers_and_same_origin(tdb):
    from fastapi.testclient import TestClient
    from savehub import app as appmod
    c = TestClient(appmod.app)
    for path in ("/", "/api/health", "/static/app.js"):
        h = c.get(path).headers
        assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in h["content-security-policy"] and "object-src 'none'" in h["content-security-policy"]
    assert "no-store" in c.get("/api/health").headers["cache-control"] and "set-cookie" not in c.get("/").headers
    assert "strict-transport-security" in c.get("/api/health", headers={"X-Forwarded-Proto": "https"}).headers
    item = {"url": "https://example.com/csrf-test", "title": "t"}
    assert c.post("/api/items", json=item, headers={"Origin": "https://evil.example.com"}).status_code == 403
    assert c.post("/api/items", json=item, headers={"Origin": "null"}).status_code == 403
    assert c.delete("/api/items/1", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/api/items", json=item, headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert c.post("/api/items", json={**item, "url": "https://example.com/2"}).status_code == 200     # non-browser client


def test_standalone_app_enforces_login(tdb, monkeypatch):
    from fastapi.testclient import TestClient
    from savehub import app as appmod
    monkeypatch.setenv("SAVEHUB_DASHBOARD_PASSWORD", DASH[1])
    c = TestClient(appmod.basic_auth_gate(appmod.app))
    assert c.get("/").status_code == 401 and c.get("/api/items").status_code == 401
    assert c.get("/api/health", auth=DASH).status_code == 200


# ======================================================================== MCP
def arun(coro):
    """asyncio.run in a fresh thread (the module's Playwright sync fixture owns this thread's event loop)."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex: return ex.submit(asyncio.run, coro).result()


def test_mcp_tools_auth_and_validation(tdb, monkeypatch):
    from starlette.testclient import TestClient
    from savehub import mcp_server as m
    tools = {t.name: t for t in arun(m.mcp.list_tools())}
    assert list(tools["sync_status"].inputSchema.get("properties", {})) == []
    assert list(tools["set_autosync"].inputSchema["properties"]) == ["enabled", "interval_minutes"]
    assert tools["set_autosync"].inputSchema["required"] == ["enabled"]
    assert "error" in m.set_autosync(True, 1) and "error" in m.set_autosync(True, 100000)
    r = m.set_autosync(True, 3); assert r["enabled"] and r["interval_minutes"] == 3 and r["unsave"] == "never"
    sessions.write_state(tdb / "x@alice.json", _sess_doc())
    st = m.sync_status(); assert st["accounts"][0]["account"] == "@alice" and COOKIE not in json.dumps(st)
    assert m.set_autosync(False)["enabled"] is False and autosync.get_config()["interval_minutes"] == 3
    # HTTP transport: every MCP request (incl. these tools) needs the bearer token
    c = TestClient(m.BearerAuth(m.mcp.streamable_http_app(), "tok-autosync-test"))
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "set_autosync", "arguments": {"enabled": True}}}
    H = {"Accept": "application/json, text/event-stream"}
    assert c.post("/mcp", json=call, headers=H).status_code == 401
    assert c.post("/mcp", json=call, headers={**H, "Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/mcp", json=call, headers={**H, "Authorization": "tok-autosync-test"}).status_code == 401
    assert autosync.get_config()["enabled"] is False


def test_mcp_import_file_path_guard(tdb, tmp_path):
    from savehub import mcp_server as m
    sessions.write_state(tdb / "x@alice.json", _sess_doc())
    kf = db.DB_PATH.parent / ".vault_key"; kf.write_text("k" * 44)
    hidden = tmp_path / ".ssh"; hidden.mkdir(); (hidden / "id").write_text("x")
    link = tmp_path / "innocent.json"; link.symlink_to(tdb / "x@alice.json")
    for p in (tdb / "x@alice.json", kf, db.DB_PATH, hidden / "id", link, "/etc/passwd", "/proc/self/environ", str(tdb / ".." / "sessions" / "x@alice.json")):
        r = m.import_file(path=str(p)); assert "error" in r and COOKIE not in json.dumps(r), p
    assert "error" not in m.import_file(path=str(ROOT / "samples" / "tiktok_user_data.json"), kind="tiktok_json")


def test_mcp_token_file_private_and_not_printed(tdb, monkeypatch, capsys):
    from savehub import mcp_server as m
    monkeypatch.delenv("SAVEHUB_MCP_TOKEN", raising=False)
    t = m.mcp_token(); out = capsys.readouterr().out
    f = db.DB_PATH.parent / ".mcp_token"
    assert t and t not in out and _mode(f) == 0o600 and m.mcp_token() == t
