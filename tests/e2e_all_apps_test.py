"""ONE end-to-end integration test of the whole collector wiring, for every implemented app and two accounts each.

Fully isolated: temp data dir + temp DB + throwaway vault key + throwaway MCP token / dashboard password + temp sessions
dir. The real combined server (python -m savehub.server) runs on a free loopback port. Collections are started through
every real entry point and run in the real collector subprocesses:
    (a) MCP `collect` over streamable HTTP with the bearer token
    (b) REST "Sync now" (POST /api/autosync/run, dashboard login)
    (c) `python -m savehub.collector autosync --once`
No request reaches the internet: a sitecustomize.py (only on the PYTHONPATH of the processes this test starts) installs a
Playwright context route in every collector child that answers the apps' hosts from tests/fixtures (the same fakes as
collector_test.py / collector_platforms_test.py) and aborts everything else. Account @b gets the same pages with
rewritten ids, so @a and @b have disjoint saves. Library backends (twscrape / instaloader) are replaced by mocks that
fail, so X `auto` must fall back to Playwright. Un-saves on the fake apps are logged to a file the test inspects.

Run:  .venv/bin/python -m pytest -q -s tests/e2e_all_apps_test.py      (about 3-6 minutes; prints the pass/fail matrix)
"""
from __future__ import annotations
import asyncio, json, os, pathlib, re, secrets, shutil, socket, sqlite3, subprocess, sys, tempfile, time, types
from urllib.parse import urlparse, parse_qs

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
PLATFORMS = ["x", "threads", "instagram", "tiktok", "douyin", "xiaohongshu", "facebook", "messenger", "whatsapp"]
CONTACT_PLATFORMS = {"messenger", "whatsapp"}
SAVE_PLATFORMS = [p for p in PLATFORMS if p not in CONTACT_PLATFORMS]
ACCOUNTS = ["a", "b"]
DOMAINS = {"x": ".x.com", "threads": ".threads.com", "instagram": ".instagram.com", "tiktok": ".tiktok.com", "douyin": ".douyin.com",
           "xiaohongshu": ".xiaohongshu.com", "facebook": ".facebook.com", "messenger": ".messenger.com", "whatsapp": "web.whatsapp.com"}
HOSTS = {"x": {"x.com", "twitter.com"}, "threads": {"www.threads.com", "www.threads.net", "threads.com"},
         "instagram": {"www.instagram.com", "instagram.com"}, "tiktok": {"www.tiktok.com", "tiktok.com"},
         "douyin": {"www.douyin.com", "douyin.com"}, "xiaohongshu": {"www.xiaohongshu.com", "xiaohongshu.com"},
         "facebook": {"www.facebook.com", "facebook.com"}, "messenger": {"www.messenger.com", "messenger.com"},
         "whatsapp": {"web.whatsapp.com"}}
HOST2P = {h: p for p, hs in HOSTS.items() for h in hs}
# account @b sees the same fixtures with every saved-item id rewritten (same id shapes), so @a / @b saves are disjoint
B_SUBS = {
    "x": [(r"18400000000000000(\d\d)", r"28400000000000000\1")],
    "threads": [(r"DThr([A-Z]\d)", r"DThb\1")],
    "instagram": [(r"IG([a-z]{3}\d)", r"IGb\1")],
    "tiktok": [(r"73000000000000000(\d\d)", r"73100000000000000\1")],
    "douyin": [(r"74000000000000000(\d\d)", r"74100000000000000\1")],
    "xiaohongshu": [(r"670000000000000000000", "671000000000000000000")],
    "facebook": [("pfbid0ABC", "pfbid0XYZ"), ("nasi-lemak", "roti-canai"), ("999000111", "999000222")],
    "messenger": [(r"\b10000([14])\b", r"20000\1"), ("7000123", "7000456"), (r"ben\.tan", "ben.lee")],
    "whatsapp": [("60120000", "60130000"), ("120363000000000001", "120363000000000002"), ("Mum", "Mom"),
                 ("12-000 0003", "13-000 0003"), ("Jalan Alor", "Jalan Bukit"), ("kopi-1234", "kopi-5678")],
}
FAST = dict(scroll_delay=0.15, scroll_jitter=0.05, idle_rounds=3, unsave_delay=0.05, page_timeout_ms=20000)


# ====================================================================== child harness (inside collector subprocesses)
class _SubRoute:
    """A Playwright Route whose fulfill() rewrites ids for account @b."""
    def __init__(self, route, subs): self._r, self._subs = route, subs

    @property
    def request(self): return self._r.request

    def abort(self, *a, **k): return self._r.abort(*a, **k)

    def fulfill(self, **kw):
        if self._subs and isinstance(kw.get("body"), str):
            b = kw["body"]
            for rx, rep in self._subs: b = re.sub(rx, rep, b)
            kw["body"] = b
        return self._r.fulfill(**kw)


