"""Media schema (normalize / infer_type / durations / URL cleaning / migration / type filter / REST) and the image proxy
(allow-list, scheme, credentials, ports, IP literals, SSRF via DNS, redirects, content checks, size cap, cache, auth)."""
import json, os, socket, sqlite3, tempfile, pathlib
import httpx, pytest
TMP = pathlib.Path(tempfile.mkdtemp(prefix="savehub-media-test-"))
os.environ["SAVEHUB_DB"] = str(TMP / "media.db")
from fastapi.testclient import TestClient
from savehub import app as appmod, db, media, mediaproxy as mp

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PUBLIC = "93.184.216.34"


@pytest.fixture(autouse=True)
def _own_db(monkeypatch, tmp_path):
    """Every test on its own throwaway DB + media cache (never the real data/ directory)."""
    old = db.DB_PATH
    db.DB_PATH = tmp_path / "t.db"
    monkeypatch.setenv("SAVEHUB_MEDIA_CACHE", str(tmp_path / "media-cache"))
    monkeypatch.delenv("SAVEHUB_DASHBOARD_PASSWORD", raising=False)
    yield
    db.DB_PATH = old


def fake_dns(mapping):
    def gai(host, port, *a, **k):
        ips = mapping.get(host)
        if ips is None: raise socket.gaierror("nope")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
    return gai


# ---------------- media schema ----------------
def test_normalize_explicit_and_legacy():
    d = media.normalize({"platform": "instagram", "url": "https://instagram.com/p/x", "images": ["https://a.cdninstagram.com/1.jpg", "https://a.cdninstagram.com/2.jpg"]})
    assert d["type"] == "photo" and d["thumbnail_url"].endswith("1.jpg") and len(d["images"]) == 2
    d = media.normalize({"platform": "x", "url": "https://x.com/a/status/1", "media_url": "https://pbs.twimg.com/media/a.jpg"})
    assert d["thumbnail_url"] == "https://pbs.twimg.com/media/a.jpg" and d["type"] in ("post", "photo")
    d = media.normalize({"platform": "tiktok", "url": "https://www.tiktok.com/@a/video/123", "video_url": "https://v.tiktokcdn.com/x.mp4", "duration": "1:05"})
    assert d["type"] == "video" and d["duration"] == 65 and d["video_url"].endswith(".mp4") and "embed" in (d["embed_url"] or "")
    # images as a JSON string or dicts; capped at 20
    assert media.normalize({"images": json.dumps(["https://pbs.twimg.com/a.jpg"])})["images"] == ["https://pbs.twimg.com/a.jpg"]
    assert media.normalize({"images": [{"url": "https://pbs.twimg.com/b.jpg"}]})["images"] == ["https://pbs.twimg.com/b.jpg"]
    assert len(media.normalize({"images": [f"https://pbs.twimg.com/{i}.jpg" for i in range(40)]})["images"]) == 20


def test_infer_type():
    assert media.infer_type("whatsapp", "", {"kind": "contact"}, False, 0) == "contact"
    assert media.infer_type("icloud", "", {"path": "/Docs/a.pdf"}, False, 0) == "file"
    assert media.infer_type("tiktok", "", {}, True, 0) == "video"
    assert media.infer_type("xiaohongshu", "", {"has_media": True}, False, 0) == "note"
    assert media.infer_type("instagram", "", {"carousel": True}, False, 0) == "photo"
    assert media.infer_type("x", "", {}, False, 0) == "post"
    assert media.infer_type("x", "", {}, False, 0, explicit="video") == "video"


def test_durations_and_aspect():
    for v, want in ((42, 42), ("42", 42), ("1:02", 62), ("1:02:03", 3723), ("PT1M5S", 65), ("PT2H", 7200)):
        assert media.parse_duration(v) == want, v
    for bad in ("", None, "abc", -5, 3 * 86400, "99:99:99:99"):
        assert media.parse_duration(bad) is None, bad
    assert media.aspect_from(1080, 1920) == pytest.approx(0.5625, abs=1e-3) and media.aspect_from(0, 10) is None
    assert media.aspect_from(100, 1) is None   # outside 0.2-5


