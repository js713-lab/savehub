"""SQLite storage with FTS5 (trigram tokenizer => works for English and Chinese substrings)."""
import json, os, sqlite3, threading, time
from . import media
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("SAVEHUB_DB", ROOT / "data" / "savehub.db"))

AUTO = " Add the account on the Accounts page (username + password go into the encrypted vault, SaveHub signs in headless and auto-sync keeps it current; codes / QR are asked for on the card). Importing a data export is optional."
PLATFORMS = {
    "x":           {"name": "X (Twitter)", "method": "live_api", "detail": "Automatic: on-box collector reads your Bookmarks (or the official X API v2 with your own developer app)." + AUTO},
    "threads":     {"name": "Threads", "method": "collector", "detail": "Automatic: on-box collector reads threads.com/saved." + AUTO},
    "instagram":   {"name": "Instagram", "method": "collector", "detail": "Automatic: on-box collector reads Saved (all posts + every collection)." + AUTO},
    "facebook":    {"name": "Facebook", "method": "collector", "detail": "Automatic: on-box collector reads facebook.com/saved (posts, links, videos)." + AUTO},
    "messenger":   {"name": "Messenger", "method": "collector", "detail": "Automatic: on-box collector reads your chat list as contacts (same login as Facebook)." + AUTO},
    "whatsapp":    {"name": "WhatsApp", "method": "collector", "detail": "Automatic: WhatsApp Web contacts, groups and starred messages. Signs in by QR scan only (WhatsApp has no password login): the QR code is shown on the account card, scan it once with your phone; auto-sync does the rest. Chat-export import is optional."},
    "wechat":      {"name": "WeChat 微信", "method": "collector", "detail": "Automatic: the official web File Transfer Assistant (文件传输助手, filehelper.weixin.qq.com). Scan the QR code on the account card once; every link, article, note and file you send or forward to 文件传输助手 is collected by auto-sync. Favourites 收藏 have no web/API anywhere: forward them to File Transfer. Contacts are not reachable from the web for most accounts. Pasting links is optional."},
    "tiktok":      {"name": "TikTok", "method": "collector", "detail": "Automatic: on-box collector reads Favorites (collections)." + AUTO},
    "douyin":      {"name": "抖音 Douyin", "method": "collector", "detail": "Automatic: on-box collector reads 收藏 (password or QR sign-in; a QR code is shown on the card if Douyin asks)." + AUTO},
    "xiaohongshu": {"name": "小红书 RED", "method": "collector", "detail": "Automatic: on-box collector reads 收藏 notes." + AUTO},
    "icloud":      {"name": "iCloud", "method": "collector", "detail": "Automatic: iCloud Drive files + iCloud Contacts, read-only (pyicloud). Add the Apple ID + password on the Accounts page — the password is never stored in plaintext (encrypted vault only); enter the 2FA code on the card when Apple asks. Only the encrypted trusted session is kept; auto-sync picks up new and changed files."},
    "web":         {"name": "Web / Bookmarks", "method": "import", "detail": "Add a link manually, paste links, or import browser bookmarks HTML (Chrome/Firefox/Safari export)."},
}

