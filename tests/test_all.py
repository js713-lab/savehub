"""End-to-end tests on an isolated temp DB (the live library is untouched)."""
import os, json, tempfile, pathlib
os.environ["SAVEHUB_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
from fastapi.testclient import TestClient
from savehub import app as appmod, seed
S = pathlib.Path(__file__).resolve().parent.parent / "samples"
c = TestClient(appmod.app)
seed.seed()

def imp(kind, fname, platform=""):
    with open(S / fname, "rb") as f:
        r = c.post("/api/import", data={"kind": kind, "platform": platform}, files={"file": (fname, f)})
    assert r.status_code == 200, r.text
    return r.json()

def test_health_and_seed():
    j = c.get("/api/health").json(); assert j["stats"]["items"] == 30 and j["stats"]["accounts"] == 7

def test_search_en_zh_and_short():
    assert c.get("/api/items", params={"q": "python"}).json()["total"] >= 1
    assert any("咖啡" in i["title"] for i in c.get("/api/items", params={"q": "咖啡店"}).json()["items"])
    assert c.get("/api/items", params={"q": "咖啡"}).json()["total"] >= 1  # <3 chars -> LIKE fallback

def test_sorts_filters():
    for s in ("saved_at", "platform", "author", "title"):
        for o in ("asc", "desc"):
            it = c.get("/api/items", params={"sort": s, "order": o, "limit": 100}).json()["items"]
            keys = [(i[s] or "").lower() if s in ("author", "title") else (i[s] or "") for i in it]
            assert keys == sorted(keys, reverse=(o == "desc")), (s, o)
    r = c.get("/api/items", params={"platform": "xiaohongshu,douyin"}).json()
    assert r["total"] == 6 and {i["platform"] for i in r["items"]} == {"xiaohongshu", "douyin"}
    acc = c.get("/api/accounts", params={"platform": "instagram"}).json()["accounts"][0]
    r = c.get("/api/items", params={"account_id": acc["id"]}).json(); assert r["total"] == 3

def test_tagging_and_add():
    r = c.post("/api/items", json={"url": "https://x.com/someone/status/42", "title": "Bitcoin ETF flows and gold price", "content": "crypto and gold investing"}).json()
    assert r["created"] and r["item"]["platform"] == "x" and "finance" in r["item"]["tags"] and r["item"]["summary"]
    assert c.post("/api/tag", json={"force": True}).json()["retagged"] >= 31

def test_summary():
    s = c.get("/api/summary", params={"days": 7}).json()
    assert s["total"] > 5 and s["by_platform"] and s["top_tags"] and s["digest"]

def test_importers():
    res = {
        "instagram_json": imp("instagram_json", "instagram_saved_posts.json"),
        "facebook_json": imp("facebook_json", "facebook_saved_items.json"),
        "threads_json": imp("threads_json", "threads_saved.json"),
        "messenger_json": imp("messenger_json", "messenger_message_1.json"),
        "tiktok_json": imp("tiktok_json", "tiktok_user_data.json"),
        "whatsapp_txt": imp("whatsapp_txt", "WhatsApp Chat with Sample Group.txt"),
        "bookmarks_html": imp("bookmarks_html", "bookmarks.html"),
        "csv": imp("csv", "links_wechat_douyin.csv"),
        "json": imp("json", "links.json"),
        "paste": imp("paste", "pasted_share_text.txt"),
        "vcf": imp("vcf", "contacts.vcf"),
        "contacts_csv": imp("contacts_csv", "contacts.csv"),
        "accounts_csv": imp("accounts_csv", "accounts.csv"),
    }
    print(json.dumps(res, indent=1))
    exp = {"instagram_json": 2, "facebook_json": 2, "threads_json": 1, "messenger_json": 1, "tiktok_json": 3, "whatsapp_txt": 2, "bookmarks_html": 3, "csv": 2, "json": 2, "paste": 2, "vcf": 2, "contacts_csv": 1, "accounts_csv": 2}
    for k, n in exp.items(): assert res[k]["created"] == n, (k, res[k])
    # platform detection & fields
    items = c.get("/api/items", params={"limit": 500}).json()["items"]
    by_url = {i["url"]: i for i in items}
    assert by_url["https://www.instagram.com/p/SAMPLEIMP1/"]["author"] == "sample_baker"
    assert by_url["https://www.instagram.com/p/SAMPLEIMP1/"]["saved_at"].startswith("2025") or by_url["https://www.instagram.com/p/SAMPLEIMP1/"]["saved_at"].startswith("2026")
    assert by_url["https://example.com/sample/cendol-recipe"]["author"] == "Sample Aunty"
    assert by_url["https://v.douyin.com/SAMPLECSV2/"]["platform"] == "douyin"
    assert by_url["https://mp.weixin.qq.com/s/SAMPLE_CSV1"]["platform"] == "wechat"
    assert by_url["http://xhslink.com/SAMPLEPASTE1"]["platform"] == "xiaohongshu" and "吉隆坡" in by_url["http://xhslink.com/SAMPLEPASTE1"]["title"]
    assert by_url["https://www.xiaohongshu.com/explore/SAMPLEBM1"]["platform"] == "xiaohongshu"
    # re-import = duplicates only
    assert imp("tiktok_json", "tiktok_user_data.json")["duplicates"] == 3
    # contacts searchable
    assert c.get("/api/contacts", params={"q": "王小红"}).json()["total"] == 1
    assert c.get("/api/contacts", params={"q": "three@example.com"}).json()["total"] == 1
    # accounts: password column never stored
    a = c.get("/api/accounts", params={"q": "js.threads.sample"}).json()["accounts"][0]
    assert "SHOULD_NOT_BE_STORED" not in json.dumps(a)
    assert "SHOULD_NOT_BE_STORED" not in open(os.environ["SAVEHUB_DB"], "rb").read().decode("latin-1")

def test_accounts_crud_export():
    r = c.post("/api/accounts", json={"platform": "facebook", "display_name": "FB work", "handle": "fb.work", "tags": ["work"]}).json()
    aid = r["id"]; assert r["created"]
    assert c.put(f"/api/accounts/{aid}", json={"status": "dormant"}).json()["status"] == "dormant"
    assert c.post("/api/accounts", json={"platform": "x", "handle": "a", "password": "x"}).status_code == 400
    assert "fb.work" in c.get("/api/accounts/export.csv").text
    srt = [a["display_name"].lower() for a in c.get("/api/accounts", params={"sort": "display_name"}).json()["accounts"]]
    assert srt == sorted(srt)
    assert c.delete(f"/api/accounts/{aid}").json()["ok"]

def test_accounts_bulk_paste_preview_stats():
    text = "@bulk.one\nbulk_two, Bulk Two\ntiktok @bulk.tt TT Name\nhttps://www.threads.net/@bulk.th\n# comment\n@bulk.one\nxhs 小红书号123\nwa +60 12-345 6789"
    pv = c.post("/api/accounts/preview", json={"text": text, "platform": "instagram"}).json()
    assert len(pv["accounts"]) == 7 and not pv["errors"]
    r = c.post("/api/accounts/bulk", json={"text": text, "platform": "instagram", "status": "dormant", "tags": "bulk, test"}).json()
    assert r["created"] == 6 and r["duplicates"] == 1, r
    ig = {a["handle"]: a for a in c.get("/api/accounts", params={"platform": "instagram", "q": "bulk"}).json()["accounts"]}
    assert ig["bulk.one"]["profile_url"] == "https://www.instagram.com/bulk.one/" and ig["bulk.one"]["status"] == "dormant" and ig["bulk.one"]["tags"] == ["bulk", "test"]
    assert ig["bulk_two"]["display_name"] == "Bulk Two"
    tt = c.get("/api/accounts", params={"platform": "tiktok", "q": "bulk.tt"}).json()["accounts"][0]
    assert tt["display_name"] == "TT Name" and tt["profile_url"] == "https://www.tiktok.com/@bulk.tt"
    assert c.get("/api/accounts", params={"platform": "threads", "q": "bulk.th"}).json()["total"] == 1
    assert c.get("/api/accounts", params={"platform": "xiaohongshu", "q": "小红书号123"}).json()["total"] == 1
    wa = c.get("/api/accounts", params={"platform": "whatsapp", "q": "345"}).json()["accounts"][0]
    assert wa["profile_url"] == "https://wa.me/60123456789"
    assert c.post("/api/accounts/bulk", json={"text": "@nope"}).json()["errors"]  # no platform -> error, nothing created
    assert c.post("/api/accounts/bulk", json={"text": "@a", "platform": "instagram", "password": "x"}).status_code == 400
    assert c.post("/api/accounts/bulk", json={"text": "@a", "platform": "myspace"}).status_code == 400
    st = c.get("/api/accounts/stats").json(); assert st["by_platform"]["instagram"] >= 3 and st["by_status"]["dormant"] >= 6
    assert c.get("/api/accounts", params={"status": "dormant", "q": "bulk"}).json()["total"] == 6
    for a in c.get("/api/accounts", params={"q": "bulk"}).json()["accounts"]: c.delete(f"/api/accounts/{a['id']}")
    for a in c.get("/api/accounts", params={"q": "小红书号123"}).json()["accounts"] + c.get("/api/accounts", params={"platform": "whatsapp", "q": "345"}).json()["accounts"]: c.delete(f"/api/accounts/{a['id']}")

def test_settings_sync_drive_folder(tmp_path):
    c.put("/api/settings", json={"drive": {"enabled": True, "mode": "folder", "folder_path": str(tmp_path)}})
    r = c.post("/api/sync").json()
    assert r["drive"]["ok"] and (tmp_path / "savehub_export.csv").exists() and (tmp_path / "savehub_export.md").exists()
    assert "skipped" in r["notion"]
    assert c.get("/api/x/auth", follow_redirects=False).status_code == 400  # no client id yet
    c.put("/api/settings", json={"x": {"client_id": "TEST_CLIENT"}})
    loc = c.get("/api/x/auth", follow_redirects=False).headers["location"]
    assert "code_challenge_method=S256" in loc and "bookmark.read" in loc and "client_id=TEST_CLIENT" in loc

def test_clear_samples():
    n = c.post("/api/samples/clear").json(); assert n["items"] == 30
    assert c.get("/api/items").json()["total"] > 0  # imported items remain
