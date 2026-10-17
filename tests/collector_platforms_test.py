"""Fixture tests for the non-X collectors (Threads, Instagram, TikTok, Xiaohongshu, Douyin, Facebook, Messenger,
WhatsApp). No real social-network traffic: every request is answered from tests/fixtures by a Playwright route; the
JSON-interception path is exercised by fixture pages that fetch() fake XHR endpoints like the real apps do.

Run:  .venv/bin/python -m pytest -q tests/collector_platforms_test.py
"""
import json, re, sys, pathlib
from urllib.parse import urlparse, parse_qs
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))
from savehub.collector import RunConfig, NeedLogin, get_collector, REGISTRY
from savehub.collector import runner
from savehub.collector.base import CollectorError
from savehub.collector.client import PushResult

FAST = dict(scroll_delay=0.2, scroll_jitter=0.05, idle_rounds=4, unsave_delay=0.05, page_timeout_ms=15000)


def fx(name): return (FX / name).read_text()


class Fake:
    """Serves one fake app. pages: [(path_regex, fn(match, fake, request) -> str | (status, ctype, body))]."""
    def __init__(self, hosts, pages, login_re, login_page, login_redirect, logged_in=True):
        """login_redirect=None: logged-out pages render the login UI in place (XHS / Douyin show a login modal, no redirect)."""
        self.hosts, self.pages, self.login_re, self.login_page, self.login_redirect = hosts, pages, re.compile(login_re), login_page, login_redirect
        self.logged_in, self.unsaved, self.requests, self.saved = logged_in, [], [], set()

    def handle(self, route):
        u = urlparse(route.request.url)
        if u.hostname not in self.hosts: return route.abort()               # CDN/images: offline
        self.requests.append(u.path + ("?" + u.query if u.query else ""))
        if u.path == "/__test/unsave":
            i = parse_qs(u.query)["id"][0]; self.unsaved.append(i); self.saved.discard(i); return route.fulfill(status=200, body="ok")
        if self.login_re.match(u.path):
            return route.fulfill(status=200, content_type="text/html", body=fx(self.login_page))
        if not self.logged_in and self.login_redirect is None:
            return route.fulfill(status=200, content_type="text/html", body=fx(self.login_page))
        if not self.logged_in:     # client-side redirect: a fulfilled 302 would be followed outside the route (real network)
            return route.fulfill(status=200, content_type="text/html",
                                 body=f"<html><body><script>location.replace({json.dumps(self.login_redirect)})</script></body></html>")
        for rx, fn in self.pages:
            m = re.match(rx, u.path)
            if m:
                r = fn(m, self, route.request)
                if isinstance(r, tuple): st, ct, body = r
                else: st, ct, body = 200, ("application/json" if r.lstrip()[:1] in "{[" else "text/html"), r
                return route.fulfill(status=st, content_type=ct, body=body)
        return route.fulfill(status=404, body="not found")

    def hook(self, ctx): ctx.route("**/*", self.handle)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = runner.launch_browser(p, headful=False)
        yield b
        b.close()


def sessdir_for(tmp_path, platform, domain):
    d = tmp_path / "sessions"; d.mkdir(exist_ok=True)
    (d / f"{platform}.json").write_text(json.dumps({"cookies": [{"name": "sessionid", "value": "dummy-test-cookie", "domain": domain, "path": "/",
                                                               "expires": -1, "httpOnly": True, "secure": True, "sameSite": "None"}], "origins": []}))
    return d


def page_for(browser, fake):
    ctx = runner.new_context(browser); fake.hook(ctx); return ctx, ctx.new_page()


class OkSink:
    """In-memory sink: every item is stored + confirmed."""
    kind = "fake"
    def __init__(self): self.items = []
    def check(self): return {"stats": {}}
    def resolve_account(self, *a): return None
    def push(self, item, account_id=None, collected_at=""): self.items.append(item); return PushResult(item, id=len(self.items), created=True, confirmed=True)
    def close(self): pass