def test_clean_url():
    assert media.clean_url("https://pbs.twimg.com/a.jpg") == "https://pbs.twimg.com/a.jpg"
    assert media.clean_url("/static/sample-media/x.jpg") == "/static/sample-media/x.jpg"
    for bad in ("javascript:alert(1)", "data:image/png;base64,AAAA", "/static/../data/savehub.db", "file:///etc/passwd", "ftp://x/y.jpg", "//evil.com/a.jpg"):
        assert not media.clean_url(bad), bad
    # a "video_url" that is not a video file is dropped
    assert media.normalize({"platform": "tiktok", "video_url": "https://www.tiktok.com/@a/video/1"})["video_url"] in (None, "")


def test_migration_of_old_db_backfills(tmp_path):
    """A pre-media DB (same schema minus the media columns, user_version 0) is migrated in place and backfilled."""
    db.conn()                                                    # current schema (incl. FTS + triggers) on a scratch file
    c = db.conn()
    c.execute("INSERT INTO items(platform,url,title,media_url,saved_at,raw) VALUES('x','https://x.com/a/status/1','old','https://pbs.twimg.com/media/old.jpg','2026-01-01T00:00:00+00:00','{\"video\": true}')")
    c.execute("INSERT INTO items(platform,url,title,saved_at,raw) VALUES('icloud','icloud://drive/a.pdf','a.pdf','2026-01-01T00:00:00+00:00','{\"path\": \"/a.pdf\"}')")
    c.execute("DROP INDEX IF EXISTS idx_items_type")
    MCOLS = [n for n, _ in db.MEDIA_COLUMNS]
    for col in MCOLS: c.execute(f"ALTER TABLE items DROP COLUMN {col}")
    c.execute("PRAGMA user_version=0"); c.commit()
    old = tmp_path / "old.db"
    c.execute("VACUUM INTO ?", (str(old),))
    assert not set(MCOLS) & {r[1] for r in sqlite3.connect(old).execute("PRAGMA table_info(items)")}
    db.DB_PATH = old                                             # reconnect -> init -> migrate
    c = db.conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(items)")}
    assert set(MCOLS) <= cols
    assert c.execute("PRAGMA user_version").fetchone()[0] >= 2
    rows = {r["title"]: r for r in db.list_items(limit=10)["items"]}
    assert rows["old"]["thumbnail_url"] == "https://pbs.twimg.com/media/old.jpg" and rows["old"]["type"] == "video"
    assert rows["a.pdf"]["type"] == "file" and rows["a.pdf"]["images"] == []
    assert db.list_items(q="old")["total"] == 1                 # FTS still works after the migration


def test_types_filter_and_update_rederives():
    a = db.add_item({"platform": "tiktok", "url": "https://www.tiktok.com/@a/video/9", "title": "v", "video_url": "https://v.tiktokcdn.com/9.mp4"})
    b = db.add_item({"platform": "instagram", "url": "https://instagram.com/p/9", "title": "p", "images": ["https://a.cdninstagram.com/9.jpg"]})
    ida = a[0] if isinstance(a, tuple) else (a["id"] if isinstance(a, dict) else a)
    assert [i["title"] for i in db.list_items(types=["video"])["items"]] == ["v"]
    assert {i["title"] for i in db.list_items(types=["video", "photo"])["items"]} == {"v", "p"}
    db.update_item(ida, {"raw": {"collections": ["Fun"]}})        # a raw dict is stored as JSON
    it = db.get_item(ida)
    assert it["type"] == "video" and it["collections"] == ["Fun"]
    db.update_item(ida, {"media_url": "https://p16.tiktokcdn.com/new.jpg"})
    assert db.get_item(ida)["thumbnail_url"] == "https://p16.tiktokcdn.com/new.jpg"


