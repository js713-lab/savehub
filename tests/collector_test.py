"""Collector tests: X parser on saved HTML fixtures, scroll-collect, login detection, push to a real local SaveHub
(temp port + temp DB) over REST, un-save safety (off by default, dry-run by default, only confirmed items).
No real social-network traffic: every x.com request is answered from tests/fixtures by a Playwright route.

Run:  .venv/bin/python -m pytest -q tests/collector_test.py
"""
import json, os, re, socket, stat, subprocess, sys, tempfile, time, pathlib
from urllib.parse import urlparse, parse_qs
import httpx, pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))
from savehub.collector import REGISTRY, RunConfig, NeedLogin, NotImplementedPlatform, get_collector, list_platforms
from savehub.collector.base import CollectedItem
from savehub.collector.platforms.x import parse_raw, normalize_url, XCollector
from savehub.collector import runner, cli, sessions
from savehub.collector.client import RestSink, SqliteSink, PushError, PushResult

PW_USER, PW_PASS = "savehub", "collector-test-pw"
IDS = [f"184000000000000000{i}" for i in range(1, 8)]
FAST = dict(scroll_delay=0.25, scroll_jitter=0.05, idle_rounds=4, unsave_delay=0.05, page_timeout_ms=15000)


# ---------------------------------------------------------------- fake x.com (served from fixtures)
class FakeX:
    def __init__(self, logged_in=True, menu_ids=()):
        self.logged_in, self.menu_ids = logged_in, set(menu_ids)
        self.bookmarked = set(IDS); self.unbookmarked = []; self.requests = []

    def handle(self, route):
        u = urlparse(route.request.url)
        if u.hostname not in ("x.com", "twitter.com"): return route.abort()     # images/emoji/CDN: offline
        self.requests.append(u.path)
        if u.path == "/__test/unbookmark":
            tid = parse_qs(u.query)["id"][0]; self.unbookmarked.append(tid); self.bookmarked.discard(tid)
            return route.fulfill(status=200, body="ok")
        if u.path.startswith("/i/flow/login"):
            return route.fulfill(status=200, content_type="text/html", body=(FX / "x_login.html").read_text())
        if not self.logged_in:
            return route.fulfill(status=302, headers={"location": "https://x.com/i/flow/login?redirect_after_login=%2Fi%2Fbookmarks"})
        if u.path == "/i/bookmarks":
            return route.fulfill(status=200, content_type="text/html", body=(FX / "x_bookmarks.html").read_text())
        m = re.match(r"^/(\w+)/status/(\d+)$", u.path)
        if m:
            h = (FX / "x_tweet.html").read_text().replace("{{ID}}", m[2]).replace("{{H}}", m[1]) \
                .replace("{{BM}}", "removeBookmark" if m[2] in self.bookmarked else "bookmark") \
                .replace("{{VARIANT}}", "menu" if m[2] in self.menu_ids else "direct")
            return route.fulfill(status=200, content_type="text/html", body=h)
        return route.fulfill(status=404, body="not found")

    def hook(self, ctx): ctx.route("**/*", self.handle)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = runner.launch_browser(p, headful=False)
        yield b
        b.close()


