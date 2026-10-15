"""Optional sync targets. Both OFF by default.
- Drive 'folder' mode: writes JSON/CSV/Markdown exports into a folder (e.g. a Google Drive for desktop synced folder). TESTED locally.
- Drive 'service_account' mode: uploads via Google Drive API using google-api-python-client (optional dependency). UNTESTED.
- Notion: creates pages in a database via the official REST API with an integration token. UNTESTED (needs user token).
"""
import csv, io, json, os
from pathlib import Path
import httpx
from . import db

def export_all():
    items = db.list_items(limit=100000)["items"]
    out_json = json.dumps(items, ensure_ascii=False, indent=2)
    s = io.StringIO(); cols = ["id","platform","title","url","author","saved_at","tags","summary","account_name"]
    w = csv.writer(s); w.writerow(cols)
    for i in items: w.writerow([", ".join(i[c]) if c == "tags" else i.get(c) or "" for c in cols])
    md = ["# SaveHub export\n"]
    for i in items:
        md.append(f"## {i['title']}\n- Platform: {i['platform']}  \n- Saved: {i['saved_at']}  \n- URL: {i.get('url') or ''}  \n- Tags: {', '.join(i['tags'])}  \n\n{i.get('summary') or ''}\n")
    return {"savehub_export.json": out_json, "savehub_export.csv": s.getvalue(), "savehub_export.md": "\n".join(md)}

def sync_drive(cfg):
    files = export_all()
    if cfg.get("mode", "folder") == "folder":
        p = Path(os.path.expanduser(cfg.get("folder_path") or "data/export")); p.mkdir(parents=True, exist_ok=True)
        for name, content in files.items(): (p / name).write_text(content, encoding="utf-8")
        return {"ok": True, "mode": "folder", "path": str(p), "files": list(files)}
    # service account upload (untested)
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload
    except ImportError:
        return {"ok": False, "error": "service_account mode needs: pip install google-api-python-client google-auth (untested path)"}
    creds = service_account.Credentials.from_service_account_file(cfg["service_account_file"], scopes=["https://www.googleapis.com/auth/drive.file"])
    svc = build("drive", "v3", credentials=creds)
    ids = []
    for name, content in files.items():
        media = MediaIoBaseUpload(io.BytesIO(content.encode()), mimetype="text/plain")
        meta = {"name": name, **({"parents": [cfg["folder_id"]]} if cfg.get("folder_id") else {})}
        ids.append(svc.files().create(body=meta, media_body=media, fields="id").execute()["id"])
    return {"ok": True, "mode": "service_account", "file_ids": ids}

def sync_notion(cfg, limit=200):
    tok, dbid = cfg.get("token"), cfg.get("database_id")
    if not tok or not dbid: return {"ok": False, "error": "Notion token and database id required"}
    h = {"Authorization": f"Bearer {tok}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"}
    c = db.conn(); done = 0; errors = []
    pending = c.execute("SELECT * FROM items WHERE notion_page_id IS NULL ORDER BY saved_at DESC LIMIT ?", (limit,)).fetchall()
    for r in pending:
        tags = json.loads(r["tags"] or "[]")
        props = {"Name": {"title": [{"text": {"content": (r["title"] or r["url"] or "Untitled")[:200]}}]},
                 "URL": {"url": r["url"]}, "Platform": {"select": {"name": r["platform"]}},
                 "Tags": {"multi_select": [{"name": t[:100].replace(",", " ")} for t in tags]},
                 "Saved": {"date": {"start": r["saved_at"]}} if r["saved_at"] else {"date": None},
                 "Summary": {"rich_text": [{"text": {"content": (r["summary"] or "")[:1900]}}]}}
        resp = httpx.post("https://api.notion.com/v1/pages", headers=h, json={"parent": {"database_id": dbid}, "properties": props}, timeout=30)
        if resp.status_code == 200:
            c.execute("UPDATE items SET notion_page_id=? WHERE id=?", (resp.json()["id"], r["id"])); done += 1
        else:
            errors.append(resp.text[:200]); break
    c.commit()
    return {"ok": not errors, "synced": done, "errors": errors,
            "note": "Database needs properties: Name (title), URL (url), Platform (select), Tags (multi-select), Saved (date), Summary (text)."}

def sync_now(target="all"):
    s = db.get_settings(); res = {}
    if target in ("all", "drive"):
        res["drive"] = sync_drive(s["drive"]) if s["drive"]["enabled"] else {"skipped": "Drive sync disabled in settings"}
    if target in ("all", "notion"):
        res["notion"] = sync_notion(s["notion"]) if s["notion"]["enabled"] else {"skipped": "Notion sync disabled in settings"}
    return res
