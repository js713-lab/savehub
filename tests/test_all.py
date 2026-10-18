"""End-to-end tests on an isolated temp DB (the live library is untouched)."""
import os, json, tempfile, pathlib
import pytest
TEST_DB = pathlib.Path(tempfile.mkdtemp(prefix="savehub-test_all-")) / "test.db"
os.environ["SAVEHUB_DB"] = str(TEST_DB)
from fastapi.testclient import TestClient
from savehub import app as appmod, db, seed
S = pathlib.Path(__file__).resolve().parent.parent / "samples"
c = TestClient(appmod.app)


@pytest.fixture(scope="module", autouse=True)
def _own_db():
    """This module's tests share one DB and run in order. Point savehub at it explicitly for the module's duration:
    the env var above is ignored when another test module imported savehub.db first."""
    old = db.DB_PATH
    db.DB_PATH = TEST_DB
    seed.seed()
    yield
    db.DB_PATH = old

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

# ---------------- encrypted password vault ----------------
import asyncio, base64, sqlite3
from cryptography.fernet import Fernet
TEST_KEY = Fernet.generate_key().decode()   # throwaway, generated per run
DASH = ("savehub", "dash-test-pw")
PLAIN = "S3cr3t-Pässwörd-测试-!@#"
J = {"Content-Type": "application/json"}

def _vault_env(mp, key=TEST_KEY, login=True):
    mp.setenv("SAVEHUB_VAULT_KEY", key) if key else mp.delenv("SAVEHUB_VAULT_KEY", raising=False)
    mp.setenv("SAVEHUB_DASHBOARD_PASSWORD", DASH[1]) if login else mp.delenv("SAVEHUB_DASHBOARD_PASSWORD", raising=False)

def _new_acct(handle):
    return c.post("/api/accounts", json={"platform": "instagram", "handle": handle, "display_name": "Vault " + handle}).json()["id"]

def _db_bytes():
    from savehub import db
    db.conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    p = os.environ["SAVEHUB_DB"]
    return b"".join(open(f, "rb").read() for f in (p, p + "-wal") if os.path.exists(f))

def test_vault_disabled_without_key(monkeypatch):
    _vault_env(monkeypatch, key=None)
    st = c.get("/api/vault/status").json(); assert not st["enabled"] and "SAVEHUB_VAULT_KEY" in st["reason"]
    aid = _new_acct("vault.nokey")
    r = c.put(f"/api/accounts/{aid}/secret", json={"password": PLAIN}, headers=J, auth=DASH)
    assert r.status_code == 503 and "SAVEHUB_VAULT_KEY" in r.json()["detail"]
    assert c.post(f"/api/accounts/{aid}/secret/reveal", json={}, auth=DASH).status_code == 503
    from savehub import db
    assert db.conn().execute("SELECT count(*) FROM account_secrets WHERE account_id=?", (aid,)).fetchone()[0] == 0
    assert PLAIN.encode() not in _db_bytes()                       # nothing stored, plaintext nowhere
    _vault_env(monkeypatch, key="short")                            # too-short passphrase is refused too
    assert not c.get("/api/vault/status").json()["enabled"]

def test_vault_disabled_without_login(monkeypatch):
    _vault_env(monkeypatch, login=False)
    st = c.get("/api/vault/status").json(); assert not st["enabled"] and "SAVEHUB_DASHBOARD_PASSWORD" in st["reason"]
    aid = _new_acct("vault.nologin")
    assert c.put(f"/api/accounts/{aid}/secret", json={"password": PLAIN}, headers=J).status_code == 503