class _Router:
    """One context route for all fake apps (by host). Unknown hosts (CDNs, anything real) are aborted = offline."""
    def __init__(self, cfg):
        self.log_path, self.fakes = cfg["log"], {}
        argv = sys.argv
        self.account = argv[argv.index("--account") + 1].lstrip("@").lower() if "--account" in argv else "default"
        self.platform = argv[argv.index("collect") + 1] if "collect" in argv else ""

    def log(self, event, **kw):
        line = json.dumps({"t": time.time(), "pid": os.getpid(), "event": event, "cmd_platform": self.platform,
                           "account": self.account, **kw}) + "\n"
        fd = os.open(self.log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try: os.write(fd, line.encode())
        finally: os.close(fd)

    def fake(self, p):
        if p not in self.fakes:
            if str(TESTS) not in sys.path: sys.path.insert(0, str(TESTS))
            if p == "x":
                import collector_test as ct
                f = ct.FakeX(logged_in=self.account != "c")           # @c: the app itself says "log in" (cookies look valid)
            else:
                import collector_platforms_test as cpt
                f = {"threads": cpt.threads_fake, "instagram": cpt.ig_fake, "tiktok": cpt.tt_fake, "douyin": cpt.dy_fake,
                     "xiaohongshu": cpt.xhs_fake, "facebook": cpt.fb_fake, "messenger": cpt.ms_fake, "whatsapp": cpt.wa_fake}[p]()
                # the fixtures use the username js.sample; our accounts are @a / @b
                f.pages = [(rx.replace(r"js\.sample", r"[\w.-]+"), fn) for rx, fn in f.pages]
            self.fakes[p] = f
        return self.fakes[p]

    def handle(self, route):
        u = urlparse(route.request.url)
        p = HOST2P.get(u.hostname or "")
        if p is None:
            if u.scheme in ("http", "https"): self.log("blocked", host=u.hostname)
            return route.abort()
        q = parse_qs(u.query)
        if u.path in ("/__test/unsave", "/__test/unbookmark"):
            self.log("unsave", platform=p, id=(q.get("id") or [""])[0])
        else:
            self.log("request", platform=p, path=u.path)
        return self.fake(p).handle(_SubRoute(route, B_SUBS.get(p, []) if self.account == "b" else []))


def install_child_harness(cfg: dict) -> None:
    """Called by the generated sitecustomize.py. Only collector `collect` processes are patched."""
    if "collect" not in sys.argv: return
    from savehub.collector import runner, backends, base
    router = _Router(cfg)
    orig_new_context = runner.new_context

    def new_context(browser, storage_state=None):
        ctx = orig_new_context(browser, storage_state)
        ctx.route("**/*", router.handle)
        return ctx
    runner.new_context = new_context

    orig_from_dict = base.RunConfig.from_dict.__func__
    base.RunConfig.from_dict = classmethod(lambda cls, d=None: orig_from_dict(cls, {**FAST, **(d or {})}))

    def load(platform, name):
        backends.info(platform, name)                                   # same validation as the real loader

        def collect(state, cfg, account):
            router.log("library_called", platform=platform, backend=name, cookies=sorted(c["name"] for c in state.get("cookies", [])))
            raise RuntimeError(f"{name} (mocked): HTTP 401 Could not authenticate you")
        return types.SimpleNamespace(collect=collect)
    backends.load = load

    from savehub.collector import logutil
    orig_ev = logutil.ev
    def ev(name, *a, **kw):                     # mirror the collector's stop reasons into the harness log
        if name.endswith((".incremental_stop", ".end_of_list", ".cap_reached")) or name.startswith("backend."):
            router.log("collector_event", name=name)
        return orig_ev(name, *a, **kw)
    for m in list(sys.modules.values()):
        if getattr(m, "__name__", "").startswith("savehub.collector") and getattr(m, "ev", None) is orig_ev: m.ev = ev
    logutil.ev = ev
    router.log("child_start", argv=[a for a in sys.argv if a != "--key-stdin"])


SITECUSTOMIZE = """# generated by tests/e2e_all_apps_test.py (temp dir only)
import json, os, sys
try:
    _c = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2e_harness.json")))
    for _p in (_c["tests"], _c["root"]):
        if _p not in sys.path: sys.path.insert(0, _p)
    if "collect" in sys.argv:
        import e2e_all_apps_test as _h
        _h.install_child_harness(_c)
except Exception as _e:
    sys.stderr.write("e2e harness failed: %r\\n" % (_e,))
    raise
"""


# ====================================================================== test side
import httpx, pytest                                              # noqa: E402  (not needed by the child harness)

MATRIX: dict = {}
ENTRIES = ["mcp_collect", "mcp_repeat_dedupe", "autosync_once", "rest_sync_now", "incremental_stop", "account_links", "search", "unsave_guard"]


def mark(p, entry, ok, detail=""):
    MATRIX.setdefault(p, {})[entry] = ("PASS" if ok else "FAIL") + (f" ({detail})" if detail and not ok else "")
    return ok


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def session_state(p, acct, expired=False):
    exp = time.time() - 86400 if expired else time.time() + 30 * 86400
    names = ["auth_token", "ct0"] if p == "x" else ["sessionid", "csrftoken"]
    return {"cookies": [{"name": n, "value": f"e2e-cookie-{p}-{acct}-{n}", "domain": DOMAINS[p], "path": "/", "expires": exp,
                         "httpOnly": True, "secure": True, "sameSite": "None"} for n in names], "origins": []}


class E2E:
    def __init__(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="savehub-e2e-"))
        self.data, self.sessions, self.harness = self.tmp / "data", self.tmp / "sessions", self.tmp / "harness"
        for d in (self.data, self.harness): d.mkdir(mode=0o700)
        self.db = self.data / "e2e.db"
        self.key_file = self.data / ".vault_key"
        self.log = self.harness / "fake_apps.jsonl"; self.log.touch(mode=0o600)
        self.token, self.user, self.password = "mcp-" + secrets.token_urlsafe(24), "e2e-admin", "pw-" + secrets.token_urlsafe(18)
        self.port = _free_port(); self.base = f"http://127.0.0.1:{self.port}"
        self.full = {}                       # (platform, account) -> items collected by a full (non-incremental) run
        self.secrets_seen = []

    # -------------------------------------------------- setup / teardown
    def start(self):
        (self.harness / "sitecustomize.py").write_text(SITECUSTOMIZE)
        (self.harness / "e2e_harness.json").write_text(json.dumps({"root": str(ROOT), "tests": str(TESTS), "log": str(self.log)}))
        self._saved_env = {k: os.environ.get(k) for k in ("SAVEHUB_VAULT_KEY", "SAVEHUB_VAULT_KEY_FILE", "SAVEHUB_DB", "SAVEHUB_SESSIONS_DIR")}
        os.environ.pop("SAVEHUB_VAULT_KEY", None)                      # this process too: never the real key / DB / sessions
        os.environ.update(SAVEHUB_VAULT_KEY_FILE=str(self.key_file), SAVEHUB_DB=str(self.db), SAVEHUB_SESSIONS_DIR=str(self.sessions))
        sys.path.insert(0, str(ROOT))
        from savehub import vault
        from savehub.collector import sessions
        vault.generate_key_file(self.key_file)
        assert sessions.encryption_enabled()
        self.sess_mod = sessions
        for p in PLATFORMS:
            for a in ACCOUNTS: sessions.write_state(sessions.session_path(p, self.sessions, a), session_state(p, a))
        sessions.write_state(sessions.session_path("x", self.sessions, "old"), session_state("x", "old", expired=True))   # expired cookies
        sessions.write_state(sessions.session_path("x", self.sessions, "c"), session_state("x", "c"))                     # app logs it out
        env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "PORT", "SAVEHUB_AUTOSYNC", "RENDER", "RENDER_EXTERNAL_HOSTNAME",
                                                                "SAVEHUB_ENABLED_PLATFORMS", "SAVEHUB_COLLECTOR", "SAVEHUB_URL")}
        env.update(SAVEHUB_DB=str(self.db), SAVEHUB_SESSIONS_DIR=str(self.sessions), SAVEHUB_VAULT_KEY_FILE=str(self.key_file),
                   SAVEHUB_MCP_TOKEN=self.token, SAVEHUB_DASHBOARD_USER=self.user, SAVEHUB_DASHBOARD_PASSWORD=self.password,
                   SAVEHUB_HOST="127.0.0.1", SAVEHUB_PORT=str(self.port), SAVEHUB_SEED="0", SAVEHUB_COLLECT_TIMEOUT="240",
                   SAVEHUB_AUTOSYNC_TIMEOUT="240", PYTHONPATH=os.pathsep.join([str(self.harness), str(ROOT)]))
        self.env = env
        self.server_log = open(self.tmp / "server.log", "w+")
        self.proc = subprocess.Popen([sys.executable, "-m", "savehub.server"], cwd=ROOT, env=env, stdout=self.server_log, stderr=subprocess.STDOUT)
        for _ in range(120):
            try:
                if httpx.get(self.base + "/healthz", timeout=1).status_code == 200: break
            except Exception: pass
            time.sleep(0.25)
        else:
            raise RuntimeError("server did not start: " + (self.tmp / "server.log").read_text()[-2000:])

    def stop(self):
        try:
            self.proc.terminate(); self.proc.wait(15)
        except Exception:
            self.proc.kill()
        self.server_log.close()
        for k, v in self._saved_env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

    # -------------------------------------------------- helpers
    def rest(self, method, path, auth=True, **kw):
        return httpx.request(method, self.base + path, auth=(self.user, self.password) if auth else None, timeout=60, **kw)

    def sql(self, q, args=()):
        c = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True); c.row_factory = sqlite3.Row
        try: return [dict(r) for r in c.execute(q, args)]
        finally: c.close()

    def events(self, since=0.0, **match):
        out = []
        for line in self.log.read_text().splitlines():
            e = json.loads(line)
            if e["t"] >= since and all(e.get(k) == v for k, v in match.items()): out.append(e)
        return out

    async def _mcp(self, calls, token=None):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token or self.token}"},
                                     timeout=httpx.Timeout(60, read=900)) as hc, \
                streamable_http_client(self.base + "/mcp", http_client=hc) as (rd, wr, _):
            async with ClientSession(rd, wr) as s:
                await s.initialize()
                out = []
                for name, args in calls:
                    r = await s.call_tool(name, args)
                    txt = r.content[0].text if r.content else ""
                    self.secrets_seen.append(txt)
                    out.append(json.loads(txt) if txt[:1] in "{[" else txt)
                return out

    def mcp(self, name, args=None):
        return asyncio.run(self._mcp([(name, args or {})]))[0]

    def mcp_parallel(self, calls, concurrency=3):
        async def run():
            sem = asyncio.Semaphore(concurrency)
            async def one(c):
                async with sem: return (await self._mcp([c]))[0]
            return await asyncio.gather(*(one(c) for c in calls))
        return asyncio.run(run())

    def account_row(self, p, a):
        r = self.sql("SELECT * FROM accounts WHERE platform=? AND lower(handle)=?", (p, "@" + a))
        return r[0] if r else None

    def items_of(self, p, a):
        return self.sql("SELECT * FROM items WHERE platform=? AND source=? ORDER BY id", (p, f"collector:{p}:{a}"))

    def delete_items(self, p, a):
        for it in self.items_of(p, a):
            assert self.rest("DELETE", f"/api/items/{it['id']}").status_code == 200
        assert self.items_of(p, a) == []

    def state(self, p, a):
        r = self.sql("SELECT * FROM sync_state WHERE platform=? AND account=?", (p, a))
        return r[0] if r else {}

    def autosync_once(self):
        env = {k: v for k, v in self.env.items() if k not in ("SAVEHUB_DASHBOARD_PASSWORD", "SAVEHUB_MCP_TOKEN")}
        r = subprocess.run([sys.executable, "-m", "savehub.collector", "--sessions-dir", str(self.sessions), "autosync", "--once", "--db", str(self.db)],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=1800)
        self.secrets_seen += [r.stdout, r.stderr]
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        return json.loads(r.stdout)