def collect(browser, fake, platform, **cfg):
    ctx, pg = page_for(browser, fake)
    try: return get_collector(platform).collect(pg, RunConfig(**{**FAST, **cfg}))
    finally: ctx.close()


def assert_need_login(browser, tmp_path, fake, platform, domain, login_part):
    fake.logged_in = False
    with pytest.raises(NeedLogin) as e:
        runner.run_collect(platform, RunConfig(**FAST), None, sessdir_for(tmp_path, platform, domain), browser=browser, context_hook=fake.hook)
    assert e.value.exit_code == 2 and f"login {platform}" in str(e.value) and login_part in e.value.detail
    fake.logged_in = True


def unsave_cycle(browser, tmp_path, fake, platform, domain, expect_ids, max_unsave=2, **cfg):
    """dry-run touches nothing; live un-saves (capped) and verifies; already-unsaved is reported as such."""
    sd = sessdir_for(tmp_path, platform, domain)
    s = runner.run_collect(platform, RunConfig(**FAST, **cfg, unsave=True), OkSink(), sd, browser=browser, context_hook=fake.hook)
    assert s["unsave"]["mode"] == "dry-run" and len(s["unsave"]["would_unsave"]) == s["collected"] and fake.unsaved == []
    s = runner.run_collect(platform, RunConfig(**FAST, **cfg, unsave=True, unsave_confirm=True, max_unsave=max_unsave), OkSink(), sd,
                           browser=browser, context_hook=fake.hook)
    u = s["unsave"]
    assert u["mode"] == "live" and u["failed"] == [] and len(u["unsaved"]) == max_unsave, u
    assert fake.unsaved == expect_ids[:max_unsave]
    return s


# ====================================================================== Threads
def threads_fake():
    f = Fake({"www.threads.com", "www.threads.net", "threads.com"}, [], r"^/login", "threads_login.html", "https://www.threads.com/login?next=/saved")
    f.saved = {"DThrA1", "DThrB2", "DThrC3", "DThrD4", "DThrX9"}
    f.pages = [
        (r"^/saved$", lambda m, f, r: fx("threads_saved.html")),
        (r"^/api/graphql$", lambda m, f, r: fx("threads_graphql.json")),
        (r"^/@([\w.]+)/post/([\w-]+)$", lambda m, f, r: fx("threads_post.html").replace("{{H}}", m[1]).replace("{{CODE}}", m[2])
            .replace("{{SAVED}}", "true" if m[2] in f.saved else "false")),
    ]
    return f


def test_threads_collect(browser):
    items = collect(browser, threads_fake(), "threads")
    ids = [i.external_id for i in items]
    assert sorted(ids) == ["DThrA1", "DThrB2", "DThrC3", "DThrD4", "DThrX9"] and ids[:3] == ["DThrA1", "DThrB2", "DThrC3"], ids  # dup + quoted post not separate
    a, b, c, d, x = (next(i for i in items if i.external_id == k) for k in ["DThrA1", "DThrB2", "DThrC3", "DThrD4", "DThrX9"])
    assert a.url == "https://www.threads.com/@ada.threads/post/DThrA1" and a.author == "Ada Threads"
    assert a.text == "Embedded JSON caption for A1 — 中文 ok" and a.posted_at == "2025-10-09T08:53:20+00:00"   # JSON wins over DOM
    assert b.author_handle == "kopi.lover" and b.has_media and b.media_url.endswith("kopi.jpg") and b.author == "Kopi Lover"  # DOM, enriched by XHR
    assert c.text == "Agree with this" and "quoted" not in c.text and c.posted_at == "2026-10-12T02:00:00.000Z"
    assert d.has_media and d.extra.get("video")
    assert x.author_handle == "xhr.only" and x.text == "Only in the XHR page 2" and x.media_url.endswith("x9.jpg")       # XHR only
    p = x.to_savehub(collected_at="2026-10-17T00:00:00+00:00")
    assert p["platform"] == "threads" and p["source"] == "collector:threads" and p["title"] == "Only in the XHR page 2"
    assert len(collect(browser, threads_fake(), "threads", max_items=2)) == 2


