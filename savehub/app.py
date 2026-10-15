"""SaveHub web app + REST API."""
import os
from typing import Optional
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from . import db, importers, summary, sync, tagger, xoauth

app = FastAPI(title="SaveHub", version="1.0")
STATIC = db.ROOT / "static"

def _csv(v): return [x for x in (v or "").split(",") if x]

@app.get("/", response_class=HTMLResponse)
def index(): return FileResponse(STATIC / "index.html")

app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.get("/api/health")
def health(): return {"ok": True, "stats": db.stats(), "ai": tagger.provider_status()}

@app.get("/api/platforms")
def platforms():
    s = db.get_settings(); st = db.stats()["by_platform"]
    return [{"id": k, **v, "enabled": k in s["enabled_platforms"], "count": st.get(k, 0)} for k, v in db.PLATFORMS.items()]

# ---- items ----
@app.get("/api/items")
def items(q: str = "", platform: str = "", tag: str = "", account_id: str = "", sort: str = "saved_at", order: str = "desc",
          limit: int = 50, offset: int = 0, enabled_only: bool = False):
    en = db.get_settings()["enabled_platforms"] if enabled_only else None
    return db.list_items(q, _csv(platform), _csv(tag), account_id or None, sort, order, limit, offset, en)

@app.get("/api/items/{item_id}")
def item(item_id: int):
    it = db.get_item(item_id)
    if not it: raise HTTPException(404)
    return it

@app.post("/api/items")
async def add_item(req: Request):
    d = await req.json()
    if not d.get("url") and not d.get("content"): raise HTTPException(400, "url or content required")
    if d.get("url") and not d.get("platform"): d["platform"] = importers.detect_platform(d["url"])
    d.setdefault("title", d.get("url")); d["source"] = "manual"
    iid, created = db.add_item(d)
    return {"id": iid, "created": created, "item": db.get_item(iid)}

@app.patch("/api/items/{item_id}")
async def patch_item(item_id: int, req: Request):
    db.update_item(item_id, await req.json()); return db.get_item(item_id)

@app.delete("/api/items/{item_id}")
def del_item(item_id: int): db.delete_item(item_id); return {"ok": True}

@app.post("/api/tag")
async def tag(req: Request):
    d = await req.json() if (await req.body()) else {}
    return {"retagged": tagger.retag(d.get("ids"), force=d.get("force", False)), "provider": tagger.provider_status()}

@app.get("/api/tags")
def tags(): return db.all_tags()

@app.get("/api/summary")
def get_summary(days: int = 7, ai: bool = True): return summary.get_summary(days, ai)

@app.post("/api/samples/clear")
def clear(): return db.clear_samples()

@app.post("/api/samples/seed")
def seed_samples():
    from . import seed; return seed.seed(force=True)

# ---- import ----
@app.post("/api/import")
async def do_import(kind: str = Form("auto"), platform: str = Form(""), account_id: str = Form(""),
                    file: Optional[UploadFile] = File(None), text: str = Form("")):
    data = await file.read() if file else text
    if not data: raise HTTPException(400, "file or text required")
    try:
        return importers.import_auto(kind, data, platform or None, int(account_id) if account_id else None, file.filename if file else "")
    except Exception as e:
        raise HTTPException(400, f"Import failed: {e}")

# ---- contacts ----
@app.get("/api/contacts")
def contacts(q: str = "", account_id: str = "", platform: str = "", sort: str = "name", order: str = "asc", limit: int = 200, offset: int = 0):
    return db.search_contacts(q, account_id or None, platform or None, sort, order, limit, offset)

@app.post("/api/contacts")
async def add_contact(req: Request):
    cid, c = db.add_contact(await req.json()); return {"id": cid, "created": c}

@app.delete("/api/contacts/{cid}")
def del_contact(cid: int): db.delete_contact(cid); return {"ok": True}

# ---- accounts ----
@app.get("/api/accounts")
def accounts(q: str = "", platform: str = "", status: str = "", sort: str = "platform", order: str = "asc", limit: int = 500, offset: int = 0):
    return db.list_accounts(q, _csv(platform) or None, status or None, sort, order, limit, offset)

@app.post("/api/accounts")
async def add_account(req: Request):
    d = await req.json()
    if any("password" in k.lower() or "secret" in k.lower() for k in d): raise HTTPException(400, "SaveHub does not store passwords/secrets. Use a password manager.")
    aid, c = db.add_account(d); return {"id": aid, "created": c, "account": db.get_account(aid)}

@app.put("/api/accounts/{aid}")
async def put_account(aid: int, req: Request):
    db.update_account(aid, await req.json()); return db.get_account(aid)

@app.delete("/api/accounts/{aid}")
def del_account(aid: int): db.delete_account(aid); return {"ok": True}

@app.get("/api/accounts/export.csv", response_class=PlainTextResponse)
def export_accounts():
    return PlainTextResponse(importers.export_accounts_csv(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=savehub_accounts.csv"})

# ---- settings ----
def _redact(s):
    s = dict(s); s.pop("x_tokens", None)
    for k, f in (("notion", "token"), ("x", "client_secret")):
        if s.get(k, {}).get(f): s[k] = {**s[k], f: "••••" + s[k][f][-4:]}
    return s

@app.get("/api/settings")
def settings(): return {"settings": _redact(db.get_settings()), "ai": tagger.provider_status(), "x": xoauth.status()}

@app.put("/api/settings")
async def put_settings(req: Request):
    d = await req.json(); cur = db.get_settings()
    for k, v in d.items():
        if k not in db.DEFAULT_SETTINGS: continue
        if isinstance(v, dict):
            v = {kk: vv for kk, vv in v.items() if not (isinstance(vv, str) and vv.startswith("••••"))}  # keep masked secrets
            v = {**cur.get(k, {}), **v}
        db.set_setting(k, v)
    return settings()

@app.post("/api/sync")
def do_sync(target: str = "all"): return sync.sync_now(target)

# ---- X OAuth ----
@app.get("/api/x/auth")
def x_auth():
    try: return RedirectResponse(xoauth.start_url())
    except ValueError as e: raise HTTPException(400, str(e))

@app.get("/api/x/callback")
def x_callback(code: str = "", state: str = "", error: str = ""):
    if error: return RedirectResponse(f"/#/connect?x_error={error}")
    try: xoauth.handle_callback(code, state); return RedirectResponse("/#/connect?x=connected")
    except Exception as e: return RedirectResponse(f"/#/connect?x_error={str(e)[:100]}")

@app.post("/api/x/sync")
def x_sync():
    try: return xoauth.sync_bookmarks()
    except Exception as e: raise HTTPException(400, f"X sync failed: {e}")

def main():
    import uvicorn
    from . import seed
    seed.seed()
    uvicorn.run(app, host=os.environ.get("SAVEHUB_HOST", "127.0.0.1"), port=int(os.environ.get("SAVEHUB_PORT", 8765)))

if __name__ == "__main__": main()