@pytest.fixture(scope="module")
def e2e():
    t = E2E(); t.start()
    real_sessions = ROOT / "data" / "sessions"
    before = sorted((f.name, f.stat().st_mtime, f.stat().st_size) for f in real_sessions.glob("*")) if real_sessions.exists() else None
    try:
        yield t
    finally:
        t.stop()
        after = sorted((f.name, f.stat().st_mtime, f.stat().st_size) for f in real_sessions.glob("*")) if real_sessions.exists() else None
        assert before == after, "real data/sessions changed during the e2e test"
        shutil.rmtree(t.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------------------------- 0. isolation + sessions
def test_00_isolated_encrypted_sessions(e2e):
    files = sorted(f.name for f in e2e.sessions.glob("*.json"))
    assert len(files) == 2 * len(PLATFORMS) + 2 and "x@a.json" in files and "whatsapp@b.json" in files
    for f in e2e.sessions.glob("*.json"):
        doc = json.loads(f.read_text())
        assert doc.get("savehub_encrypted") == 1 and "e2e-cookie" not in f.read_text()          # encrypted at rest
        assert (f.stat().st_mode & 0o777) == 0o600
    assert (e2e.sessions.stat().st_mode & 0o777) == 0o700
    assert str(e2e.db).startswith(str(e2e.tmp)) and not str(e2e.db).startswith(str(ROOT))
    h = e2e.rest("GET", "/api/health").json()
    assert h["stats"]["items"] == 0 and h["stats"]["accounts"] == 0                                 # empty temp DB, no seed


# ---------------------------------------------------------------------------------------------- 6. auth / CSRF / headers
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "e2e", "version": "1"}}}
MCP_HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_01_auth_csrf_headers(e2e):
    b = e2e.base
    assert httpx.post(b + "/mcp", json=INIT, headers=MCP_HDR).status_code == 401
    assert httpx.post(b + "/mcp", json=INIT, headers={**MCP_HDR, "Authorization": "Bearer nope"}).status_code == 401
    assert httpx.post(b + "/mcp/", json=INIT, headers=MCP_HDR).status_code == 401
    assert httpx.post(b + "/mcp", json=INIT, headers={**MCP_HDR, "Authorization": f"Basic {e2e.token}"}).status_code == 401
    ok = httpx.post(b + "/mcp", json=INIT, headers={**MCP_HDR, "Authorization": f"Bearer {e2e.token}"})
    assert ok.status_code == 200
    assert httpx.post(b + "/mcp", json=INIT, headers={**MCP_HDR, "Authorization": f"Bearer {e2e.token}", "Host": "evil.example"}).status_code == 421
    with pytest.raises(Exception):                                                                  # SDK client with a wrong token
        asyncio.run(e2e._mcp([("list_platforms", {})], token="wrong-token"))
    for path in ("/", "/api/items", "/api/autosync", "/api/health", "/static/app.js"):
        r = httpx.get(b + path); assert r.status_code == 401 and r.headers.get("www-authenticate", "").startswith("Basic"), path
    assert httpx.get(b + "/api/items", auth=(e2e.user, "wrong")).status_code == 401
    assert httpx.post(b + "/api/autosync/run", json={}).status_code == 401
    assert httpx.get(b + "/healthz").status_code == 200
    # cross-site state-changing requests are refused even with valid credentials
    for hdr in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
        assert e2e.rest("POST", "/api/items", json={"url": "https://example.com/csrf"}, headers=hdr).status_code == 403, hdr
        assert e2e.rest("POST", "/api/autosync/run", json={}, headers=hdr).status_code == 403, hdr
        assert e2e.rest("PUT", "/api/autosync", json={"enabled": True}, headers=hdr).status_code == 403, hdr
    assert e2e.rest("PUT", "/api/autosync", content=b'{"enabled": true}', headers={"Content-Type": "text/plain"}).status_code == 415
    assert e2e.rest("GET", "/api/items", params={"q": "csrf"}).json()["total"] == 0
    for path in ("/", "/api/items"):
        h = e2e.rest("GET", path).headers
        assert "default-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer"
        assert "camera=()" in h["permissions-policy"] and h["cross-origin-opener-policy"] == "same-origin"
        assert h["cross-origin-resource-policy"] == "same-origin" and "set-cookie" not in h
    assert e2e.rest("GET", "/api/items").headers["cache-control"].startswith("no-store")