def test_rest_post_with_images_and_type_param():
    c = TestClient(appmod.app)
    r = c.post("/api/items", json={"url": "https://www.xiaohongshu.com/explore/abc", "title": "note", "platform": "xiaohongshu",
                                   "images": ["https://sns-img.xhscdn.com/1.jpg", "https://sns-img.xhscdn.com/2.jpg", "javascript:alert(1)"], "duration": "0:30"})
    assert r.status_code == 200, r.text
    it = r.json()["item"]
    assert it["images"] == ["https://sns-img.xhscdn.com/1.jpg", "https://sns-img.xhscdn.com/2.jpg"] and it["type"] == "note"
    got = c.get("/api/items", params={"type": "note"}).json()
    assert got["total"] == 1 and got["items"][0]["title"] == "note"
    assert c.get("/api/items", params={"type": "video"}).json()["total"] == 0


def test_csp_img_src_is_same_origin_only():
    csp = TestClient(appmod.app).get("/").headers["content-security-policy"]
    assert "img-src 'self' data:;" in csp and "img-src 'self' data: https:" not in csp


# ---------------- image proxy: URL checks ----------------
@pytest.mark.parametrize("u,status", [
    ("ftp://pbs.twimg.com/a.jpg", 400), ("file:///etc/passwd", 400), ("javascript:alert(1)", 400), ("gopher://pbs.twimg.com/", 400),
    ("https://user:pw@pbs.twimg.com/a.jpg", 400), ("https://pbs.twimg.com@evil.com/a.jpg", 400),
    ("https://pbs.twimg.com:8443/a.jpg", 403), ("http://pbs.twimg.com:22/a.jpg", 403),
    ("https://evil.com/a.jpg", 403), ("https://twimg.com.evil.com/a.jpg", 403), ("https://eviltwimg.com/a.jpg", 403),
    ("https://127.0.0.1/a.jpg", 403), ("https://169.254.169.254/latest/meta-data", 403), ("https://[::1]/a.jpg", 403), ("", 400),
])
def test_check_url_rejects(u, status):
    with pytest.raises(mp.MediaError) as e: mp.check_url(u)
    assert e.value.status == status


def test_check_url_accepts_cdn():
    assert mp.check_url("https://pbs.twimg.com/media/a.jpg?format=jpg")[1] == "pbs.twimg.com"
    assert mp.check_url("https://scontent-sin6-1.cdninstagram.com/v/t51/a.jpg")[1].endswith("cdninstagram.com")
    assert mp.host_allowed("sns-webpic-qc.xhscdn.com") and not mp.host_allowed("xhscdn.com.attacker.net")


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
                                "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "2002:7f00:1::1", "224.0.0.1", "240.0.0.1"])
def test_private_resolution_blocked(monkeypatch, ip):
    assert not mp.ip_public(ip)
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns({"pbs.twimg.com": [ip]}))
    with pytest.raises(mp.MediaError) as e: mp.fetch("https://pbs.twimg.com/a.jpg", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    assert e.value.status == 403


def test_mixed_public_and_private_resolution_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns({"pbs.twimg.com": [PUBLIC, "10.0.0.1"]}))
    with pytest.raises(mp.MediaError) as e: mp.resolve("pbs.twimg.com", 443)
    assert e.value.status == 403


def test_unresolvable_host(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns({}))
    with pytest.raises(mp.MediaError) as e: mp.resolve("pbs.twimg.com", 443)
    assert e.value.status == 502


# ---------------- image proxy: fetching ----------------
def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_fetch_ok_pins_ip_and_caches(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns({"pbs.twimg.com": [PUBLIC]}))
    seen = []
    def h(req):
        seen.append(req); return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=JPEG)
    data, ctype = mp.fetch("https://pbs.twimg.com/media/a.jpg", client=client_for(h))
    assert data == JPEG and ctype == "image/jpeg"
    assert seen[0].url.host == PUBLIC and seen[0].headers["host"] == "pbs.twimg.com"            # connection pinned to the checked IP
    assert seen[0].extensions.get("sni_hostname") == "pbs.twimg.com"                            # TLS still verifies the real name
    # second call is served from the cache (no request)
    assert mp.fetch("https://pbs.twimg.com/media/a.jpg", client=client_for(lambda r: pytest.fail("not cached"))) == (JPEG, "image/jpeg")
    files = list(pathlib.Path(os.environ["SAVEHUB_MEDIA_CACHE"]).glob("*.bin"))
    assert len(files) == 1 and (files[0].stat().st_mode & 0o077) == 0