def test_threads_login_and_unsave(browser, tmp_path):
    f = threads_fake()
    assert_need_login(browser, tmp_path, f, "threads", ".threads.com", "/login")
    ctx = runner.new_context(browser); pg = ctx.new_page(); col = get_collector("threads")
    try:
        pg.set_content(fx("threads_login.html")); assert col.is_login_page(pg) and not col.is_logged_in(pg)
        pg.set_content(fx("threads_saved.html")); assert col.is_logged_in(pg) and not col.is_login_page(pg)
    finally: ctx.close()
    unsave_cycle(browser, tmp_path, f, "threads", ".threads.com", ["DThrA1", "DThrB2"])
    ctx, pg = page_for(browser, f)       # already un-saved -> reported, nothing clicked
    try:
        it = get_collector("threads").to_item({"id": "DThrA1", "url": "https://www.threads.com/@ada.threads/post/DThrA1"})
        assert get_collector("threads").unsave(pg, it, RunConfig(**FAST)) == "already_unsaved"
    finally: ctx.close()
    assert f.unsaved == ["DThrA1", "DThrB2"]


# ====================================================================== Instagram
IG_ALL = [[{"href": "/p/IGaaa1/", "src": "https://scontent.cdninstagram.com/v/ig1.jpg", "alt": "Photo by Ada IG on October 09, 2025."},
           {"href": "/kopi.ig/reel/IGbbb2/", "src": "https://scontent.cdninstagram.com/v/ig2.jpg", "reel": True},
           {"href": "/p/IGccc3/", "src": "https://scontent.cdninstagram.com/v/ig3.jpg", "car": True}],
          [{"href": "/p/IGddd4/", "src": "https://scontent.cdninstagram.com/v/ig4.jpg"}, {"href": "/p/IGaaa1/", "src": "x"}]]
IG_COL = {"food-kl": [[{"href": "/p/IGaaa1/", "src": "a"}, {"href": "/p/IGddd4/", "src": "b"}]], "travel": [[{"href": "/p/IGccc3/", "src": "c"}]]}


def ig_fake():
    f = Fake({"www.instagram.com", "instagram.com"}, [], r"^/accounts/login", "instagram_login.html", "https://www.instagram.com/accounts/login/?next=%2F")
    f.saved = {"IGaaa1", "IGbbb2", "IGccc3", "IGddd4", "IGxhr7"}
    grid = lambda rows, fetch, title: fx("instagram_grid.html").replace("{{ROWS}}", json.dumps(rows)).replace("{{FETCH}}", fetch).replace("{{TITLE}}", title)
    f.pages = [
        (r"^/$", lambda m, f, r: fx("instagram_home.html")),
        (r"^/js\.sample/saved/$", lambda m, f, r: fx("instagram_saved.html")),
        (r"^/js\.sample/saved/all-posts/$", lambda m, f, r: grid(IG_ALL, "true", "All posts")),
        (r"^/js\.sample/saved/([\w-]+)/\d+/$", lambda m, f, r: grid(IG_COL[m[1]], "false", m[1])),
        (r"^/api/v1/feed/saved/posts/$", lambda m, f, r: fx("instagram_saved_feed.json")),
        (r"^/(?:p|reel)/([\w-]+)/$", lambda m, f, r: fx("instagram_post.html").replace("{{CODE}}", m[1])
            .replace("{{SAVED}}", "true" if m[1] in f.saved else "false")
            .replace("{{ICON}}", '<svg aria-label="Remove" width="24" height="24"><rect width="24" height="24"/></svg>' if m[1] in f.saved else '<svg aria-label="Save"></svg>')),
    ]
    return f