@pytest.fixture()
def sessdir(tmp_path):
    """A fake saved X session (dummy cookie, not a real login)."""
    d = tmp_path / "sessions"; d.mkdir()
    (d / "x.json").write_text(json.dumps({"cookies": [{"name": "auth_token", "value": "dummy-test-cookie", "domain": ".x.com", "path": "/",
                                                       "expires": -1, "httpOnly": True, "secure": True, "sameSite": "None"}], "origins": []}))
    return d


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="module")
def savehub():
    """Real SaveHub combined server on a temp port + temp DB, with dashboard login on."""
    port = _free_port(); dbp = os.path.join(tempfile.mkdtemp(), "collector.db")
    env = {**os.environ, "SAVEHUB_PORT": str(port), "SAVEHUB_HOST": "127.0.0.1", "SAVEHUB_DB": dbp, "SAVEHUB_SEED": "0",
           "SAVEHUB_DASHBOARD_USER": PW_USER, "SAVEHUB_DASHBOARD_PASSWORD": PW_PASS, "SAVEHUB_MCP_TOKEN": "t-collector"}
    env.pop("PORT", None)
    proc = subprocess.Popen([sys.executable, "-m", "savehub.server"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            if httpx.get(base + "/healthz", timeout=1).status_code == 200: break
        except Exception: time.sleep(0.25)
    else:
        proc.kill(); raise RuntimeError(proc.stdout.read())
    yield base, dbp
    proc.terminate(); proc.wait(10)


def api(base, path, **kw):
    r = httpx.get(base + path, auth=(PW_USER, PW_PASS), **kw); r.raise_for_status(); return r.json()


def page_for(browser, fake):
    ctx = runner.new_context(browser); fake.hook(ctx); return ctx, ctx.new_page()


# ---------------------------------------------------------------- registry / stubs / config
def test_registry_and_stubs():
    expected = {"x", "instagram", "threads", "facebook", "tiktok", "douyin", "xiaohongshu", "wechat", "whatsapp", "messenger"}
    assert set(REGISTRY) == expected
    impl = {p["platform"]: p["implemented"] for p in list_platforms()}
    assert impl.pop("x") is True and not any(impl.values())
    for name in expected - {"x"}:
        c = get_collector(name)
        for call in (lambda: c.collect(None, RunConfig()), lambda: c.unsave(None, None, RunConfig()), lambda: c.is_login_page(None)):
            with pytest.raises(NotImplementedPlatform, match=rf"'{name}' collector is not implemented yet.*TODO"): call()
        assert c.notes and c.saved_url
    with pytest.raises(Exception, match="unknown platform"): get_collector("myspace")
    with pytest.raises(NotImplementedPlatform): runner.run_collect("tiktok", RunConfig())


def test_unsave_off_and_dry_run_by_default(tmp_path):
    assert RunConfig().unsave is False and RunConfig().unsave_confirm is False and RunConfig().unsave_mode == "off"
    P = cli.build_parser()
    cfg = lambda *a: cli.make_config(P.parse_args(["collect", "x", *a]))
    assert cfg().unsave_mode == "off"
    assert cfg("--unsave").unsave_mode == "dry-run"
    assert cfg("--unsave", "--unsave-confirm").unsave_mode == "live"
    with pytest.raises(Exception, match="only makes sense together with --unsave"): cfg("--unsave-confirm")
    f = tmp_path / "run.json"; f.write_text(json.dumps({"unsave": True, "max_items": 5}))
    c = cfg("--config", str(f)); assert c.unsave_mode == "dry-run" and c.max_items == 5
    assert cfg("--config", str(f), "--max-items", "9").max_items == 9          # CLI overrides config file
    # CLI: --no-push + --unsave refused; unknown platform / stub exit codes
    r = subprocess.run([sys.executable, "-m", "savehub.collector", "collect", "x", "--no-push", "--unsave"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 1 and "cannot be combined" in r.stdout
    r = subprocess.run([sys.executable, "-m", "savehub.collector", "collect", "douyin"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 3 and json.loads(r.stdout)["status"] == "NotImplementedPlatform"


# ---------------------------------------------------------------- parser
def test_normalize_and_dedupe():
    assert normalize_url("/a_b/status/123/photo/1")[0] == "https://x.com/a_b/status/123"
    assert normalize_url("https://twitter.com/Foo/status/9?s=20") == ("https://x.com/Foo/status/9", "Foo", "9")
    assert normalize_url("/home") is None and normalize_url("") is None
    raws = [{"href": "/a/status/1", "name": "A", "handle": "a", "text": "one"}, {"href": "/a/status/1/photo/1", "text": "dup"},
            {"href": "https://twitter.com/b/status/2", "text": "two", "photos": 1}, {"href": "/i/flow/login"}]
    out = parse_raw(raws)
    assert [i.external_id for i in out] == ["1", "2"] and out[0].text == "one" and out[1].has_media and out[1].author_handle == "b"


def test_x_parser_on_fixture(browser):
    fake = FakeX(); ctx, pg = page_for(browser, fake)
    try:
        pg.goto("https://x.com/i/bookmarks"); pg.wait_for_selector('article[data-testid="tweet"]')
        col = XCollector(); items = col.extract(pg)
        assert [i.external_id for i in items] == IDS[:4]
        a, k, d, q = items
        assert (a.author, a.author_handle, a.url) == ("Ada Sample", "ada_sample", "https://x.com/ada_sample/status/1840000000000000001")
        assert a.text == "Thread on SQLite FTS5 trigram tokenizers: substring search for 中文 works out of the box 🧵"
        assert a.posted_at == "2026-10-10T08:15:00.000Z" and a.has_media is False
        assert k.author == "Kopi Bot 🇲🇾" and k.has_media and k.media_url.startswith("https://pbs.twimg.com/media/SAMPLEkopi")
        assert d.has_media and d.extra.get("video") and d.extra["social_context"] == "Ada Sample reposted"
        assert q.text == "This is exactly right 👇" and q.author_handle == "quoter" and not q.has_media
        assert q.extra["quoted"]["text"].startswith("Quoted tweet text") and "1830000000000000099" not in q.url
        assert col.is_logged_in(pg) and not col.is_login_page(pg)
        p = q.to_savehub(account_id=3, collected_at="2026-10-16T10:00:00+00:00")
        assert p["platform"] == "x" and p["source"] == "collector:x" and p["account_id"] == 3 and p["external_id"] == IDS[3]
        assert p["saved_at"] == "2026-10-16T10:00:00+00:00" and p["raw"]["posted_at"] == "2026-10-13T09:00:00.000Z"
    finally: ctx.close()


def test_collect_scrolls_dedupes_and_caps(browser):
    fake = FakeX(); ctx, pg = page_for(browser, fake)
    try:
        items = XCollector().collect(pg, RunConfig(**FAST))
        assert [i.external_id for i in items] == IDS                     # 7 unique: promoted skipped, repeated tweet de-duped
        nl = items[4]; assert nl.text == "Line one\nLine two with a link example.com/very-long-pa…" and nl.extra.get("link_card")
        assert items[5].text == "" and items[5].has_media
        assert len(XCollector().collect(pg, RunConfig(**{**FAST, "max_items": 3}))) == 3
    finally: ctx.close()


# ---------------------------------------------------------------- not logged in
def test_login_detection(browser, sessdir):
    fake = FakeX(logged_in=False)
    with pytest.raises(NeedLogin) as e:
        runner.run_collect("x", RunConfig(**FAST), None, sessdir, browser=browser, context_hook=fake.hook)
    assert e.value.exit_code == 2 and e.value.session_file == str(sessdir / "x.json") and "login x" in str(e.value)
    assert "/i/flow/login" in e.value.detail          # followed the 302 to the login flow
    ctx = runner.new_context(browser); pg = ctx.new_page()     # DOM-only detection (no login URL)
    try:
        pg.set_content((FX / "x_login.html").read_text()); assert XCollector().is_login_page(pg) and not XCollector().is_logged_in(pg)
        pg.set_content((FX / "x_bookmarks.html").read_text()); assert not XCollector().is_login_page(pg)
    finally: ctx.close()
    r = subprocess.run([sys.executable, "-m", "savehub.collector", "--sessions-dir", str(sessdir / "none"), "collect", "x"], cwd=ROOT, capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert r.returncode == 2 and out["status"] == "need_login" and out["session_file"].endswith("none/x.json") and out["fix"] == "python -m savehub.collector login x"


# ---------------------------------------------------------------- push to a real SaveHub over REST
def test_push_to_savehub_and_search(browser, sessdir, savehub):
    base, _ = savehub
    r = httpx.post(base + "/api/accounts", json={"platform": "x", "handle": "@js_sample_me", "display_name": "Js"}, auth=(PW_USER, PW_PASS))
    acct = r.json()["id"]
    fake = FakeX(); sink = RestSink(base, PW_USER, PW_PASS)
    (sessdir / "x@js_sample_me.json").write_text((sessdir / "x.json").read_text())     # per-account session
    s = runner.run_collect("x", RunConfig(**FAST, account_handle="@js_sample_me"), sink, sessdir, browser=browser, context_hook=fake.hook)
    assert s["account"] == "@js_sample_me" and s["account_id"] == acct and s["session_file"].endswith("x@js_sample_me.json")
    assert (s["status"], s["collected"], s["created"], s["duplicates"], s["push_failed"]) == ("ok", 7, 7, 0, 0)
    assert s["unsave_mode"] == "off" and s["unsave"] == {} and fake.unbookmarked == []          # OFF by default
    assert all(i["confirmed"] for i in s["items"])
    items = api(base, "/api/items", params={"platform": "x", "limit": 50})["items"]
    assert {i["external_id"] for i in items} == set(IDS)
    assert all(i["source"] == "collector:x:js_sample_me" and i["account_id"] == acct for i in items)
    assert api(base, "/api/items", params={"account_id": acct, "limit": 50})["total"] == 7                 # filterable by account
    hit = api(base, "/api/items", params={"q": "kopi spots"})
    assert hit["total"] == 1 and hit["items"][0]["author_handle"] == "KopiBot" and hit["items"][0]["media_url"]
    assert api(base, "/api/items", params={"q": "中文"})["total"] == 1                              # CJK search via FTS/LIKE
    assert api(base, "/api/items", params={"q": "Quoted tweet text"})["total"] == 0              # quoted text not stored as content
    import sqlite3      # the API hides `raw`; read it from the server's temp DB
    raw = json.loads(sqlite3.connect(savehub[1]).execute("SELECT raw FROM items WHERE external_id=?", (IDS[1],)).fetchone()[0])
    assert raw["has_media"] is True and raw["posted_at"] == "2026-10-11T02:00:00.000Z" and raw["collected_at"]
    # second run: everything is a duplicate, nothing new
    s2 = runner.run_collect("x", RunConfig(**FAST), sink, sessdir, browser=browser, context_hook=FakeX().hook)
    assert (s2["created"], s2["duplicates"]) == (0, 7)
    assert api(base, "/api/items", params={"platform": "x"})["total"] == 7
    # session refreshed + private
    st = os.stat(sessdir / "x.json"); assert stat.S_IMODE(st.st_mode) == 0o600
    assert any(c["name"] == "auth_token" for c in json.loads((sessdir / "x.json").read_text())["cookies"])
    sink.close()
    with pytest.raises(PushError, match="401"): RestSink(base, PW_USER, "wrong").check()


def test_unsave_dry_run_then_live(browser, sessdir, savehub):
    base, _ = savehub; sink = RestSink(base, PW_USER, PW_PASS)
    fake = FakeX()
    s = runner.run_collect("x", RunConfig(**FAST, unsave=True), sink, sessdir, browser=browser, context_hook=fake.hook)
    assert s["unsave"]["mode"] == "dry-run" and sorted(s["unsave"]["would_unsave"]) == sorted(i["url"] for i in s["items"])
    assert fake.unbookmarked == [] and not any("/status/" in p for p in fake.requests)    # dry run never opens tweets
    fake = FakeX(menu_ids={IDS[1]})          # 2nd tweet only has the Share-menu path
    s = runner.run_collect("x", RunConfig(**FAST, unsave=True, unsave_confirm=True, max_unsave=3), sink, sessdir, browser=browser, context_hook=fake.hook)
    u = s["unsave"]
    assert u["mode"] == "live" and len(u["unsaved"]) == 3 and u["failed"] == [] and u["skipped_cap"] == 4
    assert fake.unbookmarked == IDS[:3]
    assert api(base, "/api/items", params={"platform": "x"})["total"] == 7                    # still stored in SaveHub
    sink.close()


class FlakySink:
    """Sink that fails to store one item: that item must never be un-saved."""
    kind = "fake"
    def __init__(self, bad): self.bad = bad
    def check(self): return {"stats": {}}
    def resolve_account(self, *a): return None
    def push(self, item, account_id=None, collected_at=""):
        if item.external_id == self.bad: return PushResult(item, error="HTTP 500: boom")
        return PushResult(item, id=1, created=True, confirmed=item.external_id != IDS[2])     # IDS[2]: read-back mismatch
    def close(self): pass


def test_unsave_only_confirmed_items(browser, sessdir):
    fake = FakeX()
    s = runner.run_collect("x", RunConfig(**FAST, unsave=True, unsave_confirm=True), FlakySink(IDS[0]), sessdir, browser=browser, context_hook=fake.hook)
    assert s["push_failed"] == 1 and IDS[0] not in fake.unbookmarked and IDS[2] not in fake.unbookmarked
    assert sorted(fake.unbookmarked) == sorted(set(IDS) - {IDS[0], IDS[2]})
    with pytest.raises(Exception, match="needs a sink"):
        runner.run_collect("x", RunConfig(unsave=True), None, sessdir, browser=browser, context_hook=fake.hook)


def test_sqlite_sink(browser, sessdir, tmp_path):
    sink = SqliteSink(tmp_path / "local.db")
    s = runner.run_collect("x", RunConfig(**FAST), sink, sessdir, browser=browser, context_hook=FakeX().hook)
    assert s["created"] == 7
    from savehub import db
    assert db.list_items("kopi")["total"] == 1 and db.list_items(platforms=["x"])["total"] == 7


def test_login_command_saves_session(browser, tmp_path):
    """`login` flow without a real login: the fake login page 'logs in' (navigates to a signed-in page) after 1s."""
    class FakeLogin(FakeX):
        def handle(self, route):
            if urlparse(route.request.url).path.startswith("/i/flow/login"):
                body = (FX / "x_login.html").read_text().replace("</body>", "<script>setTimeout(() => location.href = '/i/bookmarks', 1000)</script></body>")
                return route.fulfill(status=200, content_type="text/html", headers={"set-cookie": "auth_token=dummy; Domain=.x.com; Path=/; Secure"}, body=body)
            return super().handle(route)
    d = tmp_path / "s"
    out = runner.run_login("x", d, timeout_s=20, browser=browser, context_hook=FakeLogin().hook)
    assert out["status"] == "logged_in" and out["exists"] and stat.S_IMODE(os.stat(d / "x.json").st_mode) == 0o600
    r = subprocess.run([sys.executable, "-m", "savehub.collector", "--sessions-dir", str(d), "sessions"], cwd=ROOT, capture_output=True, text=True)
    j = json.loads(r.stdout); assert r.returncode == 0 and j["sessions"][0]["platform"] == "x" and "dummy" not in r.stdout   # metadata only
    with pytest.raises(NeedLogin, match="not detected"):        # a login page that never completes -> clear failure, nothing saved
        runner.run_login("x", tmp_path / "s2", timeout_s=3, browser=browser, context_hook=FakeX(logged_in=False).hook)
    assert not (tmp_path / "s2" / "x.json").exists()


# ---------------------------------------------------------------- multi-account sessions + attribution
def test_session_keys_per_account(tmp_path):
    assert sessions.account_key("@Js_Main ") == "js_main" and sessions.account_key(None) == "default" == sessions.account_key("")
    assert sessions.session_path("x", tmp_path, "@Js_Main").name == "x@js_main.json" and sessions.session_path("x", tmp_path).name == "x.json"
    for bad in ("../etc", "a/b", "a b", "@"*2 + "x;rm"):
        with pytest.raises(ValueError): sessions.account_key(bad)
    for n in ("x@a.json", "x@b.json", "x.json", "instagram@food.json"): (tmp_path / n).write_text('{"cookies":[{"name":"c","value":"v","domain":".x.com","expires":-1}]}')
    (tmp_path / "x@old.json").write_text('{"cookies":[{"name":"c","value":"SECRETVAL","domain":".x.com","expires":1000}]}')
    got = {(s["platform"], s["account"]): s["status"] for s in sessions.list_sessions(tmp_path)}
    assert got == {("x", "a"): "saved", ("x", "b"): "saved", ("x", "default"): "saved", ("instagram", "food"): "saved", ("x", "old"): "expired"}
    assert [s["account"] for s in sessions.list_sessions(tmp_path, "instagram")] == ["food"]
    assert "SECRETVAL" not in json.dumps(sessions.list_sessions(tmp_path))
    with pytest.raises(NeedLogin, match=r"account @c.*login x --account @c"):
        runner.run_collect("x", RunConfig(account_handle="@c"), None, tmp_path)


def test_multi_account_attribution_sqlite(browser, sessdir, tmp_path):
    """Two X accounts logged in at once: each run uses its own session file, auto-creates/links its account row and tags items."""
    for h in ("alice", "bob"): (sessdir / f"x@{h}.json").write_text((sessdir / "x.json").read_text())
    sink = SqliteSink(tmp_path / "multi.db")
    from savehub import db
    pre = db.add_account({"platform": "x", "handle": "@Alice", "display_name": "Alice main"})[0]       # existing dashboard row is reused
    sa = runner.run_collect("x", RunConfig(**FAST, account_handle="@alice", max_items=3), sink, sessdir, browser=browser, context_hook=FakeX().hook)
    sb = runner.run_collect("x", RunConfig(**FAST, account_handle="bob"), sink, sessdir, browser=browser, context_hook=FakeX().hook)
    assert sa["account_id"] == pre and sa["created"] == 3 and sb["created"] == 4 and sb["duplicates"] == 3
    bob = db.list_accounts("bob", "x")["accounts"][0]
    assert bob["handle"] == "@bob" and bob["connected_via"] == "collector" and sb["account_id"] == bob["id"]
    assert db.list_items(platforms=["x"], account_id=pre)["total"] == 3 and db.list_items(platforms=["x"], account_id=bob["id"])["total"] == 4
    srcs = {r[0] for r in db.conn().execute("SELECT source FROM items WHERE platform='x' AND source LIKE 'collector:%'")}
    assert srcs == {"collector:x:alice", "collector:x:bob"}
    raw = json.loads(db.conn().execute("SELECT raw FROM items WHERE source='collector:x:bob' LIMIT 1").fetchone()[0])
    assert raw["source_account"] == "@bob"


def test_cli_login_and_sessions_per_account(tmp_path):
    (tmp_path / "x@me.json").write_text('{"cookies":[]}')
    r = subprocess.run([sys.executable, "-m", "savehub.collector", "--sessions-dir", str(tmp_path), "sessions", "x"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout)["sessions"][0]["account"] == "me"
    r = subprocess.run([sys.executable, "-m", "savehub.collector", "--sessions-dir", str(tmp_path / "none"), "collect", "x", "--account", "@Zed", "--no-push"], cwd=ROOT, capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert r.returncode == 2 and out["account"] == "@zed" and out["fix"] == "python -m savehub.collector login x --account @zed"


# ---------------------------------------------------------------- MCP bridge: login_status / collect / list_platforms
def arun(coro):
    """asyncio.run in a fresh thread (the module's Playwright sync fixture owns this thread's event loop)."""
    import asyncio, concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex: return ex.submit(asyncio.run, coro).result()


@pytest.fixture()
def mcpmod(tmp_path, monkeypatch):
    from savehub import db
    c = getattr(db._local, "c", None)
    if c is not None: c.close(); db._local.c = None
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "mcp.db")
    monkeypatch.setenv("SAVEHUB_SESSIONS_DIR", str(tmp_path / "sess")); (tmp_path / "sess").mkdir()
    for k in ("RENDER", "RENDER_EXTERNAL_HOSTNAME", "SAVEHUB_COLLECTOR"): monkeypatch.delenv(k, raising=False)
    from savehub import mcp_server
    yield mcp_server, tmp_path / "sess"
    c = getattr(db._local, "c", None)
    if c is not None: c.close(); db._local.c = None


def _sess(d, name, expires=-1): (d / name).write_text(json.dumps({"cookies": [{"name": "auth_token", "value": "dummy-cookie-val", "domain": ".x.com", "path": "/", "expires": expires}], "origins": []}))


def test_mcp_tools_signatures_and_no_secrets(mcpmod):
    import asyncio, inspect
    m, _ = mcpmod
    tools = {t.name: t for t in arun(m.mcp.list_tools())}
    assert list(tools["login_status"].inputSchema["properties"]) == ["platform", "account"]
    assert list(tools["collect"].inputSchema["properties"]) == ["platform", "account", "max_items", "unsave"]
    assert inspect.signature(m.collect).parameters["unsave"].default is False and tools["collect"].inputSchema["properties"]["unsave"]["default"] is False
    for t in tools.values():
        sch = json.dumps(t.inputSchema).lower()
        assert not any(w in sch for w in ("password", "secret", "cookie", "token", "credential")), t.name


def test_mcp_login_status_and_list_platforms(mcpmod):
    import asyncio
    m, d = mcpmod
    r = m.login_status("x")
    assert r["logged_in"] is False and r["accounts"] == [] and "python -m savehub.collector login x --account @<handle>" in r["fix"]
    _sess(d, "x@alice.json"); _sess(d, "x@bob.json", expires=1000); _sess(d, "instagram@food.json")
    from savehub import db
    aid = db.add_account({"platform": "x", "handle": "@alice"})[0]
    r = m.login_status("x")
    acc = {a["account"]: a for a in r["accounts"]}
    assert r["logged_in"] and acc["@alice"]["logged_in"] and acc["@alice"]["account_id"] == aid and acc["@bob"]["session"] == "expired"
    assert "dummy-cookie-val" not in json.dumps(r)
    r = m.login_status("x", "@bob"); assert not r["logged_in"] and "--account @bob" in r["fix"]
    r = m.login_status("x", "@nobody"); assert r["accounts"][0]["session"] == "missing" and not r["logged_in"]
    assert "error" in m.login_status("myspace") and "error" in m.login_status("x", "../../etc")
    r = m.login_status("instagram"); assert r["collector_implemented"] is False and "not implemented" in r["note"]
    lp = {p["id"]: p for p in m.list_platforms()["platforms"]}
    assert lp["x"]["collector"]["supported"] and lp["x"]["collector"]["logged_in_accounts"] == 1
    assert {a["account"] for a in lp["x"]["collector"]["accounts"]} == {"@alice", "@bob"}
    assert lp["instagram"]["collector"]["implemented"] is False and lp["instagram"]["collector"]["accounts"][0]["account"] == "@food"
    assert lp["web"]["collector"]["supported"] is False if "web" in lp else True
    assert "available" in m.list_platforms()["collector_runtime"]


def test_mcp_collect_guards(mcpmod, monkeypatch):
    import asyncio
    m, d = mcpmod
    called = []
    async def fake(cmd, timeout): called.append(cmd); return {"status": "ok"}
    monkeypatch.setattr(m, "_run_collect_proc", fake)
    t = time.time()
    r = arun(m.collect("x")); assert r["status"] == "need_login" and "login x --account @<handle>" in r["error"]
    r = arun(m.collect("x", "@zed")); assert r["status"] == "need_login" and "login x --account @zed" in r["error"]
    assert arun(m.collect("tiktok"))["status"] == "not_implemented"
    assert "unknown platform" in arun(m.collect("myspace"))["error"]
    assert "max_items" in arun(m.collect("x", max_items=0))["error"]
    _sess(d, "x@alice.json")
    monkeypatch.setenv("RENDER", "true"); monkeypatch.setenv("SAVEHUB_COLLECTOR", "off")      # simulate Render (no browser)
    r = arun(m.collect("x")); assert r["status"] == "browser_unavailable" and "Render" in r["error"]
    monkeypatch.delenv("SAVEHUB_COLLECTOR"); monkeypatch.setattr(m.importlib.util, "find_spec", lambda n: None)
    r = arun(m.collect("x")); assert r["status"] == "browser_unavailable" and "Playwright is not installed" in r["error"]
    assert called == [] and time.time() - t < 5                                                  # nothing launched, no hang


def test_mcp_collect_all_accounts_unsave_off_by_default(mcpmod, monkeypatch):
    import asyncio
    m, d = mcpmod
    monkeypatch.setattr(m, "collector_runtime", lambda: {"available": True, "browser": "test"})
    calls = []
    async def fake(cmd, timeout):
        calls.append(cmd); acct = cmd[cmd.index("--account") + 1] if "--account" in cmd else "default"
        return {"status": "ok", "account": "@" + acct, "collected": 2, "created": 2, "duplicates": 0, "push_failed": 0, "session_file": "/x", "items": []}
    monkeypatch.setattr(m, "_run_collect_proc", fake)
    _sess(d, "x@alice.json"); _sess(d, "x@bob.json"); _sess(d, "x@old.json", expires=1000)
    r = arun(m.collect("x"))
    assert r["status"] == "ok" and r["accounts"] == 2 and r["created"] == 4 and r["unsave"] == "off" and r["skipped_accounts"] == ["@old"]
    assert all("session_file" not in x for x in r["runs"])
    assert [c[c.index("--account") + 1] for c in calls] == ["alice", "bob"]
    assert not any("--unsave" in c or "--unsave-confirm" in c for c in calls)                    # OFF unless explicit
    assert all(c[c.index("--sink") + 1] == "sqlite" for c in calls)
    calls.clear(); r = arun(m.collect("x", "@bob", max_items=5, unsave=True))
    assert r["unsave"] == "live" and len(calls) == 1 and calls[0][-5:] == ["bob", "--max-items", "5", "--unsave", "--unsave-confirm"]


def test_mcp_collect_subprocess_env_and_timeout(mcpmod, monkeypatch):
    import asyncio
    m, _ = mcpmod
    monkeypatch.setenv("SAVEHUB_DASHBOARD_PASSWORD", "pw-should-not-leak"); monkeypatch.setenv("SAVEHUB_VAULT_KEY", "k"); monkeypatch.setenv("GITHUB_TOKEN", "t")
    env = m._child_env()
    assert not {"SAVEHUB_DASHBOARD_PASSWORD", "SAVEHUB_VAULT_KEY", "GITHUB_TOKEN"} & set(env) and env["SAVEHUB_DB"] == str(m.db.DB_PATH)
    t = time.time()
    r = arun(m._run_collect_proc([sys.executable, "-c", "import time; time.sleep(60)"], 2))
    assert r["status"] == "timeout" and time.time() - t < 10
    r = arun(m._run_collect_proc([sys.executable, "-c", "print('{\"status\": \"ok\", \"collected\": 1}')"], 10))
    assert r == {"status": "ok", "collected": 1, "exit_code": 0}


def test_mcp_collect_real_subprocess_need_login(mcpmod, monkeypatch):
    """End-to-end through the real collector CLI subprocess: an account whose session file vanished -> need_login JSON, quickly."""
    import asyncio
    m, d = mcpmod
    r = arun(m._run_collect_proc(m._collect_cmd("x", "ghost", 3, False), 60))
    assert r["status"] == "need_login" and r["exit_code"] == 2 and r["account"] == "@ghost"