def test_redirect_to_private_host_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns({"pbs.twimg.com": [PUBLIC], "video.twimg.com": ["192.168.0.10"]}))
    def h(req):
        if req.headers["host"] == "pbs.twimg.com": return httpx.Response(302, headers={"location": "https://video.twimg.com/x.jpg"})
        pytest.fail("must not connect to the private address")
    with pytest.raises(mp.MediaError) as e: mp.fetch("https://pbs.twimg.com/a.jpg", client=client_for(h))
    assert e.value.status == 403


def test_redirect_off_allowlist_and_ip_literal_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns({"pbs.twimg.com": [PUBLIC]}))
    for loc, status in (("https://evil.com/a.jpg", 403), ("http://127.0.0.1/a.jpg", 403), ("http://169.254.169.254/", 403), ("file:///etc/passwd", 400)):
        h = lambda req, loc=loc: httpx.Response(301, headers={"location": loc})
        with pytest.raises(mp.MediaError) as e: mp.fetch("https://pbs.twimg.com/r.jpg", client=client_for(h))
        assert e.value.status == status, loc


def test_too_many_redirects(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns({"pbs.twimg.com": [PUBLIC]}))
    with pytest.raises(mp.MediaError) as e:
        mp.fetch("https://pbs.twimg.com/loop.jpg", client=client_for(lambda r: httpx.Response(302, headers={"location": "/loop.jpg"})))
    assert e.value.status == 502


@pytest.mark.parametrize("ctype,body,status", [
    ("text/html", b"<html>", 415), ("image/svg+xml", b"<svg onload=alert(1)>", 415), ("application/octet-stream", JPEG, 415),
    ("image/png", JPEG, 415), ("image/jpeg", b"<script>alert(1)</script>", 415), ("video/mp4", b"\x00\x00\x00\x18ftypmp42", 415),
])
def test_content_type_and_sniff_checks(monkeypatch, ctype, body, status):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns({"pbs.twimg.com": [PUBLIC]}))
    with pytest.raises(mp.MediaError) as e:
        mp.fetch("https://pbs.twimg.com/c.jpg", client=client_for(lambda r: httpx.Response(200, headers={"content-type": ctype}, content=body)))
    assert e.value.status == status


def test_size_cap(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns({"pbs.twimg.com": [PUBLIC]}))
    monkeypatch.setattr(mp, "MAX_BYTES", 1000)
    big = PNG + b"\x00" * 5000
    with pytest.raises(mp.MediaError) as e:   # declared too large
        mp.fetch("https://pbs.twimg.com/big1.png", client=client_for(lambda r: httpx.Response(200, headers={"content-type": "image/png", "content-length": str(len(big))}, content=big)))
    assert e.value.status == 413
    def chunked(r):  # no content-length: the cap is enforced while streaming
        return httpx.Response(200, headers={"content-type": "image/png"}, stream=httpx.ByteStream(big))
    with pytest.raises(mp.MediaError) as e: mp.fetch("https://pbs.twimg.com/big2.png", client=client_for(chunked))
    assert e.value.status == 413
    assert not list(pathlib.Path(os.environ["SAVEHUB_MEDIA_CACHE"]).glob("*.bin")) if pathlib.Path(os.environ["SAVEHUB_MEDIA_CACHE"]).exists() else True


def test_upstream_error_and_network_error(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns({"pbs.twimg.com": [PUBLIC]}))
    with pytest.raises(mp.MediaError) as e: mp.fetch("https://pbs.twimg.com/404.jpg", client=client_for(lambda r: httpx.Response(404)))
    assert e.value.status == 502
    def boom(r): raise httpx.ConnectTimeout("slow")
    with pytest.raises(mp.MediaError) as e: mp.fetch("https://pbs.twimg.com/t.jpg", client=client_for(boom))
    assert e.value.status == 502


