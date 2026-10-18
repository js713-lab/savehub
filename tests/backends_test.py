"""Library backends (twscrape for X, instaloader for Instagram): offline tests with mocked library responses.
Cookie conversion (in memory), mapping to the item schema, max_items + incremental stop, auto -> Playwright fallback,
explicit-backend errors, un-save always on Playwright, the per-platform setting (DB, REST, MCP), and that no plaintext
cookie file is left behind. No network: every library entry point that would talk to X / Instagram is replaced.

Run:  .venv/bin/python -m pytest -q tests/backends_test.py
"""
import asyncio, glob, json, os, pathlib, stat, sys, tempfile, time
from datetime import datetime, timezone
from types import SimpleNamespace as NS
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from savehub import db, vault
from savehub.collector import RunConfig, runner, sessions, platforms  # noqa: F401
from savehub.collector import backends
from savehub.collector.backends import twscrape as tw_backend, instaloader as il_backend
from savehub.collector.base import CollectedItem, CollectorError
from savehub.collector.client import PushResult

SECRET = "SECRETcookieVALUE-9f8e7d"
FUTURE = time.time() + 86400 * 30


def _cookie(name, value, domain, expires=FUTURE):
    return {"name": name, "value": value, "domain": domain, "path": "/", "expires": expires, "httpOnly": True, "secure": True, "sameSite": "None"}


X_STATE = {"cookies": [_cookie("auth_token", SECRET, ".x.com"), _cookie("ct0", "csrf" + SECRET, ".x.com"),
                       _cookie("guest_id", "g1", ".x.com"), _cookie("old", "stale", ".x.com", expires=time.time() - 10),
                       _cookie("sessionid", "not-for-x", ".instagram.com")], "origins": []}
IG_STATE = {"cookies": [_cookie("sessionid", SECRET, ".instagram.com"), _cookie("csrftoken", "csrf123", ".instagram.com"),
                        _cookie("ds_user_id", "4242", ".instagram.com"), _cookie("auth_token", "x-only", ".x.com")], "origins": []}