# ---------------------------------------------------------------------------------------------- 3. status tools before any run
def test_02_status_tools_before(e2e):
    lp = e2e.mcp("list_platforms")
    assert lp["collector_runtime"]["available"] is True
    byp = {p["id"]: p for p in lp["platforms"]}
    for p in PLATFORMS:
        c = byp[p]["collector"]
        accts = {a["account"]: a for a in c["accounts"]}
        assert c["implemented"] is True and accts["@a"]["session"] == "saved" and accts["@b"]["session"] == "saved", (p, c)
        assert c["logged_in_accounts"] == (3 if p == "x" else 2)
    assert {a["account"]: a["session"] for a in byp["x"]["collector"]["accounts"]}["@old"] == "expired"
    assert byp["wechat"]["collector"]["implemented"] is False
    ls = e2e.mcp("login_status", {"platform": "x"})
    st = {a["account"]: (a["session"], a["logged_in"]) for a in ls["accounts"]}
    assert st == {"@a": ("saved", True), "@b": ("saved", True), "@c": ("saved", True), "@old": ("expired", False)}, st
    assert ls["backend"]["backend"] == "auto" and ls["backend"]["effective"] == ["twscrape", "playwright"]
    miss = e2e.mcp("login_status", {"platform": "instagram", "account": "@ghost"})
    assert miss["accounts"][0]["session"] == "missing" and miss["logged_in"] is False and "login instagram --account @ghost" in miss["fix"]
    old = e2e.mcp("login_status", {"platform": "x", "account": "@old"})
    assert old["accounts"][0]["session"] == "expired" and old["logged_in"] is False and "login x --account @old" in old["fix"]
    bad = e2e.mcp("login_status", {"platform": "x", "account": "../../etc/passwd"})
    assert "error" in bad
    ss = e2e.mcp("sync_status")
    rows = {(a["platform"], a["account"]): a for a in ss["accounts"]}
    assert len(rows) == 2 * len(PLATFORMS) + 2 and ss["unsave"] == "never" and ss["available"] is True
    assert rows[("x", "@old")]["state"] == "needs_login" and rows[("x", "@old")]["session"] == "expired" and "fix" in rows[("x", "@old")]
    assert all(r["state"] == "pending" for k, r in rows.items() if k != ("x", "@old"))


