"""Importers for official data-export files and generic link lists."""
import csv, io, json, re
from datetime import datetime, timezone
from html.parser import HTMLParser
from . import db

URL_RE = re.compile(r"https?://[^\s<>\"'）)】，。]+")
DOMAINS = [("x.com","x"),("twitter.com","x"),("t.co","x"),("threads.net","threads"),("threads.com","threads"),("instagram.com","instagram"),
           ("facebook.com","facebook"),("fb.watch","facebook"),("fb.com","facebook"),("m.me","messenger"),("messenger.com","messenger"),
           ("wa.me","whatsapp"),("whatsapp.com","whatsapp"),("weixin.qq.com","wechat"),("mp.weixin","wechat"),("tiktok.com","tiktok"),
           ("douyin.com","douyin"),("iesdouyin.com","douyin"),("xiaohongshu.com","xiaohongshu"),("xhslink.com","xiaohongshu")]

def detect_platform(url, default="web"):
    u = (url or "").lower()
    for d, p in DOMAINS:
        if re.search(r"(^|[/.])" + re.escape(d), u.split("?")[0][:80]): return p
    return default

def to_iso(v):
    if v in (None, ""): return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            v = float(v); v = v / 1000 if v > 1e11 else v
            return datetime.fromtimestamp(v, timezone.utc).replace(microsecond=0).isoformat()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
            try: return datetime.strptime(v.strip()[:19], fmt).replace(tzinfo=timezone.utc).isoformat()
            except ValueError: pass
        return datetime.fromisoformat(v.replace("Z", "+00:00")).isoformat()
    except Exception:
        return None

def _fix_mojibake(s):
    # Meta exports encode UTF-8 as latin-1 escaped (\u00e5\u00b0...) — repair it
    if isinstance(s, str):
        try: return s.encode("latin-1").decode("utf-8")
        except Exception: return s
    return s

def _add(items, platform, account_id, source, sample=False):
    created = dup = 0
    for it in items:
        if not it.get("url") and not it.get("content"): continue
        it.setdefault("platform", detect_platform(it.get("url"), platform) if platform in (None, "auto", "web") else platform)
        if it["platform"] in (None, "auto"): it["platform"] = "web"
        it["account_id"] = account_id or None; it["source"] = source; it["is_sample"] = 1 if sample else 0
        _, c = db.add_item(it)
        created += c; dup += (not c)
    return {"created": created, "duplicates": dup, "parsed": len(items)}

# ---------- Meta (Instagram / Facebook / Threads / Messenger) & TikTok JSON: generic walker ----------
URL_KEYS = ("href", "url", "uri", "link", "Link", "VideoLink", "share_url")
TIME_KEYS = ("timestamp", "Date", "date", "timestamp_ms", "creation_timestamp", "saved_at", "Saved on")
TITLE_KEYS = ("title", "name", "Title", "text", "content", "description", "value")

def _walk_json(obj, ctx=None, out=None):
    """Find dicts that carry a URL; inherit timestamp/title from parent records (Meta's nested string_map_data)."""
    if out is None: out = []
    ctx = dict(ctx or {})
    if isinstance(obj, dict):
        for k in TIME_KEYS:
            if k in obj and not isinstance(obj[k], (dict, list)): ctx["time"] = obj[k]
            elif k in obj and isinstance(obj[k], dict):  # {"Saved on": {"href":..., "timestamp":...}}
                sub = obj[k]; ctx["time"] = sub.get("timestamp", ctx.get("time"))
        for k in ("title", "name", "Title"):
            if isinstance(obj.get(k), str) and obj[k] and not obj[k].startswith("http"): ctx["title"] = _fix_mojibake(obj[k])
        if isinstance(obj.get("author"), str): ctx["author"] = _fix_mojibake(obj["author"])
        if isinstance(obj.get("sender_name"), str): ctx["author"] = _fix_mojibake(obj["sender_name"])
        if isinstance(obj.get("content"), str) and obj["content"]: ctx["title"] = _fix_mojibake(URL_RE.sub("", obj["content"]).strip() or obj["content"])
        url = next((obj[k] for k in URL_KEYS if isinstance(obj.get(k), str) and obj[k].startswith("http")), None)
        if url:
            t = obj.get("timestamp") or obj.get("Date") or obj.get("date") or ctx.get("time")
            title = next((_fix_mojibake(obj[k]) for k in TITLE_KEYS if isinstance(obj.get(k), str) and obj[k] and not obj[k].startswith("http")), None) or ctx.get("title")
            out.append({"url": url, "title": title or url, "author": ctx.get("author") or (ctx.get("title") if title != ctx.get("title") else None),
                        "saved_at": to_iso(t), "content": title if title and title != url else None})
        for k, v in obj.items():
            if isinstance(v, (dict, list)): _walk_json(v, ctx, out)
    elif isinstance(obj, list):
        for v in obj: _walk_json(v, ctx, out)
    return out