def test_instagram_collect_with_collections(browser):
    items = collect(browser, ig_fake(), "instagram")
    by = {i.external_id: i for i in items}
    assert sorted(by) == ["IGaaa1", "IGbbb2", "IGccc3", "IGddd4", "IGxhr7"] and len(items) == 5
    a = by["IGaaa1"]
    assert a.url == "https://www.instagram.com/p/IGaaa1/" and a.author == "Ada IG" and a.text == "Sunset at Batu Ferringhi"   # XHR enrichment
    assert a.posted_at == "2025-10-09T08:53:20+00:00" and a.extra["collections"] == ["Food KL"]
    assert by["IGbbb2"].url == "https://www.instagram.com/reel/IGbbb2/" and by["IGbbb2"].author_handle == "kopi.ig" and by["IGbbb2"].extra["video"]
    assert by["IGccc3"].extra["carousel"] and by["IGccc3"].extra["collections"] == ["Travel"]
    assert by["IGddd4"].extra["collections"] == ["Food KL"] and "collections" not in by["IGbbb2"].extra
    x = by["IGxhr7"]
    assert x.author_handle == "xhr.chef" and x.has_media and x.media_url.endswith("ig7.jpg") and x.extra["carousel"] == 2
    assert len(collect(browser, ig_fake(), "instagram", max_items=3)) == 3
    # --account picks the username directly (no home-page lookup)
    f = ig_fake(); collect(browser, f, "instagram", account_handle="@js.sample", max_items=2)
    assert "/" not in f.requests[:1]


def test_instagram_login_and_unsave(browser, tmp_path):
    f = ig_fake()
    assert_need_login(browser, tmp_path, f, "instagram", ".instagram.com", "/accounts/login")
    ctx = runner.new_context(browser); pg = ctx.new_page(); col = get_collector("instagram")
    try:
        pg.set_content(fx("instagram_login.html")); assert col.is_login_page(pg) and not col.is_logged_in(pg)
        pg.set_content(fx("instagram_home.html")); assert col.is_logged_in(pg)
    finally: ctx.close()
    unsave_cycle(browser, tmp_path, f, "instagram", ".instagram.com", ["IGaaa1", "IGbbb2"])
    ctx, pg = page_for(browser, f)
    try:
        assert col.unsave(pg, col.to_item({"id": "IGaaa1", "url": "https://www.instagram.com/p/IGaaa1/"}), RunConfig(**FAST)) == "already_unsaved"
    finally: ctx.close()


# ====================================================================== TikTok
TT_IDS = ["7300000000000000001", "7300000000000000002", "7300000000000000003", "7300000000000000009"]


def tt_fake():
    f = Fake({"www.tiktok.com", "tiktok.com"}, [], r"^/login", "tiktok_login.html", "https://www.tiktok.com/login?redirect_url=x")
    f.saved = set(TT_IDS)
    f.pages = [
        (r"^/$", lambda m, f, r: '<html><body><a data-e2e="nav-profile" href="/@js.sample"><span data-e2e="profile-icon"></span></a></body></html>'),
        (r"^/@js\.sample$", lambda m, f, r: fx("tiktok_profile.html")),
        (r"^/api/user/collect/item_list/$", lambda m, f, r: fx("tiktok_collect.json")),
        (r"^/api/post/item_list/$", lambda m, f, r: fx("tiktok_own_posts.json")),
        (r"^/@([\w.-]+)/(?:video|photo)/(\d+)$", lambda m, f, r: fx("tiktok_video.html").replace("{{ID}}", m[2])
            .replace("{{SAVED}}", "true" if m[2] in f.saved else "false")),
    ]
    return f


