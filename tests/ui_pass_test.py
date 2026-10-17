"""Headless browser pass over EVERY dashboard page in light AND dark mode, plus the main flows.
Collects console errors, page errors, failed requests / HTTP >= 400 responses and broken images (naturalWidth == 0).
Needs a running server on a THROWAWAY DB (it adds/edits/deletes items, accounts and contacts):
  SAVEHUB_DB=/tmp/x.db SAVEHUB_PORT=8766 python -m savehub.server &   then   SAVEHUB_URL=http://127.0.0.1:8766 python tests/ui_pass_test.py
Screenshots go to $SHOTS (default /tmp/savehub-ui-pass)."""
import os, pathlib, re
import httpx
from playwright.sync_api import sync_playwright

BASE = os.environ.get("SAVEHUB_URL", "http://127.0.0.1:8766")
OUT = pathlib.Path(os.environ.get("SHOTS", "/tmp/savehub-ui-pass")); OUT.mkdir(parents=True, exist_ok=True)
SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"
DASH_PW = os.environ.get("SAVEHUB_DASHBOARD_PASSWORD", "")
AUTH = ("savehub", DASH_PW) if DASH_PW else None
PAGES = ["library", "summary", "summary?days=30", "accounts", "contacts", "connect", "settings"]
results = []


def check(name, ok, detail=""):
    results.append(bool(ok)); print(("PASS" if ok else "FAIL"), name, "" if ok else detail)


def broken_images(pg):
    return pg.evaluate("""() => [...document.images].filter(i => i.complete && i.naturalWidth === 0 && i.getAttribute('src')).map(i => i.getAttribute('src'))""")


def overflowing(pg):
    """Form controls / buttons that stick out of their panel or card, plus page-level horizontal scroll."""
    return pg.evaluate("""() => {
      const out = [];
      if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 1)
        out.push('page scrolls horizontally: ' + document.documentElement.scrollWidth + ' > ' + document.documentElement.clientWidth);
      for (const el of document.querySelectorAll('#view .panel input, #view .panel select, #view .panel textarea, #view .panel .btn, #view .card input, #view .card select, #view .card .btn')) {
        const box = el.closest('.panel, .card'); if (!box || !el.offsetParent) continue;
        const r = el.getBoundingClientRect(), b = box.getBoundingClientRect();
        if (r.right > b.right + 1 || r.left < b.left - 1) out.push((el.id || el.tagName.toLowerCase()) + ' overflows its ' + box.className.split(' ')[0]);
      }
      return out;
    }""")


def wrapped_card_heads(pg):
    """Connect-apps cards whose 'enabled' toggle wrapped onto a second line below the app badge."""
    return pg.evaluate("""() => [...document.querySelectorAll('#view .card .chead')].filter(h => {
        const b = h.querySelector('.pbadge'), l = h.querySelector('label'); if (!b || !l) return false;
        const rb = b.getBoundingClientRect(), rl = l.getBoundingClientRect();
        return Math.abs((rb.top + rb.bottom) / 2 - (rl.top + rl.bottom) / 2) > 6 || rl.right > h.getBoundingClientRect().right + 1;
      }).map(h => h.querySelector('.pbadge').textContent.trim())""")