def import_meta_json(data, platform="instagram", account_id=None, sample=False):
    obj = json.loads(data) if isinstance(data, (str, bytes)) else data
    items = _walk_json(obj)
    for it in items:
        if not it.get("saved_at"): it.pop("saved_at")
        # Meta saved_posts: record "title" is the post author's username
        if platform in ("instagram", "threads") and it.get("author") is None and it.get("title") and not it["title"].startswith("http") and " " not in it["title"]:
            it["author"] = it["title"]; it["title"] = f"Saved post by @{it['title']}"
    return _add(items, platform, account_id, f"{platform}_export", sample)

def import_tiktok_json(data, account_id=None, sample=False):
    obj = json.loads(data) if isinstance(data, (str, bytes)) else data
    items = []
    def find(o, label):
        if isinstance(o, dict):
            for k, v in o.items():
                lab = k if k in ("Favorite Videos", "Like List", "FavoriteVideoList", "ItemFavoriteList", "Favorite Sounds", "Favorite Collection") else label
                find(v, lab)
            link = o.get("Link") or o.get("link") or o.get("VideoLink")
            if isinstance(link, str) and link.startswith("http"):
                items.append({"url": link, "title": f"TikTok {(label or 'favorite').replace('List','').strip().lower()}: {link.rstrip('/').split('/')[-1]}",
                              "saved_at": to_iso(o.get("Date") or o.get("date")), "content": f"From TikTok data export ({label or 'unknown section'})"})
        elif isinstance(o, list):
            for v in o: find(v, label)
    find(obj, None)
    for it in items:
        if not it["saved_at"]: it.pop("saved_at")
    return _add(items, "tiktok", account_id, "tiktok_export", sample)

# ---------- WhatsApp chat export ----------
WA_LINE = re.compile(r"^\[?(\d{1,4}[/.\-]\d{1,2}[/.\-]\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?\s*(?:[apAP]\.?\s?[mM]\.?)?)\]?\s*[-–]?\s*([^:]+?):\s(.*)$")
def import_whatsapp_txt(text, account_id=None, chat_name=None, sample=False):
    if isinstance(text, bytes): text = text.decode("utf-8", "replace")
    items, cur = [], None
    for line in text.splitlines():
        line = line.replace("\u200e", "").replace("\u202f", " ")
        m = WA_LINE.match(line)
        if m:
            cur = {"date": m.group(1), "time": m.group(2), "sender": m.group(3).strip(), "msg": m.group(4)}
        elif cur: cur["msg"] = line
        else: continue
        for url in URL_RE.findall(cur["msg"]):
            d = None
            for fmt in ("%d/%m/%Y", "%d/%m/%y", "%m/%d/%y", "%Y-%m-%d", "%d.%m.%y", "%d.%m.%Y"):
                try: d = datetime.strptime(cur["date"], fmt).replace(tzinfo=timezone.utc).isoformat(); break
                except ValueError: pass
            msg = URL_RE.sub("", cur["msg"]).strip()
            items.append({"url": url, "title": msg[:120] or f"Link shared by {cur['sender']}", "author": cur["sender"],
                          "content": f"{cur['msg']}\n(WhatsApp chat{(': ' + chat_name) if chat_name else ''})", "saved_at": d,
                          "platform": "whatsapp"})
    for it in items:
        if not it["saved_at"]: it.pop("saved_at")
    return _add(items, "whatsapp", account_id, "whatsapp_export", sample)

# ---------- HTML (browser bookmarks / any link list) ----------
class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self._cur = None; self.folder = []; self._h = False
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href", "").startswith("http"):
            self._cur = {"url": a["href"], "title": "", "saved_at": to_iso(a.get("add_date")), "folder": "/".join(self.folder)}
        if tag == "h3": self._h = True; self.folder.append("")
        if tag == "dl" and not self.folder: pass
    def handle_endtag(self, tag):
        if tag == "a" and self._cur:
            self.links.append(self._cur); self._cur = None
        if tag == "h3": self._h = False
        if tag == "dl" and self.folder: self.folder.pop()
    def handle_data(self, data):
        if self._cur is not None: self._cur["title"] += data
        elif self._h and self.folder: self.folder[-1] += data.strip()