def test_tiktok_collect(browser):
    items = collect(browser, tt_fake(), "tiktok")
    by = {i.external_id: i for i in items}
    assert sorted(by) == TT_IDS and len(items) == 4 and "7000000000000000001" not in by       # own video never collected
    a = by[TT_IDS[0]]
    assert a.url == "https://www.tiktok.com/@kopi.tok/video/7300000000000000001" and a.author_handle == "kopi.tok"
    assert a.text == "Kopi art in 30s #kl" and a.media_url.endswith("f1.jpg") and a.extra["views"] == "1.2K"
    p = by[TT_IDS[1]]
    assert p.url.endswith("/@photo.mode/photo/7300000000000000002") and p.extra["photo"] and p.author == "Photo Mode"
    assert p.posted_at == "2025-10-10T12:40:00+00:00"                                                           # XHR enrichment
    x = by[TT_IDS[3]]
    assert x.author == "Chef Tok 🍛" and x.text.startswith("Only in the XHR page") and x.media_url.endswith("f9.jpg") and x.extra["music"]
    assert x.to_savehub()["source"] == "collector:tiktok"
    assert len(collect(browser, tt_fake(), "tiktok", max_items=2)) == 2


def test_tiktok_login_and_unsave(browser, tmp_path):
    f = tt_fake()
    assert_need_login(browser, tmp_path, f, "tiktok", ".tiktok.com", "/login")
    ctx = runner.new_context(browser); pg = ctx.new_page(); col = get_collector("tiktok")
    try:
        pg.set_content(fx("tiktok_login.html")); assert col.is_login_page(pg) and not col.is_logged_in(pg)
        pg.set_content(fx("tiktok_profile.html")); assert col.is_logged_in(pg) and not col.is_login_page(pg)
    finally: ctx.close()
    sd = sessdir_for(tmp_path, "tiktok", ".tiktok.com")
    s = runner.run_collect("tiktok", RunConfig(**FAST, unsave=True), OkSink(), sd, browser=browser, context_hook=f.hook)
    assert s["unsave"]["mode"] == "dry-run" and f.unsaved == []
    s = runner.run_collect("tiktok", RunConfig(**FAST, unsave=True, unsave_confirm=True, max_unsave=2), OkSink(), sd, browser=browser, context_hook=f.hook)
    order = [i["url"].rsplit("/", 1)[1] for i in s["items"]]
    assert s["unsave"]["failed"] == [] and len(s["unsave"]["unsaved"]) == 2 and f.unsaved == order[:2]
    ctx, pg = page_for(browser, f)
    try:
        it = col.to_item({"id": order[0], "url": s["items"][0]["url"]})
        assert col.unsave(pg, it, RunConfig(**FAST)) == "already_unsaved"
    finally: ctx.close()


# ====================================================================== Douyin
DY_IDS = ["7400000000000000001", "7400000000000000002", "7400000000000000009"]


def dy_fake():
    f = Fake({"www.douyin.com", "douyin.com"}, [], r"^/__never", "douyin_login.html", None)
    f.saved = set(DY_IDS)
    f.pages = [
        (r"^/user/self$", lambda m, f, r: fx("douyin_favorites.html")),
        (r"^/aweme/v1/web/aweme/listcollection/$", lambda m, f, r: fx("douyin_listcollection.json")),
        (r"^/(?:video|note)/(\d+)$", lambda m, f, r: fx("douyin_video.html").replace("{{ID}}", m[1])
            .replace("{{STATE}}", "video-player-is-collected" if m[1] in f.saved else "video-player-no-collect")),
    ]
    return f


def test_douyin_collect(browser):
    items = collect(browser, dy_fake(), "douyin")
    by = {i.external_id: i for i in items}
    assert sorted(by) == DY_IDS and len(items) == 3
    v = by[DY_IDS[0]]
    assert v.url == "https://www.douyin.com/video/7400000000000000001" and v.text == "成都火锅探店 🌶️" and v.author == "火锅达人"
    assert v.author_handle == "hotpot_99" and v.posted_at == "2025-10-09T08:53:20+00:00"
    assert by[DY_IDS[1]].url == "https://www.douyin.com/note/7400000000000000002" and by[DY_IDS[1]].extra["note"]
    x = by[DY_IDS[2]]
    assert x.text == "只在接口里: 广式早茶" and x.author_handle == "zaocha_jun" and x.extra["author_sec_uid"] == "MS4wSEC9" and x.media_url.endswith("v9.jpg")
    assert len(collect(browser, dy_fake(), "douyin", max_items=1)) == 1