DEFAULT_SETTINGS = {
    "enabled_platforms": list(PLATFORMS.keys()),
    "ai": {"provider": "auto", "model": "", "base_url": ""},  # auto|openai|xai|local
    "notion": {"enabled": False, "token": "", "database_id": ""},
    "drive": {"enabled": False, "mode": "folder", "folder_path": str(ROOT / "data" / "export"), "service_account_file": "", "folder_id": ""},
    "x": {"client_id": "", "client_secret": "", "redirect_uri": (os.environ.get("SAVEHUB_PUBLIC_URL") or (f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}" if os.environ.get("RENDER_EXTERNAL_HOSTNAME") else "http://localhost:8765")).rstrip("/") + "/api/x/callback"},
}

_local = threading.local()

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def conn():
    c = getattr(_local, "c", None)
    if c is not None and getattr(_local, "path", None) != str(DB_PATH):
        # DB_PATH changed since this thread connected (tests, tooling): never keep writing to the previous file
        try: c.close()
        except Exception: pass
        c = _local.c = None
    if c is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        old = os.umask(0o077)                    # a new DB (+ -wal/-shm) is created owner-only
        try: c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        finally: os.umask(old)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.c = c
        _local.path = str(DB_PATH)
        init(c)
    return c

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY, platform TEXT NOT NULL, display_name TEXT, handle TEXT, profile_url TEXT,
  linked_email TEXT, linked_phone TEXT, notes TEXT, tags TEXT DEFAULT '[]', status TEXT DEFAULT 'active',
  connected_via TEXT DEFAULT 'manual', created_at TEXT, is_sample INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, platform TEXT NOT NULL, account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  url TEXT, title TEXT, content TEXT, author TEXT, author_handle TEXT, media_url TEXT,
  saved_at TEXT, created_at TEXT, tags TEXT DEFAULT '[]', summary TEXT, tagger TEXT,
  source TEXT, external_id TEXT, notion_page_id TEXT, is_sample INTEGER DEFAULT 0, raw TEXT,
  type TEXT, thumbnail_url TEXT, images TEXT DEFAULT '[]', video_url TEXT, embed_url TEXT, duration INTEGER, aspect REAL,
  author_avatar_url TEXT,
  UNIQUE(platform, url));
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(title, content, author, tags, summary, content='items', content_rowid='id', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(rowid,title,content,author,tags,summary) VALUES(new.id,new.title,new.content,coalesce(new.author,'')||' '||coalesce(new.author_handle,''),new.tags,new.summary); END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
  INSERT INTO items_fts(items_fts,rowid,title,content,author,tags,summary) VALUES('delete',old.id,old.title,old.content,coalesce(old.author,'')||' '||coalesce(old.author_handle,''),old.tags,old.summary); END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
  INSERT INTO items_fts(items_fts,rowid,title,content,author,tags,summary) VALUES('delete',old.id,old.title,old.content,coalesce(old.author,'')||' '||coalesce(old.author_handle,''),old.tags,old.summary);
  INSERT INTO items_fts(rowid,title,content,author,tags,summary) VALUES(new.id,new.title,new.content,coalesce(new.author,'')||' '||coalesce(new.author_handle,''),new.tags,new.summary); END;
CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY, name TEXT, phones TEXT DEFAULT '[]', emails TEXT DEFAULT '[]', org TEXT,
  platform TEXT, account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL, notes TEXT,
  created_at TEXT, is_sample INTEGER DEFAULT 0, source TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS account_secrets(account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE, cipher TEXT NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS vault_meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS sync_state(
  platform TEXT NOT NULL, account TEXT NOT NULL, status TEXT, running INTEGER DEFAULT 0, last_run REAL, last_success REAL,
  last_added INTEGER DEFAULT 0, total_added INTEGER DEFAULT 0, last_collected INTEGER DEFAULT 0, last_error TEXT,
  consecutive_errors INTEGER DEFAULT 0, next_run REAL, session_mtime REAL, last_reason TEXT, updated_at REAL,
  PRIMARY KEY(platform, account));
CREATE TABLE IF NOT EXISTS autosync_meta(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS idx_items_saved ON items(saved_at);
CREATE INDEX IF NOT EXISTS idx_items_platform ON items(platform);
"""

def harden_files():
    """Owner-only permissions for the DB (+ -wal/-shm) and SaveHub's key/token files next to it (permissions only)."""
    d = DB_PATH.parent; fixed = 0
    for f in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm"), d / ".mcp_token", d / ".vault_key"):
        try:
            if f.is_file() and not f.is_symlink() and f.stat().st_mode & 0o077: os.chmod(f, 0o600); fixed += 1
        except OSError: pass
    return fixed

# media columns added in schema v2 (see savehub/media.py); older DBs get them via ALTER TABLE + a one-time backfill
MEDIA_COLUMNS = [("type", "TEXT"), ("thumbnail_url", "TEXT"), ("images", "TEXT DEFAULT '[]'"), ("video_url", "TEXT"),
                 ("embed_url", "TEXT"), ("duration", "INTEGER"), ("aspect", "REAL"), ("author_avatar_url", "TEXT")]
SCHEMA_VERSION = 2

def init(c):
    c.executescript(SCHEMA)
    migrate(c)
    c.commit()

def migrate(c):
    """Idempotent: add missing media columns, then (once per DB, tracked in PRAGMA user_version) derive the media
    fields of existing rows from their legacy media_url + raw flags."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(items)")}
    for col, typ in MEDIA_COLUMNS:
        if col not in cols:
            c.execute(f"ALTER TABLE items ADD COLUMN {col} {typ}")  # nosec B608 - fixed column list above
    c.execute("CREATE INDEX IF NOT EXISTS idx_items_type ON items(type)")
    if c.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION: return 0
    n = 0
    for r in c.execute("SELECT id, platform, url, media_url, raw, type, thumbnail_url, images, video_url, duration, aspect, author_avatar_url FROM items WHERE type IS NULL").fetchall():
        m = media.for_db(dict(r))
        c.execute(f"UPDATE items SET {','.join(k + '=?' for k in m)} WHERE id=?", list(m.values()) + [r["id"]])  # nosec B608 - keys are media.FIELDS
        n += 1
    c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return n

def rows(cur):
    out = []
    for r in cur.fetchall():
        d = dict(r)
        for k in ("tags", "phones", "emails", "images"):
            if k in d and isinstance(d[k], str):
                try: d[k] = json.loads(d[k])
                except Exception: d[k] = []
            elif k == "images" and k in d and d[k] is None: d[k] = []
        raw = d.pop("raw", None)
        if "platform" in d and "url" in d and "title" in d and "saved_at" in d:   # an item row: expose a few safe raw fields
            try: rj = json.loads(raw) if isinstance(raw, str) else {}
            except ValueError: rj = {}
            rj = rj if isinstance(rj, dict) else {}
            cols = rj.get("collections")
            d["collections"] = [str(x) for x in cols if isinstance(x, (str, int))][:20] if isinstance(cols, list) else []
            d["posted_at"] = rj.get("posted_at") if isinstance(rj.get("posted_at"), str) else None
        out.append(d)
    return out

# ---------------- settings ----------------
def get_settings():
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    for r in conn().execute("SELECT key,value FROM settings"):
        v = json.loads(r["value"])
        if isinstance(s.get(r["key"]), dict) and isinstance(v, dict): s[r["key"]].update(v)
        else: s[r["key"]] = v
    # env overrides
    if os.environ.get("X_CLIENT_ID") and not s["x"]["client_id"]: s["x"]["client_id"] = os.environ["X_CLIENT_ID"]
    if os.environ.get("NOTION_TOKEN") and not s["notion"]["token"]: s["notion"]["token"] = os.environ["NOTION_TOKEN"]
    if os.environ.get("NOTION_DATABASE_ID") and not s["notion"]["database_id"]: s["notion"]["database_id"] = os.environ["NOTION_DATABASE_ID"]
    if os.environ.get("SAVEHUB_ENABLED_PLATFORMS"):
        s["enabled_platforms"] = [p.strip() for p in os.environ["SAVEHUB_ENABLED_PLATFORMS"].split(",") if p.strip() in PLATFORMS]
    return s

def set_setting(key, value):
    c = conn(); c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value))); c.commit()

# ---------------- items ----------------
ITEM_FIELDS = ["platform","account_id","url","title","content","author","author_handle","media_url","saved_at","tags","summary","tagger","source","external_id","is_sample","raw",
               *media.FIELDS]
SORTS = {"saved_at":"i.saved_at","platform":"i.platform","author":"lower(i.author)","title":"lower(i.title)","created_at":"i.created_at"}

def add_item(d, tag=True):
    """Insert (or ignore duplicate platform+url). Returns (id, created)."""
    from . import tagger
    d = dict(d); d.setdefault("platform", "web"); d.setdefault("saved_at", now_iso())
    if d["platform"] not in PLATFORMS: d["platform"] = "web"
    if tag and not d.get("tags"):
        t = tagger.tag_item(d); d["tags"], d["summary"], d["tagger"] = t["tags"], t["summary"], t["tagger"]
    d["tags"] = json.dumps(d.get("tags") or [], ensure_ascii=False)
    d.update(media.for_db(d))                    # one media schema for every source (collector, importer, REST, MCP)
    if isinstance(d.get("raw"), (dict, list)): d["raw"] = json.dumps(d["raw"], ensure_ascii=False)[:20000]
    c = conn()
    if d.get("url"):
        ex = c.execute("SELECT id FROM items WHERE platform=? AND url=?", (d["platform"], d["url"])).fetchone()
        if ex: return ex["id"], False
    cols = [f for f in ITEM_FIELDS if f in d]
    cur = c.execute(f"INSERT INTO items({','.join(cols)},created_at) VALUES({','.join('?'*len(cols))},?)", [d[f] for f in cols] + [now_iso()])  # nosec B608 - identifiers come from fixed allow-lists (ITEM/CONTACT/ACCOUNT_FIELDS, sort maps); values are bound
    c.commit()
    mirror_collected_contact(d)
    return cur.lastrowid, True

COLLECTED_CONTACT_KINDS = ("contact", "group", "chat")

def mirror_collected_contact(d, update=False):
    """Contacts collectors (Messenger, WhatsApp) store each person/group as an item (extra.kind=contact|group|chat).
    Mirror it into the contacts table too, so the Contacts page, /api/contacts and MCP search_contacts find it, linked
    to the same account. De-duplicated per platform + name + item URL (two people with the same name stay separate)."""
    src = str(d.get("source") or "")
    if not src.startswith("collector:"): return None
    raw = d.get("raw")
    if isinstance(raw, str):
        try: raw = json.loads(raw)
        except ValueError: raw = {}
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("kind") not in COLLECTED_CONTACT_KINDS or not d.get("title"): return None
    url = d.get("url") or ""
    notes = "\n".join(x for x in (url, "group chat" if raw.get("kind") == "group" else "",
                                    f"last message: {raw['last_message']}" if raw.get("last_message") else "") if x)
    c = conn()
    def _strs(v): return [str(x) for x in v if x] if isinstance(v, list) else []
    phones = _strs(raw.get("phones")) or ([raw["phone"]] if raw.get("phone") else [])
    emails, org = _strs(raw.get("emails")), str(raw.get("org") or "")      # iCloud Contacts carry these too
    if update and url:
        # a changed contact (new version of the same item URL): refresh its row instead of adding a second one
        ex = c.execute("SELECT id FROM contacts WHERE platform=? AND source LIKE 'collector:%' AND (notes=? OR notes LIKE ?) ORDER BY id LIMIT 1",
                       (d["platform"], url, url + "\n%")).fetchone()
        if ex:
            c.execute("UPDATE contacts SET name=?, phones=?, emails=?, org=?, notes=? WHERE id=?",
                      (d["title"], json.dumps(phones), json.dumps(emails), org, notes, ex["id"])); c.commit()
            return ex["id"]
    ex = c.execute("SELECT id FROM contacts WHERE platform=? AND name=? AND (notes=? OR notes LIKE ?)",
                   (d["platform"], d["title"], url, url + "\n%")).fetchone()
    if ex: return ex["id"]
    cur = c.execute("INSERT INTO contacts(name,phones,emails,org,platform,account_id,notes,is_sample,source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (d["title"], json.dumps(phones), json.dumps(emails), org, d["platform"], d.get("account_id"), notes, 0, src, now_iso()))
    c.commit(); return cur.lastrowid

def update_item(item_id, d):
    d = {k: v for k, v in d.items() if k in ITEM_FIELDS}
    if "tags" in d: d["tags"] = json.dumps(d["tags"], ensure_ascii=False)
    if isinstance(d.get("raw"), (dict, list)): d["raw"] = json.dumps(d["raw"], ensure_ascii=False)[:20000]
    if not d: return
    if any(k in d for k in (*media.FIELDS, "media_url", "raw")):   # re-derive the media fields from the merged row
        cur = conn().execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if cur:
            merged = {**dict(cur), "images": json.loads(cur["images"] or "[]") if isinstance(cur["images"], str) else [], **d}
            if "media_url" in d and "thumbnail_url" not in d: merged["thumbnail_url"] = None
            d.update(media.for_db(merged))
    if isinstance(d.get("raw"), (dict, list)): d["raw"] = json.dumps(d["raw"], ensure_ascii=False)[:20000]
    c = conn(); c.execute(f"UPDATE items SET {','.join(k+'=?' for k in d)} WHERE id=?", list(d.values()) + [item_id]); c.commit()  # nosec B608 - identifiers come from fixed allow-lists (ITEM/CONTACT/ACCOUNT_FIELDS, sort maps); values are bound
    if "raw" in d or "title" in d:
        r = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if r: mirror_collected_contact(dict(r), update=True)

def merge_item_raw(item_id, patch: dict):
    """Merge keys into an item's raw JSON (e.g. the outcome of an un-save attempt). Never touches anything else."""
    c = conn(); r = c.execute("SELECT raw FROM items WHERE id=?", (item_id,)).fetchone()
    if not r: return False
    try: raw = json.loads(r["raw"] or "{}")
    except ValueError: raw = {}
    if not isinstance(raw, dict): raw = {}
    raw.update(patch)
    c.execute("UPDATE items SET raw=? WHERE id=?", (json.dumps(raw, ensure_ascii=False)[:20000], item_id)); c.commit()
    return True

def get_item_raw(item_id) -> dict:
    r = conn().execute("SELECT raw FROM items WHERE id=?", (item_id,)).fetchone()
    try: return json.loads(r["raw"] or "{}") if r else {}
    except ValueError: return {}

def get_item(item_id):
    r = rows(conn().execute("SELECT i.*, a.display_name AS account_name, a.handle AS account_handle, a.platform AS account_platform FROM items i LEFT JOIN accounts a ON a.id=i.account_id WHERE i.id=?", (item_id,)))
    return r[0] if r else None

def delete_item(item_id):
    c = conn(); c.execute("DELETE FROM items WHERE id=?", (item_id,)); c.commit()

def _fts_query(q):
    # quote each term; trigram needs >=3 chars, shorter terms fall back to LIKE
    terms = [t for t in q.replace('"', ' ').split() if t]
    long_t = [t for t in terms if len(t) >= 3]
    short_t = [t for t in terms if len(t) < 3]
    return " AND ".join(f'"{t}"' for t in long_t), short_t

def list_items(q="", platforms=None, tags=None, account_id=None, sort="saved_at", order="desc",
               limit=50, offset=0, enabled_only=None, since=None, include_samples=True, types=None):
    where, args = [], []
    types = [t for t in (types or []) if t in media.TYPES]
    if types: where.append(f"coalesce(i.type,'post') IN ({','.join('?'*len(types))})"); args += types
    join = ""
    if q:
        fq, short = _fts_query(q)
        if fq:
            join = "JOIN items_fts f ON f.rowid=i.id"; where.append("items_fts MATCH ?"); args.append(fq)
        for t in short:
            where.append("(i.title LIKE ? OR i.content LIKE ? OR i.author LIKE ? OR i.tags LIKE ? OR i.summary LIKE ?)"); args += [f"%{t}%"]*5
    if platforms: where.append(f"i.platform IN ({','.join('?'*len(platforms))})"); args += list(platforms)
    if enabled_only is not None: where.append(f"i.platform IN ({','.join('?'*len(enabled_only)) or 'NULL'})"); args += list(enabled_only)
    for t in (tags or []): where.append("EXISTS (SELECT 1 FROM json_each(i.tags) WHERE lower(value)=lower(?))"); args.append(t)
    if account_id not in (None, ""): where.append("i.account_id=?"); args.append(int(account_id))
    if since: where.append("i.saved_at>=?"); args.append(since)
    if not include_samples: where.append("i.is_sample=0")
    w = ("WHERE " + " AND ".join(where)) if where else ""
    if sort == "relevance" and join: ob = "bm25(items_fts)"
    else: ob = SORTS.get(sort, "i.saved_at") + (" ASC" if str(order).lower() == "asc" else " DESC")
    c = conn()
    total = c.execute(f"SELECT count(*) FROM items i {join} {w}", args).fetchone()[0]  # nosec B608 - identifiers come from fixed allow-lists (ITEM/CONTACT/ACCOUNT_FIELDS, sort maps); values are bound
    res = rows(c.execute(f"SELECT i.*, a.display_name AS account_name, a.handle AS account_handle, a.platform AS account_platform FROM items i {join} LEFT JOIN accounts a ON a.id=i.account_id {w} ORDER BY {ob}, i.id DESC LIMIT ? OFFSET ?", args + [int(limit), int(offset)]))  # nosec B608 - identifiers come from fixed allow-lists (ITEM/CONTACT/ACCOUNT_FIELDS, sort maps); values are bound
    return {"total": total, "items": res}

def all_tags(limit=100):
    return [dict(r) for r in conn().execute("SELECT lower(value) AS tag, count(*) AS n FROM items, json_each(items.tags) GROUP BY lower(value) ORDER BY n DESC LIMIT ?", (limit,))]

def clear_samples():
    c = conn()
    n = {t: c.execute(f"DELETE FROM {t} WHERE is_sample=1").rowcount for t in ("items", "contacts", "accounts")}  # nosec B608 - identifiers come from fixed allow-lists (ITEM/CONTACT/ACCOUNT_FIELDS, sort maps); values are bound
    c.commit(); return n

def stats():
    c = conn()
    return {"items": c.execute("SELECT count(*) FROM items").fetchone()[0],
            "contacts": c.execute("SELECT count(*) FROM contacts").fetchone()[0],
            "accounts": c.execute("SELECT count(*) FROM accounts").fetchone()[0],
            "samples": c.execute("SELECT (SELECT count(*) FROM items WHERE is_sample=1)+(SELECT count(*) FROM contacts WHERE is_sample=1)+(SELECT count(*) FROM accounts WHERE is_sample=1)").fetchone()[0],
            "by_platform": {r[0]: r[1] for r in c.execute("SELECT platform, count(*) FROM items GROUP BY platform")}}

# ---------------- contacts ----------------
CONTACT_FIELDS = ["name","phones","emails","org","platform","account_id","notes","is_sample","source"]
def add_contact(d):
    d = {k: v for k, v in d.items() if k in CONTACT_FIELDS}
    for k in ("phones", "emails"):
        v = d.get(k) or []
        if isinstance(v, str): v = [x.strip() for x in v.replace(";", ",").split(",") if x.strip()]
        d[k] = json.dumps(v, ensure_ascii=False)
    c = conn()
    ex = c.execute("SELECT id FROM contacts WHERE name=? AND phones=? AND emails=?", (d.get("name"), d["phones"], d["emails"])).fetchone()
    if ex: return ex["id"], False
    cols = list(d)
    cur = c.execute(f"INSERT INTO contacts({','.join(cols)},created_at) VALUES({','.join('?'*len(cols))},?)", list(d.values()) + [now_iso()])  # nosec B608 - identifiers come from fixed allow-lists (ITEM/CONTACT/ACCOUNT_FIELDS, sort maps); values are bound
    c.commit(); return cur.lastrowid, True

def search_contacts(q="", account_id=None, platform=None, sort="name", order="asc", limit=100, offset=0):
    where, args = [], []
    if q:
        for t in q.split():
            where.append("(c.name LIKE ? OR c.phones LIKE ? OR c.emails LIKE ? OR c.org LIKE ? OR c.notes LIKE ?)"); args += [f"%{t}%"]*5
    if account_id not in (None, ""): where.append("c.account_id=?"); args.append(int(account_id))
    if platform: where.append("c.platform=?"); args.append(platform)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    col = {"name": "lower(c.name)", "org": "lower(c.org)", "created_at": "c.created_at", "platform": "c.platform"}.get(sort, "lower(c.name)")
    o = "DESC" if str(order).lower() == "desc" else "ASC"
    cc = conn()
    total = cc.execute(f"SELECT count(*) FROM contacts c {w}", args).fetchone()[0]  # nosec B608 - identifiers come from fixed allow-lists (ITEM/CONTACT/ACCOUNT_FIELDS, sort maps); values are bound
    return {"total": total, "contacts": rows(cc.execute(f"SELECT c.*, a.display_name AS account_name FROM contacts c LEFT JOIN accounts a ON a.id=c.account_id {w} ORDER BY {col} {o} LIMIT ? OFFSET ?", args + [limit, offset]))}  # nosec B608 - identifiers come from fixed allow-lists (ITEM/CONTACT/ACCOUNT_FIELDS, sort maps); values are bound

def delete_contact(cid):
    c = conn(); c.execute("DELETE FROM contacts WHERE id=?", (cid,)); c.commit()

# ---------------- accounts (no passwords stored, by design) ----------------
ACCOUNT_FIELDS = ["platform","display_name","handle","profile_url","linked_email","linked_phone","notes","tags","status","connected_via","created_at","is_sample"]
def add_account(d):
    d = {k: v for k, v in d.items() if k in ACCOUNT_FIELDS}
    t = d.get("tags") or []
    if isinstance(t, str): t = [x.strip() for x in t.replace(";", ",").split(",") if x.strip()]
    d["tags"] = json.dumps(t, ensure_ascii=False)
    d.setdefault("created_at", now_iso()); d.setdefault("platform", "web")
    c = conn()
    if d.get("handle"):
        ex = find_account(d["platform"], d["handle"])
        if ex: return ex, False
    cols = list(d)
    cur = c.execute(f"INSERT INTO accounts({','.join(cols)}) VALUES({','.join('?'*len(cols))})", list(d.values()))  # nosec B608 - identifiers come from fixed allow-lists (ITEM/CONTACT/ACCOUNT_FIELDS, sort maps); values are bound
    c.commit(); return cur.lastrowid, True

def norm_handle(h) -> str:
    """'@Js_Main ' -> 'js_main' (how collectors, session files and sync_state name an account)."""
    return str(h or "").strip().lstrip("@").strip().lower()

def find_account(platform, handle):
    """Id of the account row for this platform + handle ('@x' == 'x', case-insensitive), or None."""
    h = norm_handle(handle)
    if not h: return None
    r = conn().execute("SELECT id FROM accounts WHERE platform=? AND ltrim(lower(trim(handle)),'@')=? ORDER BY id LIMIT 1", (platform, h)).fetchone()
    return r["id"] if r else None

def update_account(aid, d):
    d = {k: v for k, v in d.items() if k in ACCOUNT_FIELDS}
    if "tags" in d:
        t = d["tags"]
        if isinstance(t, str): t = [x.strip() for x in t.split(",") if x.strip()]
        d["tags"] = json.dumps(t, ensure_ascii=False)
    if not d: return
    c = conn(); c.execute(f"UPDATE accounts SET {','.join(k+'=?' for k in d)} WHERE id=?", list(d.values()) + [aid]); c.commit()  # nosec B608 - identifiers come from fixed allow-lists (ITEM/CONTACT/ACCOUNT_FIELDS, sort maps); values are bound

def delete_account(aid):
    c = conn(); c.execute("DELETE FROM accounts WHERE id=?", (aid,)); c.commit()

def get_account(aid):
    r = rows(conn().execute("SELECT * FROM accounts WHERE id=?", (aid,))); return r[0] if r else None

def list_accounts(q="", platform=None, status=None, sort="platform", order="asc", limit=200, offset=0):
    where, args = [], []
    if q:
        for t in q.split():
            where.append("(display_name LIKE ? OR handle LIKE ? OR profile_url LIKE ? OR linked_email LIKE ? OR linked_phone LIKE ? OR notes LIKE ? OR tags LIKE ?)"); args += [f"%{t}%"]*7
    if platform:
        ps = platform if isinstance(platform, list) else [platform]
        where.append(f"a.platform IN ({','.join('?'*len(ps))})"); args += ps
    if status: where.append("status=?"); args.append(status)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    col = {"platform": "platform, lower(display_name)", "display_name": "lower(display_name)", "handle": "lower(handle)", "created_at": "created_at", "status": "status"}.get(sort, "platform")
    o = "DESC" if str(order).lower() == "desc" else "ASC"
    c = conn()
    total = c.execute(f"SELECT count(*) FROM accounts a {w}", args).fetchone()[0]  # nosec B608 - identifiers come from fixed allow-lists (ITEM/CONTACT/ACCOUNT_FIELDS, sort maps); values are bound
    res = rows(c.execute(f"SELECT a.*, (SELECT count(*) FROM items i WHERE i.account_id=a.id) AS item_count, (SELECT count(*) FROM contacts k WHERE k.account_id=a.id) AS contact_count FROM accounts a {w} ORDER BY {col} {o} LIMIT ? OFFSET ?", args + [limit, offset]))  # nosec B608 - identifiers come from fixed allow-lists (ITEM/CONTACT/ACCOUNT_FIELDS, sort maps); values are bound
    return {"total": total, "accounts": res}

# ---------------- un-save setting (manual collects only; auto-sync never un-saves) ----------------
UNSAVE_KEY = "unsave"

def get_unsave_settings() -> dict:
    """{"platforms": {platform: bool}, "accounts": {"platform@handle": bool}}. Missing = off."""
    r = conn().execute("SELECT value FROM settings WHERE key=?", (UNSAVE_KEY,)).fetchone()
    try: v = json.loads(r["value"]) if r else {}
    except ValueError: v = {}
    v = v if isinstance(v, dict) else {}
    return {"platforms": {k: bool(x) for k, x in (v.get("platforms") or {}).items()},
            "accounts": {k: bool(x) for k, x in (v.get("accounts") or {}).items()}}

def unsave_enabled(platform, account="") -> bool:
    """Per-account setting wins over the per-platform one; default OFF."""
    s = get_unsave_settings(); h = norm_handle(account)
    if h and h != "default" and f"{platform}@{h}" in s["accounts"]: return s["accounts"][f"{platform}@{h}"]
    return bool(s["platforms"].get(platform, False))

def set_unsave(platform, account, enabled):
    """enabled: True / False, or None to drop the per-account override (falls back to the platform setting)."""
    s = get_unsave_settings(); h = norm_handle(account)
    if h and h != "default":
        if enabled is None: s["accounts"].pop(f"{platform}@{h}", None)
        else: s["accounts"][f"{platform}@{h}"] = bool(enabled)
    elif enabled is None: s["platforms"].pop(platform, None)
    else: s["platforms"][platform] = bool(enabled)
    set_setting(UNSAVE_KEY, s)
    return s