def import_html(html, platform="auto", account_id=None, sample=False):
    if isinstance(html, bytes): html = html.decode("utf-8", "replace")
    p = _LinkParser(); p.feed(html)
    items = []
    for l in p.links:
        it = {"url": l["url"], "title": l["title"].strip() or l["url"], "content": f"Bookmark folder: {l['folder']}" if l["folder"] else None}
        if l["saved_at"]: it["saved_at"] = l["saved_at"]
        items.append(it)
    return _add(items, platform, account_id, "html_import", sample)

# ---------- generic CSV / JSON / pasted text ----------
def _norm_row(r):
    g = lambda *ks: next((r[k] for k in ks if r.get(k)), None)
    return {"url": g("url", "URL", "link", "Link", "href"), "title": g("title", "Title", "name", "标题"),
            "author": g("author", "Author", "creator", "作者"), "content": g("content", "notes", "note", "description", "text", "备注"),
            "saved_at": to_iso(g("saved_at", "date", "Date", "created", "time", "时间")), "platform": g("platform", "Platform")}

def import_csv(text, platform="auto", account_id=None, sample=False):
    if isinstance(text, bytes): text = text.decode("utf-8-sig", "replace")
    items = []
    for r in csv.DictReader(io.StringIO(text)):
        it = {k: v for k, v in _norm_row(r).items() if v}
        if it.get("platform") not in db.PLATFORMS: it.pop("platform", None)
        it.setdefault("title", it.get("url"))
        items.append(it)
    return _add(items, platform, account_id, "csv_import", sample)

def import_json_list(data, platform="auto", account_id=None, sample=False):
    obj = json.loads(data) if isinstance(data, (str, bytes)) else data
    if isinstance(obj, dict): obj = obj.get("items") or obj.get("links") or obj.get("data") or [obj]
    items = []
    for r in obj:
        if isinstance(r, str): r = {"url": r}
        it = {k: v for k, v in _norm_row(r).items() if v}
        if it.get("platform") not in db.PLATFORMS: it.pop("platform", None)
        it.setdefault("title", it.get("url")); items.append(it)
    return _add(items, platform, account_id, "json_import", sample)

def import_pasted(text, platform="auto", account_id=None, sample=False):
    """Paste share links/share text (e.g. 小红书/抖音/微信 share text). Each line or block with a URL -> item."""
    items = []
    for block in re.split(r"\n\s*\n|\r?\n(?=.*https?://)", text):
        urls = URL_RE.findall(block)
        if not urls: continue
        title = URL_RE.sub(" ", block)
        title = re.split(r"[，,]?\s*(?:复制本条信息|复制此链接|复制打开|打开【|打开(?:小红书|抖音|Dou音|微信))", title)[0]
        title = re.sub(r"^\s*\d+(\.\d+)?\s*[A-Za-z]{2,4}:/\s*", " ", title)  # douyin share prefix like '7.89 abc:/'
        title = re.sub(r"[A-Za-z]@[A-Za-z]\.[A-Za-z]{2,}\s*\d+/\d+", " ", title)
        title = re.sub(r"\s+", " ", title).strip(" -—|·,，") or None
        for u in urls:
            items.append({"url": u.rstrip(".,"), "title": title or u, "content": block.strip()})
    return _add(items, platform, account_id, "paste", sample)

# ---------- contacts ----------
def import_vcf(text, account_id=None, platform=None, sample=False):
    if isinstance(text, bytes): text = text.decode("utf-8", "replace")
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold
    n = 0; created = 0
    for card in re.findall(r"BEGIN:VCARD(.*?)END:VCARD", text, re.S | re.I):
        c = {"name": None, "phones": [], "emails": [], "org": None, "notes": None}
        for line in card.splitlines():
            if ":" not in line: continue
            k, v = line.split(":", 1); key = k.split(";")[0].upper(); v = v.strip()
            if key == "FN": c["name"] = v
            elif key == "N" and not c["name"]: c["name"] = " ".join(x for x in reversed(v.split(";")[:2]) if x)
            elif key == "TEL": c["phones"].append(v)
            elif key == "EMAIL": c["emails"].append(v)
            elif key == "ORG": c["org"] = v.replace(";", " ").strip()
            elif key == "NOTE": c["notes"] = v
        c.update(account_id=account_id or None, platform=platform, is_sample=int(sample), source="vcf")
        _, cr = db.add_contact(c); n += 1; created += cr
    return {"parsed": n, "created": created, "duplicates": n - created}