# ---------------------------------------------------------------------------------------------- 2a. MCP collect (+ 5. X fallback)
def test_03_mcp_collect_all_platforms_two_accounts(e2e):
    t0 = time.time()
    res = e2e.mcp_parallel([("collect", {"platform": p}) for p in PLATFORMS])
    for p, r in zip(PLATFORMS, res):
        runs = {x["account"]: x for x in r["runs"]}
        ok = r["unsave"] == "off" and set(runs) >= {"@a", "@b"}
        for a in ACCOUNTS:
            x = runs.get("@" + a, {})
            good = x.get("status") == "ok" and x.get("exit_code") == 0 and x.get("collected", 0) > 0 and x.get("created") == x.get("collected") \
                and x.get("unsave_mode") == "off" and x.get("backend") == "playwright" and "session_file" not in x
            ok &= good
            e2e.full[(p, a)] = x.get("collected", 0)
            assert good, (p, a, {k: v for k, v in x.items() if k != "items"})
        if p == "x":
            assert r["status"] == "partial" and r["skipped_accounts"] == ["@old"], r
            assert runs["@c"]["status"] == "need_login" and runs["@c"]["exit_code"] == 2 and "login x --account @c" in runs["@c"]["fix"]
            for a in ACCOUNTS:                                                                 # 5. auto: twscrape (mocked) failed -> Playwright
                fb = runs["@" + a].get("fallback") or []
                assert runs["@" + a]["backend_setting"] == "auto" and fb and fb[0]["backend"] == "twscrape" and "mocked" in fb[0]["error"], runs["@" + a]
                assert e2e.events(t0, event="library_called", account=a, backend="twscrape")
            assert not e2e.events(t0, event="request", account="old")                         # expired: no browser run at all
        else:
            assert r["status"] == "ok" and r["skipped_accounts"] == [], r
            assert not any(x.get("fallback") for x in r["runs"])
        mark(p, "mcp_collect", ok)
    assert mark("x", "x_auto_fallback", True)
    assert not e2e.events(t0, event="library_called", backend="instaloader")                  # instagram default = playwright
    assert not e2e.events(t0, event="unsave")
    # every account got its own row (connected_via=collector) and its items are linked + tagged
    for p in PLATFORMS:
        urls = {}
        for a in ACCOUNTS:
            row = e2e.account_row(p, a); its = e2e.items_of(p, a)
            ok = bool(row) and row["connected_via"] == "collector" and len(its) == e2e.full[(p, a)] \
                and all(i["account_id"] == row["id"] and i["source"] == f"collector:{p}:{a}" for i in its)
            assert ok, (p, a, row, [(i["url"], i["account_id"], i["source"]) for i in its])
            urls[a] = {i["url"] for i in its}
            raw = [json.loads(i["raw"] or "{}") for i in its]
            assert all(r.get("source_account") == "@" + a for r in raw)
            if p in CONTACT_PLATFORMS:
                assert all(r.get("kind") in ("contact", "group", "chat", "starred_message") for r in raw)
                # people/groups are mirrored into the contacts table, linked to the same account
                people = [i for i, r in zip(its, raw) if r.get("kind") in ("contact", "group", "chat")]
                cs = e2e.rest("GET", "/api/contacts", params={"platform": p, "account_id": row["id"]}).json()
                assert cs["total"] == len(people) > 0 and {c["name"] for c in cs["contacts"]} == {i["title"] for i in people}, (p, a, cs)
                assert all(c["source"] == f"collector:{p}:{a}" and c["account_name"] == "@" + a for c in cs["contacts"])
                if p == "whatsapp":
                    assert any(c["phones"] for c in cs["contacts"])
        disjoint = not (urls["a"] & urls["b"])
        assert disjoint, (p, urls["a"] & urls["b"])
        mark(p, "account_links", True)
    # repeat: de-duplicated (nothing new, everything reported as duplicate)
    res = e2e.mcp_parallel([("collect", {"platform": p}) for p in PLATFORMS])
    for p, r in zip(PLATFORMS, res):
        runs = {x["account"]: x for x in r["runs"]}
        ok = all(runs["@" + a]["status"] == "ok" and runs["@" + a]["created"] == 0 and runs["@" + a]["duplicates"] == e2e.full[(p, a)]
                 for a in ACCOUNTS)
        ok &= all(len(e2e.items_of(p, a)) == e2e.full[(p, a)] for a in ACCOUNTS)
        assert mark(p, "mcp_repeat_dedupe", ok), (p, r)