@pytest.fixture()
def tdb(tmp_path, monkeypatch):
    old = getattr(db._local, "c", None)
    if old is not None: old.close(); db._local.c = None
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setenv("SAVEHUB_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("SAVEHUB_VAULT_KEY_FILE", str(tmp_path / "no.key"))
    monkeypatch.delenv("SAVEHUB_VAULT_KEY", raising=False)
    vault.set_runtime_key("")
    yield tmp_path
    c = getattr(db._local, "c", None)
    if c is not None: c.close(); db._local.c = None


def _leftovers(prefix):
    return set(glob.glob(os.path.join(tempfile.gettempdir(), prefix + "*")))


def _tweet(i, quoted=False):
    return NS(id=1840000000000000000 + i, id_str=str(1840000000000000000 + i), rawContent=f"tweet {i} 🎉",
              user=NS(username=f"user{i}", displayname=f"User {i}"), date=datetime(2026, 10, 1, 12, i, tzinfo=timezone.utc),
              media=NS(photos=[NS(url=f"https://pbs.twimg.com/media/p{i}.jpg")] if i % 2 else [], videos=[], animated=[]),
              quotedTweet=NS(rawContent="q", user=NS(username="qq"), url="https://x.com/qq/status/1") if quoted else None,
              card=None, links=[], likeCount=i, retweetCount=0, viewCount=None)


class FakeAPI:
    """Stands in for twscrape.API: records what the real AccountsPool stored, yields canned tweets."""
    calls = []

    def __init__(self, pool, **kw):
        self.pool = pool; FakeAPI.calls.append(self)

    def bookmarks(self, limit=-1, kv=None):
        api = self

        async def gen():
            # the pool file is a real 0600 file in a 0700 dir while the library runs
            f = api.pool._db_file
            api.seen_mode = (stat.S_IMODE(os.stat(f).st_mode), stat.S_IMODE(os.stat(os.path.dirname(f)).st_mode))
            api.seen_file = f
            accs = await api.pool.get_all()
            api.seen_cookies = dict(accs[0].cookies)
            for i in range(1, FakeAPI.n + 1):
                if FakeAPI.fail_at and i == FakeAPI.fail_at: raise RuntimeError("401 Unauthorized auth_token=" + SECRET)
                yield _tweet(i, quoted=(i == 2))
        return gen()


@pytest.fixture()
def fake_tw(monkeypatch):
    import twscrape
    FakeAPI.calls, FakeAPI.n, FakeAPI.fail_at = [], 8, 0
    monkeypatch.setattr(twscrape, "API", FakeAPI)
    return FakeAPI


# ---------------------------------------------------------------- cookie conversion
def test_cookie_dict_filters_domain_and_expired():
    c = backends.cookie_dict(X_STATE, ("x.com", "twitter.com"))
    assert c == {"auth_token": SECRET, "ct0": "csrf" + SECRET, "guest_id": "g1"}
    assert backends.cookie_dict(IG_STATE, ("instagram.com",)) == {"sessionid": SECRET, "csrftoken": "csrf123", "ds_user_id": "4242"}
    # most specific domain wins, lookalike domains are ignored
    st = {"cookies": [_cookie("a", "1", ".x.com"), _cookie("a", "2", "api.x.com"), _cookie("b", "3", "evilx.com")]}
    assert backends.cookie_dict(st, ("x.com",)) == {"a": "2"}
    with pytest.raises(backends.BackendError, match="ct0"):
        backends.require({"auth_token": "1"}, ("auth_token", "ct0"), "twscrape")


# ---------------------------------------------------------------- twscrape (X)
def test_twscrape_maps_and_leaves_no_file(fake_tw):
    before = _leftovers("savehub-twscrape-")
    items = tw_backend.collect(X_STATE, RunConfig(max_items=5), "main")
    api = fake_tw.calls[0]
    assert api.seen_cookies["auth_token"] == SECRET and api.seen_cookies["ct0"] == "csrf" + SECRET
    assert "sessionid" not in api.seen_cookies                       # other sites' cookies are not handed over
    assert api.seen_mode == (0o600, 0o700)
    assert not os.path.exists(api.seen_file) and not os.path.exists(os.path.dirname(api.seen_file))
    assert _leftovers("savehub-twscrape-") == before
    assert len(items) == 5                                           # max_items
    it = items[0]
    assert it.platform == "x" and it.url == "https://x.com/user1/status/1840000000000000001" and it.external_id == "1840000000000000001"
    assert it.author == "User 1" and it.author_handle == "user1" and it.text == "tweet 1 🎉"
    assert it.posted_at.startswith("2026-10-01T12:01") and it.has_media and it.media_url.endswith("p1.jpg")
    assert it.extra["via"] == "twscrape" and items[1].extra["quoted"]["user"] == "@qq"
    assert not items[1].has_media
    assert os.environ.get("TWS_TELEMETRY") == "0" and os.environ.get("DO_NOT_TRACK") == "1"


def test_twscrape_incremental_stop(fake_tw):
    known = {"https://x.com/user2/status/1840000000000000002", "id:1840000000000000003", "id:1840000000000000004"}
    items = tw_backend.collect(X_STATE, RunConfig(max_items=30, known=known, stop_after_known=3), "")
    assert [i.external_id[-1] for i in items] == ["1", "2", "3", "4"]       # stopped at the 3rd already-stored item


def test_twscrape_error_is_scrubbed_and_file_removed(fake_tw):
    fake_tw.fail_at = 3
    before = _leftovers("savehub-twscrape-")
    with pytest.raises(backends.BackendError) as e:
        tw_backend.collect(X_STATE, RunConfig(max_items=30), "main")
    assert SECRET not in str(e.value) and "401" in str(e.value)
    assert _leftovers("savehub-twscrape-") == before


def test_twscrape_needs_cookies(fake_tw):
    with pytest.raises(backends.BackendError, match="ct0"):
        tw_backend.collect({"cookies": [_cookie("auth_token", "1", ".x.com")]}, RunConfig(), "")
    assert fake_tw.calls == []                                          # never reached the library


# ---------------------------------------------------------------- instaloader (Instagram)
def _node(i, **kw):
    return {"shortcode": f"C{i:03d}", "__typename": "GraphSidecar" if i == 2 else "GraphImage", "is_video": i == 3,
            "display_url": f"https://scontent.cdninstagram.com/{i}.jpg", "taken_at_timestamp": 1790000000 + i,
            "owner": {"username": f"igu{i}", "full_name": f"IG {i}"}, "id": str(9000 + i),
            "edge_media_to_caption": {"edges": [{"node": {"text": f"caption {i} 你好"}}]}, **kw}


class FakeProfile:
    made = []

    def __init__(self, context, node):
        self.context, self.node = context, node; FakeProfile.made.append(self)

    def get_saved_posts(self):
        for i in range(1, FakeProfile.n + 1):
            yield NS(_node=_node(i, **({"product_type": "clips"} if i == 4 else {})), shortcode=f"C{i:03d}")


@pytest.fixture()
def fake_il(monkeypatch):
    import instaloader
    FakeProfile.made, FakeProfile.n = [], 6
    monkeypatch.setattr(instaloader, "Profile", FakeProfile)
    return FakeProfile


def test_instaloader_maps_in_memory(fake_il, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                                        # instaloader would write session/resume files to cwd
    items = il_backend.collect(IG_STATE, RunConfig(max_items=4), "@Main")
    p = fake_il.made[0]
    assert p.node == {"id": 4242, "username": "main"}                  # profile id from ds_user_id, user from --account
    jar = p.context._session.cookies.get_dict()
    assert jar["sessionid"] == SECRET and "auth_token" not in jar
    assert p.context.username == "main"
    assert list(tmp_path.iterdir()) == []                              # nothing written (no session/resume file)
    assert [i.external_id for i in items] == ["C001", "C002", "C003", "C004"]
    a, b, c, d = items
    assert a.url == "https://www.instagram.com/p/C001/" and a.author_handle == "igu1" and a.author == "IG 1"
    assert a.text == "caption 1 你好" and a.posted_at.startswith("2026-") and a.media_url.endswith("1.jpg")
    assert b.extra.get("carousel") and c.extra.get("video") and d.url == "https://www.instagram.com/reel/C004/"
    assert all(i.extra["via"] == "instaloader" for i in items)


def test_instaloader_incremental_and_missing_cookie(fake_il):
    items = il_backend.collect(IG_STATE, RunConfig(max_items=30, known={"id:C001", "https://www.instagram.com/p/C002/"}, stop_after_known=2), "main")
    assert [i.external_id for i in items] == ["C001", "C002"]
    bad = {"cookies": [c for c in IG_STATE["cookies"] if c["name"] != "ds_user_id"]}
    with pytest.raises(backends.BackendError, match="ds_user_id"):
        il_backend.collect(bad, RunConfig(), "main")


def test_instaloader_rate_limit_fails_fast():
    _, L = il_backend._loader()
    rc = L.context._rate_controller
    with pytest.raises(backends.BackendError, match="rate limit"):
        rc.sleep(600)
    rc.sleep(0)                                                        # short waits still just sleep


# ---------------------------------------------------------------- selection + fallback through the runner
class FakeSink:
    kind = "fake"

    def __init__(self): self.pushed = []

    def check(self): return {"ok": True, "stats": {"items": 0}}

    def ensure_account(self, p, h): return 7

    def push(self, it, account_id, collected_at):
        self.pushed.append(it); return PushResult(item=it, id=len(self.pushed), created=True, confirmed=True)


class FakePage:
    def set_default_timeout(self, ms): pass
    def wait_for_timeout(self, ms): pass


class FakeCtx:
    def new_page(self): return FakePage()
    def close(self): pass
    def storage_state(self, **kw): return X_STATE


@pytest.fixture()
def xsession(tdb, monkeypatch):
    sd = tdb / "sessions"
    sessions.write_state(sessions.session_path("x", sd, "main"), X_STATE)
    used = {"playwright": 0, "unsave": 0}

    def fake_pw_collect(self, page, cfg, session_file=""):
        used["playwright"] += 1
        return [CollectedItem(platform="x", url="https://x.com/pw/status/1", external_id="1", text="from browser")]

    monkeypatch.setattr(runner, "new_context", lambda browser, state: FakeCtx())
    from savehub.collector.platforms.x import XCollector
    monkeypatch.setattr(XCollector, "collect", fake_pw_collect)
    monkeypatch.setattr(XCollector, "unsave", lambda self, page, item, cfg, sf="": used.__setitem__("unsave", used["unsave"] + 1) or "unsaved")
    monkeypatch.setattr(sessions, "save_state", lambda ctx, path, idb=False: path)
    return sd, used


def test_auto_uses_library_without_browser(xsession, fake_tw):
    sd, used = xsession
    sink = FakeSink()
    r = runner.run_collect("x", RunConfig(account_handle="@main", max_items=3, backend="auto"), sink, sd, browser="BROWSER")
    assert r["backend"] == "twscrape" and r["collected"] == 3 and r["created"] == 3 and "fallback" not in r
    assert used["playwright"] == 0 and all(i.source_account == "main" for i in sink.pushed)
    assert sink.pushed[0].to_savehub(7)["source"] == "collector:x:main"


def test_auto_falls_back_to_playwright(xsession, fake_tw):
    sd, used = xsession
    fake_tw.fail_at = 1
    r = runner.run_collect("x", RunConfig(account_handle="@main", backend="auto"), FakeSink(), sd, browser="BROWSER")
    assert r["backend"] == "playwright" and used["playwright"] == 1 and r["collected"] == 1
    assert r["fallback"][0]["backend"] == "twscrape" and SECRET not in json.dumps(r)


def test_auto_falls_back_when_library_missing(xsession, monkeypatch):
    sd, used = xsession
    monkeypatch.setattr(backends, "installed", lambda b: False)
    r = runner.run_collect("x", RunConfig(account_handle="@main", backend="auto"), FakeSink(), sd, browser="BROWSER")
    assert r["backend"] == "playwright" and used["playwright"] == 1 and "fallback" not in r
    with pytest.raises(CollectorError, match="pip install twscrape==0.20.1"):
        runner.run_collect("x", RunConfig(account_handle="@main", backend="twscrape"), FakeSink(), sd, browser="BROWSER")


def test_explicit_library_error_does_not_fall_back(xsession, fake_tw):
    sd, used = xsession
    fake_tw.fail_at = 1
    with pytest.raises(CollectorError, match="twscrape") as e:
        runner.run_collect("x", RunConfig(account_handle="@main", backend="twscrape"), FakeSink(), sd, browser="BROWSER")
    assert used["playwright"] == 0 and SECRET not in str(e.value)


def test_playwright_choice_and_unsave_never_use_library(xsession, fake_tw):
    sd, used = xsession
    r = runner.run_collect("x", RunConfig(account_handle="@main", backend="playwright"), FakeSink(), sd, browser="BROWSER")
    assert r["backend"] == "playwright" and fake_tw.calls == []
    r = runner.run_collect("x", RunConfig(account_handle="@main", backend="twscrape", unsave=True, unsave_confirm=True), FakeSink(), sd, browser="BROWSER")
    assert r["backend"] == "playwright" and fake_tw.calls == [] and used["unsave"] == 1 and r["unsave"]["unsaved"]
    with pytest.raises(CollectorError, match="must be one of"):
        runner.run_collect("x", RunConfig(account_handle="@main", backend="instaloader"), FakeSink(), sd, browser="BROWSER")


def test_injected_browser_without_explicit_backend_uses_playwright(xsession, fake_tw):
    sd, used = xsession
    r = runner.run_collect("x", RunConfig(account_handle="@main"), FakeSink(), sd, browser="BROWSER")
    assert r["backend"] == "playwright" and r["backend_setting"] == "auto" and fake_tw.calls == []


def test_setting_from_db_drives_auto(xsession, fake_tw, monkeypatch):
    sd, used = xsession
    assert backends.get_choice("x") == "auto" and backends.get_choice("instagram") == "playwright"
    assert backends.get_choice("tiktok") == "playwright" and backends.options("tiktok") == ["playwright"]
    backends.set_choice("x", "playwright")
    assert backends.get_choice("x") == "playwright"
    with pytest.raises(ValueError): backends.set_choice("x", "instaloader")
    with pytest.raises(ValueError): backends.set_choice("tiktok", "auto")
    backends.set_choice("x", "auto")
    # no injected browser: the stored setting is used (browser stubbed so nothing real is launched)
    monkeypatch.setattr(runner, "_require_playwright", lambda: (lambda: __import__("contextlib").nullcontext("PW")))
    monkeypatch.setattr(runner, "launch_browser", lambda pw, headful=False: NS(close=lambda: None))
    r = runner.run_collect("x", RunConfig(account_handle="@main", max_items=2), FakeSink(), sd)
    assert r["backend"] == "twscrape" and r["backend_setting"] == "auto"
    backends.set_choice("x", "playwright")
    r = runner.run_collect("x", RunConfig(account_handle="@main", max_items=2), FakeSink(), sd)
    assert r["backend"] == "playwright"


def test_status_rest_and_mcp_expose_backend(tdb, monkeypatch):
    from fastapi.testclient import TestClient
    from savehub import app as appmod, mcp_server
    monkeypatch.delenv("SAVEHUB_DASHBOARD_PASSWORD", raising=False)
    local = TestClient(appmod.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5555))
    remote = TestClient(appmod.app)
    st = local.get("/api/autosync").json()
    assert st["backends"]["x"]["backend"] == "auto" and st["backends"]["x"]["options"] == ["auto", "playwright", "twscrape"]
    assert st["backends"]["instagram"]["options"] == ["auto", "playwright", "instaloader"] and st["backends"]["tiktok"]["options"] == ["playwright"]
    assert st["backends"].get("wechat", {}).get("backend") in (None, "playwright", "auto") or "wechat" in st["backends"]
    r = local.put("/api/collector/backends", json={"platform": "x", "backend": "playwright"})
    assert r.status_code == 200 and r.json()["backends"]["x"]["backend"] == "playwright"
    assert local.put("/api/collector/backends", json={"platform": "x", "backend": "evil"}).status_code == 400
    assert local.put("/api/collector/backends", json={"platform": "x", "backend": "auto", "unsave": True}).status_code == 400
    assert local.put("/api/collector/backends", json={"platform": "wechat", "backend": "playwright"}).status_code in (200, 400)
    assert local.put("/api/collector/backends", json={"platform": "x", "backend": "auto"}, headers={"Origin": "https://evil.example.com"}).status_code == 403
    assert local.put("/api/collector/backends", content='{"platform":"x","backend":"auto"}', headers={"Content-Type": "text/plain"}).status_code == 415
    assert remote.put("/api/collector/backends", json={"platform": "x", "backend": "auto"}).status_code == 403
    lp = {p["id"]: p for p in mcp_server.list_platforms()["platforms"]}
    assert lp["x"]["collector"]["backend"]["backend"] == "playwright"
    assert lp["instagram"]["collector"]["backend"]["libraries"][0]["license"] == "MIT"
    ls = mcp_server.login_status("x")
    assert ls["backend"]["options"] == ["auto", "playwright", "twscrape"] and ls["backend"]["unsave_backend"] == "playwright"
    blob = json.dumps(st) + json.dumps(ls)
    assert SECRET not in blob and "cookie" not in blob.lower().replace("cookies", "")


def test_cli_backend_flag(tdb, capsys):
    from savehub.collector import cli
    code = cli.main(["--sessions-dir", str(tdb / "sessions"), "collect", "x", "--no-push", "--backend", "bogus", "--account", "@nobody"])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["status"] == "need_login"                 # no session: refused before any backend runs
    sessions.write_state(sessions.session_path("x", tdb / "sessions", "main"), X_STATE)
    code = cli.main(["--sessions-dir", str(tdb / "sessions"), "collect", "x", "--no-push", "--backend", "bogus", "--account", "@main"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and "must be one of" in out["error"]