def import_contacts_csv(text, account_id=None, platform=None, sample=False):
    if isinstance(text, bytes): text = text.decode("utf-8-sig", "replace")
    n = created = 0
    for r in csv.DictReader(io.StringIO(text)):
        g = lambda *ks: next((r[k] for k in ks if r.get(k)), "")
        name = g("name", "Name", "full_name", "Display Name") or " ".join(x for x in (g("First Name", "Given Name"), g("Last Name", "Family Name")) if x)
        phones = [v for k, v in r.items() if k and "phone" in k.lower() and v]
        emails = [v for k, v in r.items() if k and ("email" in k.lower() or "e-mail" in k.lower()) and v]
        _, cr = db.add_contact({"name": name, "phones": phones, "emails": emails, "org": g("org", "Organization", "Company", "Organization 1 - Name"),
                                "notes": g("notes", "Notes"), "platform": g("platform") or platform, "account_id": account_id or None, "is_sample": int(sample), "source": "csv"})
        n += 1; created += cr
    return {"parsed": n, "created": created, "duplicates": n - created}

# ---------- accounts CSV ----------
ACCOUNT_CSV_COLS = ["platform", "display_name", "handle", "profile_url", "linked_email", "linked_phone", "notes", "tags", "status", "connected_via", "created_at"]
def import_accounts_csv(text):
    if isinstance(text, bytes): text = text.decode("utf-8-sig", "replace")
    n = created = 0
    for r in csv.DictReader(io.StringIO(text)):
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        if any("password" in k.lower() or "secret" in k.lower() for k in r if r[k]):
            r = {k: v for k, v in r.items() if "password" not in k.lower() and "secret" not in k.lower()}  # never store secrets
        d = {k: r[k] for k in ACCOUNT_CSV_COLS if r.get(k)}
        if d.get("platform") not in db.PLATFORMS: d["platform"] = detect_platform(d.get("profile_url"), "web")
        _, cr = db.add_account(d); n += 1; created += cr
    return {"parsed": n, "created": created, "duplicates": n - created}

def export_accounts_csv():
    out = io.StringIO(); w = csv.writer(out); w.writerow(["id"] + ACCOUNT_CSV_COLS)
    for a in db.list_accounts(limit=100000)["accounts"]:
        w.writerow([a["id"]] + [", ".join(a[c]) if c == "tags" else (a.get(c) or "") for c in ACCOUNT_CSV_COLS])
    return out.getvalue()

# ---------- dispatcher ----------
KINDS = ["instagram_json", "facebook_json", "threads_json", "messenger_json", "tiktok_json", "whatsapp_txt", "bookmarks_html", "html", "csv", "json", "paste", "vcf", "contacts_csv", "accounts_csv"]
def import_auto(kind, data, platform=None, account_id=None, filename=""):
    kind = kind or "auto"
    if kind == "auto":
        f = filename.lower()
        kind = ("vcf" if f.endswith(".vcf") else "whatsapp_txt" if f.endswith(".txt") else "bookmarks_html" if f.endswith((".html", ".htm"))
                else "csv" if f.endswith(".csv") else "json")
    if kind in ("instagram_json", "facebook_json", "threads_json", "messenger_json"):
        return import_meta_json(data, kind.split("_")[0], account_id)
    if kind == "tiktok_json": return import_tiktok_json(data, account_id)
    if kind == "whatsapp_txt": return import_whatsapp_txt(data, account_id, chat_name=filename)
    if kind in ("bookmarks_html", "html"): return import_html(data, platform or "auto", account_id)
    if kind == "csv": return import_csv(data, platform or "auto", account_id)
    if kind == "json": return import_json_list(data, platform or "auto", account_id)
    if kind == "paste": return import_pasted(data if isinstance(data, str) else data.decode(), platform or "auto", account_id)
    if kind == "vcf": return import_vcf(data, account_id, platform)
    if kind == "contacts_csv": return import_contacts_csv(data, account_id, platform)
    if kind == "accounts_csv": return import_accounts_csv(data)
    raise ValueError(f"unknown import kind {kind}; one of {KINDS}")