# ---------------------------------------------------------------------------------------------- 2c. autosync --once
def test_04_autosync_once(e2e):
    for p in PLATFORMS: e2e.delete_items(p, "a")                         # @a must be re-created by auto-sync, @b is all known
    t0 = time.time()
    out = e2e.autosync_once()
    assert out["status"] == "ok" and out["unsave"] == "never"
    runs = {(r["platform"], r["account"]): r for r in out["runs"]}
    # x@old (expired cookies) and x@c (login wall) were already marked needs_login by the MCP collect in test_03, so auto-sync
    # does not even try them (before the fix, MCP runs left sync_state untouched and auto-sync had to find out again)
    for a in ("old", "c"):
        st = e2e.state("x", a)
        assert ("x", a) not in runs and st["status"] == "needs_login" and st["last_reason"] == "mcp", (runs.get(("x", a)), st)
    for p in PLATFORMS:
        ra, rb = runs.get((p, "a"), {}), runs.get((p, "b"), {})
        row = e2e.account_row(p, "a")
        ok = ra.get("status") == "ok" and ra.get("added") == e2e.full[(p, "a")] and rb.get("status") == "ok" and rb.get("added") == 0
        ok &= len(e2e.items_of(p, "a")) == e2e.full[(p, "a")] and all(i["account_id"] == row["id"] for i in e2e.items_of(p, "a"))
        ok &= e2e.state(p, "a")["status"] == "ok" and e2e.state(p, "b")["status"] == "ok"
        assert mark(p, "autosync_once", ok), (p, ra, rb)
    for e in e2e.events(t0, event="child_start"):
        assert not any("unsave" in a for a in e["argv"]) and "--incremental" in e["argv"], e
    assert not e2e.events(t0, event="unsave")
    assert not e2e.events(t0, event="request", account="old")
    assert e2e.state("x", "old")["status"] == "needs_login" and e2e.state("x", "c")["status"] == "needs_login"
    # second pass: needs-login accounts are NOT retried (no loop); everything else is incremental with nothing new
    t1 = time.time()
    out2 = e2e.autosync_once()
    runs2 = {(r["platform"], r["account"]): r for r in out2["runs"]}
    assert ("x", "old") not in runs2 and ("x", "c") not in runs2, runs2
    assert not e2e.events(t1, event="child_start", account="c") and not e2e.events(t1, account="old")
    for p in PLATFORMS:
        inc_ok = True
        for a in ACCOUNTS:
            r = runs2[(p, a)]
            stops = {e["name"].rsplit(".", 1)[1] for e in e2e.events(t1, event="collector_event", cmd_platform=p, account=a)}
            # stopped because 3 already-stored items showed up (not because the list ended), nothing new pushed
            inc_ok &= r["status"] == "ok" and r["added"] == 0 and r["collected"] <= e2e.full[(p, a)] and "incremental_stop" in stops
            assert inc_ok, (p, a, r, stops)
        assert mark(p, "incremental_stop", inc_ok), (p, runs2[(p, "a")], runs2[(p, "b")], e2e.full[(p, "a")])