def test_vault_roundtrip_ciphertext_only_and_auth(monkeypatch):
    _vault_env(monkeypatch)
    st = c.get("/api/vault/status").json(); assert st["enabled"] and st["algorithm"] == "AES-256-GCM", st
    assert TEST_KEY not in json.dumps(st)
    aid, other = _new_acct("vault.one"), _new_acct("vault.two")
    url = f"/api/accounts/{aid}/secret"
    # auth required (no creds / wrong creds), JSON-only, same-origin only
    assert c.put(url, json={"password": PLAIN}).status_code == 401
    assert c.put(url, json={"password": PLAIN}, auth=("savehub", "wrong")).status_code == 401
    assert c.put(url, content=json.dumps({"password": PLAIN}), headers={"Content-Type": "text/plain"}, auth=DASH).status_code == 415
    assert c.put(url, json={"password": PLAIN}, headers={**J, "Origin": "https://evil.example.com"}, auth=DASH).status_code == 403
    assert c.put(url, json={"password": ""}, auth=DASH).status_code == 400
    r = c.put(url, json={"password": PLAIN}, auth=DASH); assert r.status_code == 200 and r.json()["has_password"]
    # reveal: auth required, returns plaintext with no-store
    assert c.post(url + "/reveal", json={}).status_code == 401
    assert c.post(url + "/reveal", json={}, auth=("savehub", "nope")).status_code == 401
    r = c.post(url + "/reveal", json={}, auth=DASH)
    assert r.status_code == 200 and r.json()["password"] == PLAIN and "no-store" in r.headers["cache-control"]
    # DB holds only ciphertext
    from savehub import db
    cipher = db.conn().execute("SELECT cipher FROM account_secrets WHERE account_id=?", (aid,)).fetchone()[0]
    assert cipher.startswith("v1:") and PLAIN not in cipher and PLAIN.encode() not in base64.b64decode(cipher[3:])
    raw = _db_bytes()
    assert PLAIN.encode() not in raw and PLAIN.encode("utf-16-le") not in raw and TEST_KEY.encode() not in raw
    cols = [r[1] for r in db.conn().execute("PRAGMA table_info(accounts)")]; assert not any("pass" in x or "secret" in x for x in cols)
    # list/export never contain secrets
    lst = c.get("/api/accounts", params={"q": "vault.one"}).json()
    assert lst["accounts"][0]["has_password"] is True and PLAIN not in json.dumps(lst) and cipher not in json.dumps(lst)
    exp = c.get("/api/accounts/export.csv").text; assert PLAIN not in exp and cipher not in exp
    # plaintext password via generic endpoints still rejected
    assert c.post("/api/accounts", json={"platform": "x", "handle": "pw", "password": PLAIN}).status_code == 400
    assert c.put(f"/api/accounts/{aid}", json={"password": PLAIN}).status_code == 400
    # two encryptions of the same value differ (random nonce); ciphertext bound to its account (AAD)
    c.put(f"/api/accounts/{other}/secret", json={"password": PLAIN}, auth=DASH)
    c2 = db.conn().execute("SELECT cipher FROM account_secrets WHERE account_id=?", (other,)).fetchone()[0]; assert c2 != cipher
    db.conn().execute("UPDATE account_secrets SET cipher=? WHERE account_id=?", (cipher, other)); db.conn().commit()
    assert c.post(f"/api/accounts/{other}/secret/reveal", json={}, auth=DASH).status_code == 409
    # wrong master key -> refused, not garbage
    _vault_env(monkeypatch, key=Fernet.generate_key().decode())
    st = c.get("/api/vault/status").json(); assert not st["enabled"] and st["key_mismatch"]
    assert c.post(url + "/reveal", json={}, auth=DASH).status_code == 409
    _vault_env(monkeypatch)
    # replace + remove + cascade on account delete
    c.put(url, json={"password": "second-value"}, auth=DASH)
    assert c.post(url + "/reveal", json={}, auth=DASH).json()["password"] == "second-value"
    assert c.request("DELETE", url, json={}, auth=DASH).json()["deleted"] == 1
    assert c.post(url + "/reveal", json={}, auth=DASH).status_code == 400
    assert c.delete(f"/api/accounts/{other}").json()["ok"]
    assert db.conn().execute("SELECT count(*) FROM account_secrets WHERE account_id=?", (other,)).fetchone()[0] == 0
    c.delete(f"/api/accounts/{aid}")

def test_vault_passphrase_key(monkeypatch):
    _vault_env(monkeypatch)            # same DB keeps one key-check; a different key must be refused
    _vault_env(monkeypatch, key="a long passphrase that is not a fernet key")
    assert c.get("/api/vault/status").json()["key_mismatch"]
    from savehub import db
    db.conn().execute("DELETE FROM account_secrets"); db.conn().execute("DELETE FROM vault_meta"); db.conn().commit()   # fresh vault
    assert c.get("/api/vault/status").json()["enabled"]          # PBKDF2-HMAC-SHA256 (600k) path
    aid = _new_acct("vault.pp")
    assert c.put(f"/api/accounts/{aid}/secret", json={"password": PLAIN}, auth=DASH).status_code == 200
    assert c.post(f"/api/accounts/{aid}/secret/reveal", json={}, auth=DASH).json()["password"] == PLAIN
    assert PLAIN.encode() not in _db_bytes()
    c.delete(f"/api/accounts/{aid}")
    db.conn().execute("DELETE FROM vault_meta"); db.conn().commit()