def test_douyin_login_and_unsave(browser, tmp_path):
    f = dy_fake()
    assert_need_login(browser, tmp_path, f, "douyin", ".douyin.com", "")
    unsave_cycle(browser, tmp_path, f, "douyin", ".douyin.com", DY_IDS[:2])
    ctx, pg = page_for(browser, f); col = get_collector("douyin")
    try: assert col.unsave(pg, col.to_item({"id": DY_IDS[0], "url": "https://www.douyin.com/video/" + DY_IDS[0]}), RunConfig(**FAST)) == "already_unsaved"
    finally: ctx.close()


# ====================================================================== Xiaohongshu
XHS_IDS = ["6700000000000000000000a1", "6700000000000000000000b2", "6700000000000000000000c3"]


def xhs_fake():
    f = Fake({"www.xiaohongshu.com", "xiaohongshu.com"}, [], r"^/__never", "xhs_login.html", None)
    f.saved = set(XHS_IDS)
    f.pages = [
        (r"^/explore$", lambda m, f, r: fx("xhs_explore.html")),
        (r"^/user/profile/5f0000000000000000000abc$", lambda m, f, r: fx("xhs_profile.html")),
        (r"^/api/sns/web/v2/note/collect/page$", lambda m, f, r: fx("xhs_collect.json")),
        (r"^/explore/([0-9a-f]{24})$", lambda m, f, r: fx("xhs_note.html").replace("{{ID}}", m[1])
            .replace("{{TOK}}", (parse_qs(urlparse(r.url).query).get("xsec_token") or [""])[0])
            .replace("{{CLS}}", "collected" if m[1] in f.saved else "")),
    ]
    return f


def test_xiaohongshu_collect(browser):
    items = collect(browser, xhs_fake(), "xiaohongshu")
    by = {i.external_id: i for i in items}
    assert sorted(by) == XHS_IDS and len(items) == 3 and "6600000000000000000000ff" not in by       # own note not collected
    a = by[XHS_IDS[0]]
    assert a.url == "https://www.xiaohongshu.com/explore/6700000000000000000000a1" and a.title == "吉隆坡咖啡馆合集 ☕"
    assert a.author == "咖啡猫" and a.author_handle == "5e00000000000000000000a1" and a.extra["xsec_token"] == "ABtokA1" and a.media_url.endswith("a1.jpg")
    b = by[XHS_IDS[1]]
    assert b.url.endswith("/explore/6700000000000000000000b2") and b.extra["video"] and b.extra["xsec_token"] == "ABtokB2"
    c = by[XHS_IDS[2]]
    assert c.title == "只在接口里：怡保芽菜鸡" and c.author == "怡保仔" and c.extra["xsec_token"] == "ABtokC3"
    p = a.to_savehub(); assert p["title"] == "吉隆坡咖啡馆合集 ☕" and p["platform"] == "xiaohongshu"
    assert len(collect(browser, xhs_fake(), "xiaohongshu", max_items=2)) == 2
    f = xhs_fake(); collect(browser, f, "xiaohongshu", account_handle="5f0000000000000000000abc", max_items=1)
    assert not any(r.startswith("/explore") for r in f.requests)                                     # user id given: no lookup


def test_xiaohongshu_login_and_unsave(browser, tmp_path):
    f = xhs_fake()
    assert_need_login(browser, tmp_path, f, "xiaohongshu", ".xiaohongshu.com", "")
    ctx = runner.new_context(browser); pg = ctx.new_page(); col = get_collector("xiaohongshu")
    try:
        pg.set_content(fx("xhs_login.html")); assert col.is_login_page(pg) and not col.is_logged_in(pg)
        pg.set_content(fx("xhs_explore.html")); assert col.is_logged_in(pg) and not col.is_login_page(pg)
    finally: ctx.close()
    unsave_cycle(browser, tmp_path, f, "xiaohongshu", ".xiaohongshu.com", XHS_IDS[:2])
    assert any("tok=ABtokA1" in r for r in f.requests)                                                # opened with its xsec_token


