"""Headless screenshots of the running app (default http://127.0.0.1:8765) into screenshots/.
  python tests/screenshots.py                     # the classic set (01-07)
  python tests/screenshots.py after 40            # media/badges/empty-state redesign: 40_after_* (+ 41_empty_*), light + dark
Run it against a server on a THROWAWAY DB with the sample data. Empty states are produced with route mocks (nothing is deleted)."""
import os, pathlib, re, sys
from playwright.sync_api import sync_playwright
BASE = os.environ.get("SAVEHUB_URL", "http://127.0.0.1:8765")
OUT = pathlib.Path(__file__).resolve().parent.parent / "screenshots"; OUT.mkdir(exist_ok=True)
MODE = sys.argv[1] if len(sys.argv) > 1 else "classic"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 40
EMPTY_SUMMARY = {"days": 7, "since": "2026-01-01T00:00:00+00:00", "total": 0, "by_platform": {}, "top_tags": [], "top_authors": [], "by_day": {},
                 "by_type": {}, "digest": "", "digest_by": "local", "recent": []}
NEEDS_LOGIN = {"available": True, "enabled": True, "interval_minutes": 10, "loop": {"running": True, "pid": 1, "where": "box"}, "backends": {},
               "accounts": [{"platform": "tiktok", "account": "@js.clips.sample", "account_id": None, "state": "needs_login",
                             "fix": "python -m savehub.collector login tiktok --account @js.clips.sample"}]}


def warm(pg):
    """Scroll through the page once so lazy images are loaded before a full-page shot."""
    for y in range(0, 8000, 600): pg.evaluate(f"window.scrollTo(0,{y})"); pg.wait_for_timeout(50)
    pg.evaluate("window.scrollTo(0,0)"); pg.wait_for_timeout(400)


def classic(b):
    shots = [("01_dashboard_home", "#/library", None, "dark"), ("02_search_result", "#/library?q=咖啡店", None, "dark"),
             ("03_summary", "#/summary", None, "dark"), ("04_connect_apps", "#/connect", None, "dark"),
             ("05_accounts", "#/accounts", None, "dark"), ("06_contacts_light", "#/contacts", None, "light"),
             ("07_library_table_light", "#/library?q=", "list", "light")]
    for name, hash_, view, theme in shots:
        pg = b.new_page(viewport={"width": 1366, "height": 900}, device_scale_factor=1)
        pg.add_init_script(f"localStorage.theme='{theme}'; localStorage.libView='{view or 'grid'}';")
        pg.goto(BASE + "/" + hash_); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(700); warm(pg)
        pg.screenshot(path=str(OUT / f"{name}.png"), full_page=name in ("03_summary", "04_connect_apps"))
        print("saved", OUT / f"{name}.png"); pg.close()


def after(b):
    for theme in ("light", "dark"):
        ctx = b.new_context(viewport={"width": 1366, "height": 900}); ctx.add_init_script(f"localStorage.theme='{theme}'; localStorage.libView='grid';")
        pg = ctx.new_page()
        def save(name, full=False):
            p = OUT / f"{N}_after_{name}_{theme}.png"; pg.screenshot(path=str(p), full_page=full); print("saved", p)
        pg.goto(BASE + "/#/library"); pg.wait_for_selector(".card.item"); warm(pg); save("library")
        pg.click("[data-lv=list]"); pg.wait_for_timeout(500); warm(pg); save("library_list"); pg.click("[data-lv=grid]"); pg.wait_for_timeout(400)
        pg.locator(".card.item:has(.cv-badge.cnt)").first.click(); pg.wait_for_selector("#gallery.many"); pg.wait_for_timeout(700); save("detail_gallery")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
        pg.goto(BASE + "/#/library?q=concert"); pg.wait_for_selector(".card.item"); pg.locator(".card.item").first.click(); pg.wait_for_selector("#dVideo"); pg.wait_for_timeout(900); save("detail_video")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        for page in ("accounts", "contacts", "summary", "connect"):
            pg.goto(BASE + f"/#/{page}"); pg.wait_for_timeout(1200); warm(pg); save(page, full=page in ("summary", "connect"))
        # phone
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.goto(BASE + "/#/library"); pg.reload(); pg.wait_for_selector(".card.item"); warm(pg); save("library_phone")
        pg.locator(".card.item").nth(1).click(); pg.wait_for_selector("#dTitle"); pg.wait_for_timeout(700); save("detail_phone")
        pg.keyboard.press("Escape"); pg.set_viewport_size({"width": 1366, "height": 900})
        # empty states
        E = N + 1
        def esave(name):
            p = OUT / f"{E}_empty_{name}_{theme}.png"; pg.screenshot(path=str(p)); print("saved", p)
        pg.goto(BASE + "/#/library"); pg.reload(); pg.wait_for_selector(".card.item")
        pg.fill("#q", "zzqqxx-nothing"); pg.wait_for_selector("#emptySearch"); pg.wait_for_timeout(600); esave("search")
        pg.click("#emptySearch button:has-text('Clear search')"); pg.wait_for_timeout(600)
        pg.click("#tseg [data-ty=file]"); pg.wait_for_selector("#emptyFilter"); pg.wait_for_timeout(600); esave("filter")
        pg.click("#emptyFilter button:has-text('Clear filters')"); pg.wait_for_timeout(500)
        z = pg.locator("#pchips .chip:has(.n:text-is('0'))")
        if z.count(): z.first.click(); pg.wait_for_selector("#emptyPlatform"); pg.wait_for_timeout(600); esave("app_not_connected")
        pg.route(re.compile(r"/api/items\?"), lambda r: r.fulfill(json={"total": 0, "items": []}))
        pg.route(re.compile(r"/api/accounts\?"), lambda r: r.fulfill(json={"total": 0, "accounts": []}))
        pg.route(re.compile(r"/api/contacts\?"), lambda r: r.fulfill(json={"total": 0, "contacts": []}))
        pg.route(re.compile(r"/api/summary\?"), lambda r: r.fulfill(json=EMPTY_SUMMARY))
        for page, sel in (("library", "#emptyLibrary"), ("accounts", "#emptyAccounts"), ("contacts", "#emptyContacts"), ("summary", "#emptySummary")):
            pg.goto(BASE + f"/#/{page}"); pg.reload(); pg.wait_for_selector(sel); pg.wait_for_timeout(700); esave(page)
        pg.unroute(re.compile(r"/api/accounts\?"))
        pg.route(re.compile(r"/api/autosync$"), lambda r: r.fulfill(json=NEEDS_LOGIN) if r.request.method == "GET" else r.continue_())
        pg.goto(BASE + "/#/accounts"); pg.reload(); pg.wait_for_selector("#needsLogin"); pg.wait_for_timeout(600); esave("needs_login")
        pg.locator("#needsLogin button").first.click(); pg.wait_for_selector("#siT"); pg.wait_for_timeout(400); esave("sign_in_sheet")
        ctx.close()


with sync_playwright() as p:
    exe = os.environ.get("CHROME", "/usr/bin/google-chrome")
    b = p.chromium.launch(executable_path=exe if os.path.exists(exe) else None, args=["--no-sandbox"])
    after(b) if MODE == "after" else classic(b)
    b.close()