def test_cache_prune_lru():
    for i in range(5):
        mp.cache_put(f"https://pbs.twimg.com/{i}.jpg", JPEG + bytes(1000), "image/jpeg")
    assert mp.cache_get("https://pbs.twimg.com/0.jpg")[1] == "image/jpeg"
    d = mp.cache_dir()
    files = sorted(d.glob("*.bin"), key=lambda f: f.stat().st_mtime)
    for k, f in enumerate(files): os.utime(f, (1000 + k, 1000 + k))
    oldest = files[0]
    removed = mp.prune(limit=mp.cache_size() - 1)
    assert removed == 1 and not oldest.exists() and not oldest.with_suffix(".json").exists()
    assert mp.prune(limit=0) == 4 and mp.cache_size() == 0


# ---------------- image proxy: endpoint auth ----------------
def test_endpoint_requires_login_when_configured(monkeypatch):
    monkeypatch.setenv("SAVEHUB_DASHBOARD_PASSWORD", "pw-for-test")
    monkeypatch.setattr(mp, "fetch", lambda u, client=None: (JPEG, "image/jpeg"))
    c = TestClient(appmod.app)
    assert c.get("/api/media", params={"u": "https://pbs.twimg.com/a.jpg"}).status_code == 401
    r = c.get("/api/media", params={"u": "https://pbs.twimg.com/a.jpg"}, auth=("savehub", "pw-for-test"))
    assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    r = c.get("/api/media", params={"u": "https://pbs.twimg.com/a.jpg"}, auth=("savehub", "pw-for-test"), headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


def test_endpoint_without_login_is_loopback_only(monkeypatch):
    monkeypatch.setattr(mp, "fetch", lambda u, client=None: (JPEG, "image/jpeg"))
    remote = TestClient(appmod.app)                                   # client host "testclient", Host "testserver"
    assert remote.get("/api/media", params={"u": "https://pbs.twimg.com/a.jpg"}).status_code == 403
    local = TestClient(appmod.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    assert local.get("/api/media", params={"u": "https://pbs.twimg.com/a.jpg"}).status_code == 200
    # loopback client but a foreign Host header (DNS rebinding) is refused
    assert local.get("/api/media", params={"u": "https://pbs.twimg.com/a.jpg"}, headers={"host": "evil.example"}).status_code in (400, 403)


def test_endpoint_maps_errors(monkeypatch):
    local = TestClient(appmod.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    assert local.get("/api/media", params={"u": "https://evil.com/a.jpg"}).status_code == 403
    assert local.get("/api/media", params={"u": "ftp://pbs.twimg.com/a.jpg"}).status_code == 400
    assert local.get("/api/media").status_code == 400


# ---------------- collectors share the schema ----------------
def test_collected_item_carries_media():
    from savehub.collector.base import CollectedItem
    d = CollectedItem(platform="instagram", url="https://www.instagram.com/p/ABC/", text="carousel",
                      images=["https://a.cdninstagram.com/1.jpg", "https://a.cdninstagram.com/2.jpg"], author_avatar="https://a.cdninstagram.com/av.jpg",
                      aspect=0.8).to_savehub()
    assert d["type"] == "photo" and d["thumbnail_url"].endswith("1.jpg") and len(d["images"]) == 2
    assert d["author_avatar_url"].endswith("av.jpg") and d["aspect"] == 0.8
    v = CollectedItem(platform="tiktok", url="https://www.tiktok.com/@a/video/77", text="clip", media_url="https://p16.tiktokcdn.com/cover.jpg",
                      video_url="https://v16-webapp.tiktok.com/video/tos/77.mp4", duration=12.4, media_type="video").to_savehub()
    assert v["type"] == "video" and v["thumbnail_url"].endswith("cover.jpg") and v["video_url"].endswith("77.mp4") and v["duration"] == 12
    assert v["embed_url"] and "77" in v["embed_url"]
    for f in media.FIELDS: assert f in v