# ====================================================================== Facebook
def fb_fake():
    f = Fake({"www.facebook.com", "facebook.com"}, [], r"^/login", "facebook_login.html", "https://www.facebook.com/login/?next=%2Fsaved%2F")
    f.pages = [(r"^/saved/$", lambda m, f, r: fx("facebook_saved.html"))]
    return f


def test_facebook_collect(browser):
    items = collect(browser, fb_fake(), "facebook")
    assert [i.url for i in items] == ["https://www.facebook.com/AdaPage/posts/pfbid0ABC", "https://example.com/nasi-lemak?id=7",
                                      "https://www.facebook.com/watch/?v=999000111"]
    a, n, v = items
    assert a.title == "Ada's post about kopi" and a.author == "Ada Page" and a.extra["kind"] == "Post" and a.media_url.endswith("s1.jpg")
    assert a.extra["saved_from_url"] == "https://www.facebook.com/AdaPage/posts/pfbid0ABC" and "idx" not in a.extra
    assert n.title == "The best nasi lemak in KL" and n.author == "Foodie Group" and n.extra["kind"] == "Link"
    assert v.extra["video"] and v.author == "Satay King" and v.has_media
    assert len({i.external_id for i in items}) == 3 and a.to_savehub()["title"] == "Ada's post about kopi"
    assert len(collect(browser, fb_fake(), "facebook", max_items=2)) == 2


def test_facebook_login_and_unsave(browser, tmp_path):
    f = fb_fake()
    assert_need_login(browser, tmp_path, f, "facebook", ".facebook.com", "/login")
    ctx = runner.new_context(browser); pg = ctx.new_page(); col = get_collector("facebook")
    try:
        pg.set_content(fx("facebook_login.html")); assert col.is_login_page(pg) and not col.is_logged_in(pg)
        pg.set_content(fx("facebook_saved.html")); assert col.is_logged_in(pg) and not col.is_login_page(pg)
    finally: ctx.close()
    unsave_cycle(browser, tmp_path, f, "facebook", ".facebook.com", ["0", "1"])


# ====================================================================== Messenger (contacts)
def ms_fake():
    f = Fake({"www.messenger.com", "messenger.com"}, [], r"^/login", "messenger_login.html", "https://www.messenger.com/login/")
    f.pages = [(r"^/$", lambda m, f, r: fx("messenger_chats.html"))]
    return f


def test_messenger_contacts(browser, tmp_path):
    items = collect(browser, ms_fake(), "messenger")
    assert [i.url for i in items] == ["https://www.messenger.com/t/100001/", "https://www.messenger.com/e2ee/t/7000123/",
                                      "https://www.messenger.com/t/ben.tan/", "https://www.messenger.com/t/100004/"]
    a, g, b, c = items
    assert a.title == "Ada Lim" and a.extra == {"kind": "contact", "thread_id": "100001", "last_message": "You: see you at 8 · 2h"}
    assert g.title == "Kopi Gang 🇲🇾" and g.extra["e2ee"] and g.extra["unread"]
    p = a.to_savehub(); assert p["title"] == "Ada Lim" and p["platform"] == "messenger" and p["content"] == ""
    assert len(collect(browser, ms_fake(), "messenger", max_items=2)) == 2
    col = get_collector("messenger"); assert col.kind == "contacts" and not col.supports_unsave and col.describe()["supports_unsave"] is False
    f = ms_fake(); sd = sessdir_for(tmp_path, "messenger", ".messenger.com")
    with pytest.raises(CollectorError, match="no un-save action"):
        runner.run_collect("messenger", RunConfig(**FAST, unsave=True), OkSink(), sd, browser=browser, context_hook=f.hook)
    assert f.requests == []                                                                       # refused before any browsing
    assert_need_login(browser, tmp_path, f, "messenger", ".messenger.com", "/login")