def run_theme(b, theme):
    ctx = b.new_context(viewport={"width": 1366, "height": 900}, http_credentials={"username": AUTH[0], "password": AUTH[1]} if AUTH else None)
    ctx.add_init_script(f"localStorage.theme='{theme}';")
    pg = ctx.new_page()
    errs, fails = [], []
    pg.on("console", lambda m: m.type == "error" and errs.append(f"console: {m.text}"))
    pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    pg.on("requestfailed", lambda r: fails.append(f"{r.method} {r.url} {r.failure}"))
    pg.on("response", lambda r: r.status >= 400 and fails.append(f"{r.status} {r.request.method} {r.url}"))
    pg.on("dialog", lambda d: d.accept())
    for p in PAGES:
        pg.goto(f"{BASE}/#/{p}"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(500)
        check(f"[{theme}] {p}: theme applied", pg.evaluate("document.documentElement.dataset.theme") == theme)
        check(f"[{theme}] {p}: rendered (no error panel)", "Error:" not in pg.inner_text("#view")[:200], pg.inner_text("#view")[:120])
        bi = broken_images(pg); check(f"[{theme}] {p}: no broken images", not bi, str(bi[:5]))
        for w in (1366, 1024, 390):
            pg.set_viewport_size({"width": w, "height": 900}); pg.wait_for_timeout(150)
            ov = overflowing(pg); check(f"[{theme}] {p}: nothing overflows its panel at {w}px", not ov, str(ov[:5]))
            if p == "connect":
                wr = wrapped_card_heads(pg); check(f"[{theme}] connect: every card's enabled toggle stays on the badge line at {w}px", not wr, str(wr))
        pg.set_viewport_size({"width": 1366, "height": 900})
        pg.screenshot(path=str(OUT / f"{theme}_{p.replace('?', '_').replace('=', '')}.png"), full_page=True)
    # ---- library flows
    pg.goto(f"{BASE}/#/library"); pg.wait_for_selector("#results .card")
    for q, want in (("python", "python"), ("咖啡店", "咖啡"), ("咖啡", "咖啡"), ("nasi lemak", "nasi")):
        pg.fill("#q", q); pg.wait_for_timeout(700)
        txt = pg.inner_text("#results").lower()
        check(f"[{theme}] search {q!r} finds results", want.lower() in txt and "no items" not in txt, pg.inner_text("#count"))
    pg.fill("#q", "zzqqxx-nothing"); pg.wait_for_timeout(700)
    check(f"[{theme}] empty search shows empty state", "No items" in pg.inner_text("#results"))
    pg.fill("#q", ""); pg.wait_for_timeout(700)
    for s in ("title", "platform", "author", "relevance", "saved_at"):
        pg.select_option("#sort", s); pg.wait_for_timeout(300)
        check(f"[{theme}] sort {s}", pg.locator("#results .card, #results tr").count() > 0)
    pg.click("#order"); pg.wait_for_timeout(400); check(f"[{theme}] order toggle", "Asc" in pg.inner_text("#order"))
    pg.click("#order"); pg.wait_for_timeout(400)
    n_all = int(re.match(r"\d+", pg.inner_text("#count")).group())
    pg.locator("#pchips .chip").first.click(); pg.wait_for_timeout(500)
    n_f = int(re.match(r"\d+", pg.inner_text("#count")).group())
    check(f"[{theme}] platform filter narrows", 0 < n_f <= n_all, f"{n_f}/{n_all}")
    pg.locator("#pchips .chip").first.click(); pg.wait_for_timeout(500)
    pg.locator("#tchips .chip").first.click(); pg.wait_for_timeout(600)
    check(f"[{theme}] tag chip filter", pg.locator("#tchips .chip.on").count() == 1)
    pg.locator("#tchips .chip.on").first.click(); pg.wait_for_timeout(600)
    # clicking a tag inside a card toggles the tag filter
    pg.locator("#results .tag").first.click(); pg.wait_for_timeout(600)
    check(f"[{theme}] tag click in card filters", pg.locator("#tchips .chip.on").count() == 1)
    pg.locator("#tchips .chip.on").first.click(); pg.wait_for_timeout(600)
    pg.click("#vt"); pg.wait_for_timeout(500); check(f"[{theme}] table view", pg.locator("#results table tr").count() > 1)
    pg.locator("#results th").first.click(); pg.wait_for_timeout(500); check(f"[{theme}] table header sort", "↓" in pg.inner_text("#results thead") or "↑" in pg.inner_text("#results thead"))
    pg.screenshot(path=str(OUT / f"{theme}_library_table.png"))
    pg.set_viewport_size({"width": 390, "height": 844}); pg.wait_for_timeout(200)
    ov = overflowing(pg); check(f"[{theme}] library table view does not scroll the page sideways at 390px", not ov, str(ov[:5]))
    pg.set_viewport_size({"width": 1366, "height": 900}); pg.wait_for_timeout(150)
    pg.click("#vt"); pg.wait_for_timeout(500); check(f"[{theme}] card view", pg.locator("#results .card").count() > 0)
    pg.select_option("#acct", index=1); pg.wait_for_timeout(500); check(f"[{theme}] account filter", pg.locator("#count").inner_text() != "")
    pg.select_option("#acct", index=0); pg.wait_for_timeout(400)
    # add link: empty URL must give feedback, valid URL saves
    pg.click("text=+ Add link"); pg.wait_for_selector("#nurl")
    pg.click("text=Save & auto-tag"); pg.wait_for_timeout(500)
    check(f"[{theme}] add link without URL shows an error toast", pg.locator("#toast.show").count() == 1 and pg.inner_text("#toast").strip() != "", pg.inner_text("#toast"))
    url = f"https://example.com/ui-pass-{theme}"
    pg.fill("#nurl", url); pg.fill("#ntitle", f"UI pass {theme} 中文测试 rock'n'roll"); pg.click("text=Save & auto-tag"); pg.wait_for_timeout(900)
    pg.fill("#q", f"UI pass {theme}"); pg.wait_for_timeout(800)
    check(f"[{theme}] added link is searchable", f"UI pass {theme}" in pg.inner_text("#results"))
    pg.click("#results >> text=details"); pg.wait_for_selector("#etags")
    pg.fill("#etags", f"uipass, it's-{theme}"); pg.click("#modalcard button:text-is('Save')"); pg.wait_for_timeout(800)
    check(f"[{theme}] edit tags saved", f"it's-{theme}" in pg.inner_text("#results"))
    pg.locator("#results .tag", has_text=f"it's-{theme}").first.click(); pg.wait_for_timeout(700)
    check(f"[{theme}] tag with apostrophe is clickable", pg.locator("#tchips .chip.on").count() == 1, pg.inner_text("#count"))
    pg.locator("#tchips .chip.on").first.click(); pg.wait_for_timeout(600)
    pg.fill("#q", f"UI pass {theme}"); pg.wait_for_timeout(800)
    pg.click("#results >> text=details"); pg.wait_for_selector("#etags")
    pg.click("#modalcard button:text-is('Delete')"); pg.wait_for_timeout(800)
    check(f"[{theme}] item deleted", "No items" in pg.inner_text("#results"))
    pg.fill("#q", ""); pg.wait_for_timeout(500)
    # ---- summary chips
    pg.goto(f"{BASE}/#/summary"); pg.wait_for_selector(".kpi")
    pg.click("a.chip:has-text('14 days')"); pg.wait_for_timeout(800); check(f"[{theme}] summary 14 days", "14 days" in pg.inner_text("h2"))
    # ---- accounts: add (single), quick-edit, paste handles, delete
    pg.goto(f"{BASE}/#/accounts"); pg.wait_for_selector(".agroup, .acard, table", timeout=10000)
    pg.click("#addAcct"); pg.wait_for_timeout(400)
    pg.locator("#modalcard .ptile[data-p='instagram']").click(); pg.wait_for_timeout(300)
    h = f"uipass_{theme}"
    pg.fill("#f_handle", h); pg.wait_for_timeout(200)
    pg.click("#amSaveClose"); pg.wait_for_timeout(900)
    accts = httpx.get(BASE + "/api/accounts", params={"q": h}, auth=AUTH).json()["accounts"]
    check(f"[{theme}] account added via UI", any(a["handle"] == h for a in accts), str([a["handle"] for a in accts]))
    if pg.locator("#modal:not(.hidden)").count(): pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    # paste handles
    pg.click("#addAcct"); pg.wait_for_timeout(300)
    pg.click("#modalcard button.linkbtn:has-text('Paste many handles')"); pg.wait_for_selector("#btext")
    pg.select_option("#bp", "x"); pg.fill("#btext", f"@paste1_{theme}\n@paste2_{theme}, Paste Two"); pg.wait_for_timeout(700)
    check(f"[{theme}] paste preview counts 2", "2" in pg.inner_text("#bprev"), pg.inner_text("#bprev")[:80])
    pg.click("#bgo"); pg.wait_for_timeout(900)
    got = httpx.get(BASE + "/api/accounts", params={"q": f"_{theme}"}, auth=AUTH).json()["total"]
    check(f"[{theme}] paste handles adds accounts", got >= 3, str(got))
    if pg.locator("#modal:not(.hidden)").count(): pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    for a in httpx.get(BASE + "/api/accounts", params={"q": theme}, auth=AUTH).json()["accounts"]:
        if theme in (a["handle"] or ""): httpx.delete(f"{BASE}/api/accounts/{a['id']}", auth=AUTH)
    pg.screenshot(path=str(OUT / f"{theme}_accounts_after.png"), full_page=True)
    # ---- contacts: search + import vcf + csv
    pg.goto(f"{BASE}/#/contacts"); pg.wait_for_selector("#ctable table")
    pg.set_input_files("input[type=file]", str(SAMPLES / "contacts.vcf")); pg.wait_for_timeout(1000)
    check(f"[{theme}] contacts vcf import toast", "Contacts:" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    pg.set_input_files("input[type=file]", str(SAMPLES / "contacts.csv")); pg.wait_for_timeout(1000)
    check(f"[{theme}] contacts csv import toast", "Contacts:" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    pg.fill("#cq", "a"); pg.wait_for_timeout(500); check(f"[{theme}] contacts search", "contacts" in pg.inner_text("#ctable"))
    # ---- connect: paste links, import modal
    pg.goto(f"{BASE}/#/connect"); pg.wait_for_selector("#paste")
    pg.fill("#paste", f"https://v.douyin.com/UIPASS{theme}/ 抖音测试 http://xhslink.com/UIPASS{theme}")
    pg.click("text=Import pasted links"); pg.wait_for_timeout(1000)
    check(f"[{theme}] paste links import", "Imported" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    pg.locator("button:has-text('Import…')").first.click(); pg.wait_for_selector("#ikind")
    pg.set_input_files("#ifile", str(SAMPLES / "instagram_saved_posts.json")); pg.click("#modalcard button:text-is('Import')"); pg.wait_for_timeout(1000)
    check(f"[{theme}] import modal file import", "Imported" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    # ---- settings save + sync
    pg.goto(f"{BASE}/#/settings"); pg.wait_for_selector("#s_prov")
    pg.click("text=Save settings"); pg.wait_for_timeout(800); check(f"[{theme}] settings save", "Settings saved" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    pg.click("button:has-text('Sync now')"); pg.wait_for_timeout(1200); check(f"[{theme}] settings sync now gives feedback", pg.inner_text("#toast").strip() != "")
    # ---- theme toggle
    pg.click("#theme"); pg.wait_for_timeout(200)
    check(f"[{theme}] theme toggle", pg.evaluate("document.documentElement.dataset.theme") != theme)
    # ---- sync panel on accounts
    pg.goto(f"{BASE}/#/accounts"); pg.wait_for_timeout(1500)
    check(f"[{theme}] accounts sync strip rendered", pg.locator("#syncbar").count() == 1 and pg.inner_text("#syncbar").strip() != "", pg.inner_text("#syncbar")[:100] if pg.locator("#syncbar").count() else "missing")
    ignorable = [f for f in fails if "/api/x/" in f]
    real_fails = [f for f in fails if f not in ignorable and "400 POST" not in f]   # 400 from the deliberate empty add-link
    check(f"[{theme}] no console errors / page errors", not errs, "\n  " + "\n  ".join(errs[:10] + ["responses: " + " | ".join(fails[:10])]))
    check(f"[{theme}] no failed network requests", not real_fails, "\n  " + "\n  ".join(real_fails[:10]))
    ctx.close()
    return errs, fails


with sync_playwright() as p:
    exe = os.environ.get("CHROME", "/usr/bin/google-chrome")
    b = p.chromium.launch(headless=True, executable_path=exe if os.path.exists(exe) else None, args=["--no-sandbox"])
    for theme in ("light", "dark"): run_theme(b, theme)
    b.close()
print(f"\n{sum(results)}/{len(results)} checks passed")
raise SystemExit(0 if all(results) else 1)