# ---------- accounts: handle normalisation + bulk paste ----------
PROFILE_URL = {
    "x": "https://x.com/{h}", "threads": "https://www.threads.net/@{h}", "instagram": "https://www.instagram.com/{h}/",
    "facebook": "https://www.facebook.com/{h}", "messenger": "https://m.me/{h}", "tiktok": "https://www.tiktok.com/@{h}",
}
PLATFORM_ALIASES = {
    "x": "x", "twitter": "x", "tw": "x", "threads": "threads", "th": "threads", "instagram": "instagram", "ig": "instagram", "insta": "instagram",
    "facebook": "facebook", "fb": "facebook", "messenger": "messenger", "msgr": "messenger", "whatsapp": "whatsapp", "wa": "whatsapp",
    "wechat": "wechat", "weixin": "wechat", "wx": "wechat", "微信": "wechat", "tiktok": "tiktok", "tt": "tiktok",
    "douyin": "douyin", "dy": "douyin", "抖音": "douyin", "xiaohongshu": "xiaohongshu", "xhs": "xiaohongshu", "red": "xiaohongshu",
    "rednote": "xiaohongshu", "小红书": "xiaohongshu", "web": "web",
}

def clean_handle(h):
    h = (h or "").strip().strip(",;|")
    return h[1:].strip() if h.startswith("@") else h

def profile_url_for(platform, handle):
    h = clean_handle(handle)
    if platform == "whatsapp":
        digits = re.sub(r"\D", "", h)
        return f"https://wa.me/{digits}" if len(digits) >= 7 else ""
    if not h or " " in h: return ""
    t = PROFILE_URL.get(platform)
    return t.format(h=h) if t else ""

def _handle_from_url(url):
    path = [p for p in re.sub(r"^https?://[^/]+", "", url.split("?")[0]).split("/") if p]
    if not path: return ""
    h = path[-1] if path[0] in ("user", "profile", "people") else path[0]
    return clean_handle(h)

def parse_handle_lines(text, platform="", default_status="active", tags=None):
    """Parse pasted lines into account dicts. Accepted per line:
    '@foo' · 'foo' · 'instagram @foo' · 'ig @foo Food account' · '@foo, Display Name' · 'https://instagram.com/foo'.
    Lines starting with '#' are ignored."""
    out, errors = [], []
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"): continue
        name = ""
        if "," in line or "|" in line or "\t" in line:
            line, name = [s.strip() for s in re.split(r"[,|\t]", line, maxsplit=1)]
        parts = line.split()
        p, explicit = platform or "", False
        if parts and PLATFORM_ALIASES.get(parts[0].lower().rstrip(":")) and len(parts) > 1:
            p, explicit = PLATFORM_ALIASES[parts.pop(0).lower().rstrip(":")], True
        if not parts: errors.append({"line": n, "text": raw, "error": "no handle"}); continue
        tok, rest = parts[0], " ".join(parts[1:])
        url = ""
        if re.match(r"^https?://", tok, re.I):
            url = tok; p = p if explicit else (detect_platform(tok, "") or p); h = _handle_from_url(tok)
        else:
            h = clean_handle(tok)
            if p == "whatsapp" and rest and re.fullmatch(r"[\d\s\-()+]+", tok + " " + rest):
                h, rest = (tok + " " + rest).strip(), ""
        name = name or rest
        if not p: errors.append({"line": n, "text": raw, "error": "unknown platform — pick one or prefix the line, e.g. 'instagram @foo'"}); continue
        if not h: errors.append({"line": n, "text": raw, "error": "no handle"}); continue
        out.append({"platform": p, "handle": h, "display_name": name or "", "profile_url": url or profile_url_for(p, h),
                    "status": default_status or "active", "tags": list(tags or []), "connected_via": "bulk paste"})
    return out, errors

def bulk_add_accounts(text, platform="", status="active", tags=None):
    parsed, errors = parse_handle_lines(text, platform, status, tags)
    created, dups = [], []
    for d in parsed:
        aid, cr = db.add_account(d)
        (created if cr else dups).append({"id": aid, "platform": d["platform"], "handle": d["handle"]})
    return {"parsed": len(parsed), "created": len(created), "duplicates": len(dups), "created_accounts": created, "duplicate_accounts": dups, "errors": errors}