# ====================================================================== WhatsApp (contacts + starred)
def wa_fake(idb=True):
    f = Fake({"web.whatsapp.com"}, [], r"^/__never", "whatsapp_qr.html", None)
    f.pages = [(r"^/$", lambda m, f, r: fx("whatsapp_web.html").replace("{{IDB}}", "true" if idb else "false"))]
    return f


def test_whatsapp_contacts_idb_dom_and_starred(browser):
    items = collect(browser, wa_fake(), "whatsapp")
    by = {i.url: i for i in items}
    assert set(by) == {"https://wa.me/60120000001", "https://wa.me/60120000002", "https://web.whatsapp.com/#group/120363000000000001@g.us",
                       "https://wa.me/60120000003", "https://web.whatsapp.com/#chat/Mum%20%E2%9D%A4%EF%B8%8F"} | \
                      {u for u in by if u.startswith("https://web.whatsapp.com/#starred/")}
    ada = by["https://wa.me/60120000001"]
    assert ada.title == "Ada Lim" and ada.extra["phone"] == "+60120000001" and ada.extra["in_address_book"] and ada.extra["unread"] == 2
    assert ada.extra["last_chat_at"] == "2025-10-09T08:53:20+00:00" and ada.extra["kind"] == "contact"
    assert by["https://wa.me/60120000002"].title == "Ben T" and by["https://wa.me/60120000002"].extra["business"]
    assert by["https://web.whatsapp.com/#group/120363000000000001@g.us"].title == "Kopi Gang"
    assert by["https://wa.me/60120000003"].extra["last_message"] == "hello"                          # DOM-only phone row
    starred = [i for i in items if i.extra.get("kind") == "starred_message"]
    assert sorted(s.text for s in starred) == ["Meet at Jalan Alor, 8pm", "Wifi password is kopi-1234"]
    assert {s.extra["chat"] for s in starred} == {"Ada Lim > You", "Kopi Gang"} and all(s.extra.get("meta") for s in starred)
    assert len(items) == 7


def test_whatsapp_dom_fallback_login_and_session_idb(browser, tmp_path):
    items = collect(browser, wa_fake(idb=False), "whatsapp", max_items=3)
    assert [i.title for i in items] == ["Ada Lim", "Kopi Gang", "+60 12-000 0003"] and items[1].extra["kind"] == "group"
    assert items[0].url == "https://web.whatsapp.com/#chat/Ada%20Lim" and items[2].url == "https://wa.me/60120000003"
    col = get_collector("whatsapp")
    assert col.needs_indexed_db and col.kind == "contacts"
    f = wa_fake(); assert_need_login(browser, tmp_path, f, "whatsapp", "web.whatsapp.com", "")
    ctx = runner.new_context(browser); pg = ctx.new_page()
    try:
        pg.set_content(fx("whatsapp_qr.html")); assert col.is_login_page(pg) and not col.is_logged_in(pg)
    finally: ctx.close()
    # the run saves IndexedDB into the session file (WhatsApp's login keys live there)
    sd = sessdir_for(tmp_path, "whatsapp", "web.whatsapp.com")
    runner.run_collect("whatsapp", RunConfig(**FAST, max_items=2), None, sd, browser=browser, context_hook=wa_fake().hook)
    st = json.loads((sd / "whatsapp.json").read_text())
    dbs = [d["name"] for o in st["origins"] for d in o.get("indexedDB", [])]
    assert "model-storage" in dbs


def test_wechat_stays_stub_with_reason():
    c = get_collector("wechat")
    assert not c.implemented and "no usable web client" in c.notes
    impl = sorted(k for k, v in REGISTRY.items() if v.implemented)
    assert impl == ["douyin", "facebook", "icloud", "instagram", "messenger", "threads", "tiktok", "whatsapp", "x", "xiaohongshu"]