# ---------------------------------------------------------------------------------------------- 2b. REST "Sync now"
def _wait_synced(e2e, targets, t0, timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        st = {t: e2e.state(*t) for t in targets}
        if all(s and float(s.get("last_run") or 0) >= t0 and not s.get("running") and s.get("last_reason") == "manual" for s in st.values()):
            return st
        time.sleep(1)
    raise AssertionError(f"sync now did not finish: {st}")


def test_05_rest_sync_now(e2e):
    for p in PLATFORMS: e2e.delete_items(p, "b")
    # guards first: un-save can never be requested here, bad input refused, stub refused
    assert e2e.rest("POST", "/api/autosync/run", json={"platform": "x", "account": "@a", "unsave": True}).status_code == 400
    assert e2e.rest("POST", "/api/autosync/run", json={"platform": "x", "account": "../a"}).status_code == 400
    assert e2e.rest("POST", "/api/autosync/run", json={"platform": "wechat"}).status_code == 400
    assert e2e.rest("POST", "/api/autosync/run", json={"platform": "x", "account": "@ghost"}).status_code == 400
    t0 = time.time()
    for i in range(0, len(PLATFORMS), 2):                                 # 2 platforms x 2 accounts at a time
        batch = PLATFORMS[i:i + 2]; tb = time.time()
        for p in batch:
            for a in ACCOUNTS:
                r = e2e.rest("POST", "/api/autosync/run", json={"platform": p, "account": "@" + a})
                assert r.status_code == 202 and r.json()["unsave"] == "off" and r.json()["started"] == [f"{p}@{a}"], (p, a, r.text)
        _wait_synced(e2e, [(p, a) for p in batch for a in ACCOUNTS], tb)
    r = e2e.rest("POST", "/api/autosync/run", json={"platform": "x", "account": "@a"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0                       # rate limit per account
    r = e2e.rest("POST", "/api/autosync/run", json={"platform": "x", "account": "@old"})    # expired: needs_login, no browser
    assert r.status_code == 202
    _wait_synced(e2e, [("x", "old")], time.time() - 1)
    for p in PLATFORMS:
        sa, sb = e2e.state(p, "a"), e2e.state(p, "b")
        row = e2e.account_row(p, "b")
        ok = sb["status"] == "ok" and sb["last_added"] == e2e.full[(p, "b")] and sa["status"] == "ok" and sa["last_added"] == 0
        ok &= len(e2e.items_of(p, "b")) == e2e.full[(p, "b")] and all(i["account_id"] == row["id"] for i in e2e.items_of(p, "b"))
        assert mark(p, "rest_sync_now", ok), (p, sa, sb)
    for e in e2e.events(t0, event="child_start"):
        assert not any("unsave" in a for a in e["argv"]), e
    assert not e2e.events(t0, event="unsave") and not e2e.events(t0, account="old")
    st = e2e.rest("GET", "/api/autosync").json()
    rows = {(a["platform"], a["account"]): a for a in st["accounts"]}
    assert rows[("x", "@old")]["state"] == "needs_login" and rows[("x", "@c")]["state"] == "needs_login"
    assert all(rows[(p, "@" + a)]["state"] == "synced" for p in PLATFORMS for a in ACCOUNTS)


# ---------------------------------------------------------------------------------------------- search (EN + 中文)
def _word(title):
    """A search term taken from the stored title: the longest English word, else a run of 3-4 Chinese characters."""
    ws = sorted(re.findall(r"[A-Za-z]{4,}", title or ""), key=len, reverse=True)
    if ws: return ws[0]
    zh = re.findall(r"[\u4e00-\u9fff]{3,}", title or "")
    return zh[0][:4] if zh else ""


def test_06_dashboard_search(e2e):
    for p in PLATFORMS:
        ok = True
        for a in ACCOUNTS:
            row = e2e.account_row(p, a)
            its = [i for i in e2e.items_of(p, a) if _word(i["title"])]
            assert its, (p, a)
            it = its[0]; q = _word(it["title"])
            r = e2e.rest("GET", "/api/items", params={"q": q, "platform": p}).json()
            ok &= it["id"] in [x["id"] for x in r["items"]]
            r = e2e.rest("GET", "/api/items", params={"q": q, "account_id": row["id"]}).json()
            ok &= it["id"] in [x["id"] for x in r["items"]] and all(x["account_id"] == row["id"] for x in r["items"])
            ok &= all(x["account_handle"] == "@" + a for x in r["items"])
        assert mark(p, "search", ok), p
    zh = {"douyin": "成都火锅", "xiaohongshu": "吉隆坡咖啡馆", "threads": "中文"}
    for p, q in zh.items():
        r = e2e.rest("GET", "/api/items", params={"q": q}).json()
        srcs = {x["source"] for x in r["items"] if x["platform"] == p}
        assert {f"collector:{p}:a", f"collector:{p}:b"} <= srcs, (p, q, srcs)
    r = e2e.mcp("search_items", {"query": "吉隆坡咖啡馆"})
    assert r["total"] >= 2 and all(i["platform"] == "xiaohongshu" for i in r["items"])
    r = e2e.rest("GET", "/api/items", params={"q": "Ada Lim", "platform": "whatsapp"}).json()
    assert r["total"] >= 1
    r = e2e.mcp("search_contacts", {"query": "Ada Lim"})
    assert {c["platform"] for c in r["contacts"]} == {"messenger", "whatsapp"} and r["total"] >= 4      # 2 apps x 2 accounts
    r = e2e.rest("GET", "/api/contacts", params={"q": "6013"}).json()                                     # @b's WhatsApp numbers
    assert r["total"] >= 1 and all(c["platform"] == "whatsapp" and c["account_name"] == "@b" for c in r["contacts"])


def test_06b_cli_rest_sink_to_server(e2e):
    """The CLI's default REST sink (POST + read-back confirm, dashboard login) against the same server."""
    from savehub import vault
    env = {k: v for k, v in e2e.env.items() if k not in ("SAVEHUB_MCP_TOKEN", "SAVEHUB_VAULT_KEY_FILE")}
    env.update(SAVEHUB_URL=e2e.base)
    cmd = [sys.executable, "-m", "savehub.collector", "--log-level", "WARNING", "--key-stdin", "--sessions-dir", str(e2e.sessions),
           "collect", "instagram", "--account", "@b"]
    r = subprocess.run(cmd, cwd=ROOT, env=env, input=vault.raw_key_for_child() + "\n", capture_output=True, text=True, timeout=300)
    s = json.loads(r.stdout)
    assert r.returncode == 0 and s["status"] == "ok" and s["created"] == 0 and s["duplicates"] == e2e.full[("instagram", "b")], r.stdout[-1500:]
    assert s["account_id"] == e2e.account_row("instagram", "b")["id"] and all(i["confirmed"] for i in s["items"])
    env.pop("SAVEHUB_DASHBOARD_PASSWORD")                                                    # no login -> refused, exit 4
    r = subprocess.run(cmd, cwd=ROOT, env=env, input=vault.raw_key_for_child() + "\n", capture_output=True, text=True, timeout=300)
    assert r.returncode == 4 and json.loads(r.stdout)["status"] == "PushError" and "401" in r.stdout, r.stdout[-800:]


# ---------------------------------------------------------------------------------------------- 4. un-save safety
def test_07_unsave_only_explicit(e2e):
    assert not e2e.events(0, event="unsave")                              # nothing so far: MCP default, sync now, auto-sync
    # CLI --unsave without --unsave-confirm: dry run, nothing touched
    t0 = time.time()
    from savehub import vault
    env = {k: v for k, v in e2e.env.items() if k not in ("SAVEHUB_DASHBOARD_PASSWORD", "SAVEHUB_MCP_TOKEN", "SAVEHUB_VAULT_KEY_FILE")}
    r = subprocess.run([sys.executable, "-m", "savehub.collector", "--log-level", "WARNING", "--key-stdin", "--sessions-dir", str(e2e.sessions),
                        "collect", "threads", "--account", "@a", "--sink", "sqlite", "--db", str(e2e.db), "--unsave", "--max-items", "2"],
                       cwd=ROOT, env=env, input=vault.raw_key_for_child() + "\n", capture_output=True, text=True, timeout=300)
    s = json.loads(r.stdout)
    assert r.returncode == 0 and s["unsave"]["mode"] == "dry-run" and len(s["unsave"]["would_unsave"]) == 2, r.stdout[-1500:] + r.stderr[-1500:]
    assert not e2e.events(t0, event="unsave")
    # MCP collect(unsave=false) explicitly: off
    r = e2e.mcp("collect", {"platform": "facebook", "account": "@a", "max_items": 2, "unsave": False})
    assert r["unsave"] == "off" and r["runs"][0]["unsave_mode"] == "off" and not e2e.events(t0, event="unsave")
    # MCP collect(unsave=true) on the FAKE apps: live, only @a, capped by max_items, verified on the app
    t1 = time.time()
    res = e2e.mcp_parallel([("collect", {"platform": p, "account": "@a", "max_items": 2, "unsave": True}) for p in SAVE_PLATFORMS])
    for p, r in zip(SAVE_PLATFORMS, res):
        run = r["runs"][0]; u = run.get("unsave") or {}
        ev = e2e.events(t1, event="unsave", platform=p)
        ok = r["unsave"] == "live" and run["status"] == "ok" and u.get("mode") == "live" and len(u.get("unsaved", [])) == 2 and u.get("failed") == []
        ok &= len(ev) == 2 and all(e["account"] == "a" for e in ev) and run["backend"] == "playwright"
        assert mark(p, "unsave_guard", ok), (p, run.get("unsave"), ev)
    for p in CONTACT_PLATFORMS:                                           # contacts collectors have no un-save at all
        r = e2e.mcp("collect", {"platform": p, "account": "@a", "unsave": True})
        run = r["runs"][0]
        ok = run["status"] != "ok" and "no un-save action" in (run.get("error") or "") and not e2e.events(t1, event="unsave", platform=p)
        assert mark(p, "unsave_guard", ok), (p, run)
    assert not e2e.events(t1, event="unsave", account="b")
    # auto-sync config via MCP: never un-saves
    r = e2e.mcp("set_autosync", {"enabled": True, "interval_minutes": 5})
    assert r["enabled"] is True and r["interval_minutes"] == 5 and r["unsave"] == "never"
    assert "error" in e2e.mcp("set_autosync", {"enabled": True, "interval_minutes": 1})


# ---------------------------------------------------------------------------------------------- 7. WeChat
def test_08_wechat_not_implemented(e2e):
    r = e2e.mcp("collect", {"platform": "wechat"})
    assert r["status"] == "not_implemented" and "not implemented" in r["error"] and "import_file" in r["error"]
    ls = e2e.mcp("login_status", {"platform": "wechat"})
    assert ls["collector_implemented"] is False and "not implemented" in ls["note"]
    r = e2e.rest("POST", "/api/autosync/run", json={"platform": "wechat"})
    assert r.status_code == 400 and "not implemented" in r.text
    p = subprocess.run([sys.executable, "-m", "savehub.collector", "--sessions-dir", str(e2e.sessions), "collect", "wechat", "--sink", "sqlite",
                        "--db", str(e2e.db)], cwd=ROOT, env={k: v for k, v in e2e.env.items() if "PASSWORD" not in k and "TOKEN" not in k},
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 3 and json.loads(p.stdout)["status"] == "NotImplementedPlatform"
    MATRIX.setdefault("wechat", {})["not_implemented"] = "PASS"


# ---------------------------------------------------------------------------------------------- 3. status after + no leaks
def test_09_status_after_and_no_secret_leaks(e2e):
    ss = e2e.mcp("sync_status")
    rows = {(a["platform"], a["account"]): a for a in ss["accounts"]}
    assert ss["enabled"] is True and ss["interval_minutes"] == 5 and ss["unsave"] == "never" and ss["session_encryption"] == "on"
    assert rows[("x", "@old")]["state"] == "needs_login" and rows[("x", "@c")]["state"] == "needs_login"
    assert rows[("x", "@c")]["next_run"] is None and rows[("x", "@old")]["next_run"] is None and "fix" in rows[("x", "@c")]
    for p in PLATFORMS:
        for a in ACCOUNTS:
            r = rows[(p, "@" + a)]
            assert r["state"] == "synced" and r["account_id"] == e2e.account_row(p, a)["id"] and r["last_success"], r
    lp = e2e.mcp("list_platforms")
    byp = {p["id"]: p for p in lp["platforms"]}
    for p in PLATFORMS:
        accts = {a["account"]: a for a in byp[p]["collector"]["accounts"]}
        assert accts["@a"]["account_id"] == e2e.account_row(p, "a")["id"] and accts["@b"]["account_id"] == e2e.account_row(p, "b")["id"]
        assert byp[p]["count"] == len(e2e.sql("SELECT id FROM items WHERE platform=?", (p,)))
    for f in e2e.sessions.glob("*.json"):                                 # refreshed sessions are still encrypted
        assert json.loads(f.read_text()).get("savehub_encrypted") == 1
    blob = "\n".join(e2e.secrets_seen) + (e2e.tmp / "server.log").read_text() + json.dumps(e2e.rest("GET", "/api/autosync").json())
    for secret in (e2e.token, e2e.password, e2e.key_file.read_text().strip(), "e2e-cookie-"):
        assert secret not in blob
    hosts = sorted({e["host"] for e in e2e.events(0, event="blocked")})
    print("\nblocked (aborted, offline) hosts:", hosts)


def test_99_matrix(e2e):
    cols = ENTRIES
    print("\n\nE2E matrix (platform x entry point)")
    print("platform".ljust(12) + "".join(c[:16].ljust(18) for c in cols))
    fails = 0
    for p in PLATFORMS:
        row = MATRIX.get(p, {})
        cells = [row.get(c, "MISSING") for c in cols]
        fails += sum(1 for c in cells if c != "PASS")
        print(p.ljust(12) + "".join(c[:16].ljust(18) for c in cells))
    w = MATRIX.get("wechat", {}).get("not_implemented", "MISSING"); fails += w != "PASS"
    print("wechat      not_implemented (MCP collect / login_status / Sync now / CLI exit 3):", w)
    print("x auto backend -> playwright fallback (twscrape mocked to fail):", MATRIX.get("x", {}).get("x_auto_fallback", "MISSING"))
    rep = os.environ.get("SAVEHUB_E2E_REPORT")
    if rep: pathlib.Path(rep).write_text(json.dumps(MATRIX, indent=2))
    assert fails == 0


if __name__ == "__main__":
    sys.exit(pytest.main(["-q", "-s", __file__]))