def test_mcp_never_exposes_secrets(monkeypatch):
    _vault_env(monkeypatch)
    from savehub import mcp_server
    aid = _new_acct("vault.mcp")
    assert c.put(f"/api/accounts/{aid}/secret", json={"password": PLAIN}, auth=DASH).status_code == 200
    from savehub import db
    cipher = db.conn().execute("SELECT cipher FROM account_secrets WHERE account_id=?", (aid,)).fetchone()[0]
    out = json.dumps([mcp_server.list_accounts(), mcp_server.search_accounts("vault.mcp"), mcp_server.add_account("x", handle="vault.mcp2")], ensure_ascii=False)
    assert PLAIN not in out and cipher not in out and "has_password" not in out
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert len(tools) == 19
    for t in tools:
        assert "password" not in json.dumps(t.inputSchema).lower() and "secret" not in t.name.lower() and "vault" not in t.name.lower(), t.name
    c.delete(f"/api/accounts/{aid}")

def test_settings_sync_drive_folder(tmp_path):
    c.put("/api/settings", json={"drive": {"enabled": True, "mode": "folder", "folder_path": str(tmp_path)}})
    r = c.post("/api/sync").json()
    assert r["drive"]["ok"] and (tmp_path / "savehub_export.csv").exists() and (tmp_path / "savehub_export.md").exists()
    assert "skipped" in r["notion"]
    assert c.get("/api/x/auth", follow_redirects=False).status_code == 400  # no client id yet
    c.put("/api/settings", json={"x": {"client_id": "TEST_CLIENT"}})
    loc = c.get("/api/x/auth", follow_redirects=False).headers["location"]
    assert "code_challenge_method=S256" in loc and "bookmark.read" in loc and "client_id=TEST_CLIENT" in loc

def test_collected_contacts_are_mirrored_into_contacts():
    """Messenger/WhatsApp collector items (extra.kind contact/group/chat) also show up in /api/contacts, linked to the account."""
    aid = c.post("/api/accounts", json={"platform": "whatsapp", "handle": "@mirror", "display_name": "@mirror"}).json()["id"]
    base = {"platform": "whatsapp", "source": "collector:whatsapp:mirror", "account_id": aid}
    r = c.post("/api/items", json={**base, "url": "https://wa.me/60199990001", "title": "Mirror Person",
                                   "raw": {"kind": "contact", "phone": "+60199990001", "last_message": "hi"}}).json()
    assert r["created"]
    c.post("/api/items", json={**base, "url": "https://web.whatsapp.com/#group/1@g.us", "title": "Mirror Group", "raw": {"kind": "group"}})
    c.post("/api/items", json={**base, "url": "https://web.whatsapp.com/#starred/x", "title": "starred text", "raw": {"kind": "starred_message"}})
    c.post("/api/items", json={"platform": "whatsapp", "url": "https://wa.me/60199990009", "title": "Manual", "raw": {"kind": "contact"}})  # manual add
    cs = c.get("/api/contacts", params={"q": "Mirror"}).json()["contacts"]
    assert sorted(x["name"] for x in cs) == ["Mirror Group", "Mirror Person"] and all(x["account_id"] == aid for x in cs)
    p = next(x for x in cs if x["name"] == "Mirror Person")
    assert p["phones"] == ["+60199990001"] and p["source"] == "collector:whatsapp:mirror" and "last message: hi" in p["notes"]
    assert c.get("/api/contacts", params={"q": "Manual"}).json()["total"] == 0 and c.get("/api/contacts", params={"q": "starred"}).json()["total"] == 0
    # the same person collected again (or by a second account) is not duplicated
    c.post("/api/items", json={**base, "url": "https://wa.me/60199990001", "title": "Mirror Person", "raw": {"kind": "contact"}})
    from savehub import db
    db.mirror_collected_contact({**base, "url": "https://wa.me/60199990001", "title": "Mirror Person", "raw": {"kind": "contact"}})
    assert c.get("/api/contacts", params={"q": "Mirror Person"}).json()["total"] == 1

def test_clear_samples():
    n = c.post("/api/samples/clear").json(); assert n["items"] == 30
    assert c.get("/api/items").json()["total"] > 0  # imported items remain
