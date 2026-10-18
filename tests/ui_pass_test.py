"""Headless browser pass over EVERY dashboard page in light AND dark mode, plus the main flows.
Collects console errors, page errors, failed requests / HTTP >= 400 responses and broken images (naturalWidth == 0).
Needs a running server on a THROWAWAY DB (it adds/edits/deletes items, accounts and contacts):
  SAVEHUB_DB=/tmp/x.db SAVEHUB_PORT=8766 python -m savehub.server &   then   SAVEHUB_URL=http://127.0.0.1:8766 python tests/ui_pass_test.py
Screenshots go to $SHOTS (default /tmp/savehub-ui-pass).
Covers: every page at 1366/1024/390 in both themes, no JS errors / broken images, AA contrast, cards clickable by mouse and keyboard,
the detail drawer (deep link, Esc, next/prev, gallery arrows/keys/dots, inline video, tag editor), badges, and every empty state
(produced with Playwright route mocks, so nothing real is deleted)."""
import os, pathlib, re, time
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


def a11y_problems(pg, scope=""):
    """Visible form fields / controls without an accessible name, and text below WCAG AA contrast
    (4.5:1, or 3:1 for large/bold text) against its first opaque background. Elements sitting on a
    gradient/image background (e.g. account avatars) are skipped since their colour can't be computed."""
    return pg.evaluate("""(SCOPE) => {
      const out = [];
      const nm = el => (el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder') || el.innerText || el.value || '').trim();
      for (const el of document.querySelectorAll(SCOPE ? `${SCOPE} button, ${SCOPE} a[href], ${SCOPE} [role=button]` : 'button, a[href], [role=button]')) {
        if (el.offsetParent && !nm(el) && !el.querySelector('img[alt]:not([alt=""]), svg[aria-label]')) out.push('unnamed control: ' + el.outerHTML.slice(0, 90)); }
      for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=file]), select, textarea')) {
        if (!el.offsetParent) continue;
        if (!(el.id && document.querySelector('label[for="' + el.id + '"]')) && !el.closest('label') && !el.getAttribute('aria-label') && !el.getAttribute('title') && !el.getAttribute('placeholder'))
          out.push('unlabeled field: ' + (el.id || el.outerHTML.slice(0, 60))); }
      const lum = c => { const m = c.match(/[\\d.]+/g); if (!m) return null; const k = c.startsWith('color(') ? 1 : 255; const [r, g, b] = m.slice(0, 3).map(v => { v = v / k; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
      const bgOf = el => { while (el) { const cs = getComputedStyle(el); if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
          const m = cs.backgroundColor.match(/[\\d.]+/g); if (m && (m.length < 4 || +m[3] > 0.5)) return cs.backgroundColor; el = el.parentElement; } return 'rgb(255,255,255)'; };
      const seen = new Set();
      for (const el of document.querySelectorAll(SCOPE ? SCOPE + ' *' : 'header *, #view *, #modalcard *, #drawer *')) {
        if (!el.offsetParent) continue;
        const t = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(''); if (!t) continue;
        const cs = getComputedStyle(el); if (+cs.opacity < 0.99 || el.closest('[disabled], .muted')) continue;
        const bg = bgOf(el); if (!bg) continue; const f = lum(cs.color), b = lum(bg); if (f == null || b == null) continue;
        const ratio = (Math.max(f, b) + 0.05) / (Math.min(f, b) + 0.05), fs = parseFloat(cs.fontSize);
        const big = fs >= 18.66 || (fs >= 14 && +cs.fontWeight >= 700);
        const k = el.tagName + '.' + el.className + cs.color + bg;
        if (ratio < (big ? 3 : 4.5) && !seen.has(k)) { seen.add(k); out.push('low contrast ' + ratio.toFixed(2) + ': ' + el.tagName.toLowerCase() + '.' + el.className + ' "' + t.slice(0, 24) + '" ' + cs.color + ' on ' + bg); }
      }
      return out;
    }""", scope)


def a11y_problems_in(pg, scope):
    """a11y_problems() limited to one container (the drawer / an empty state)."""
    return [p for p in a11y_problems(pg, scope)]


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
        ap = a11y_problems(pg); check(f"[{theme}] {p}: every field/control has an accessible name and text meets AA contrast", not ap, "\n  " + "\n  ".join(ap[:8]))
        for w in (1366, 1024, 390):
            pg.set_viewport_size({"width": w, "height": 900}); pg.wait_for_timeout(150)
            ov = overflowing(pg); check(f"[{theme}] {p}: nothing overflows its panel at {w}px", not ov, str(ov[:5]))
            if p == "connect":
                wr = wrapped_card_heads(pg); check(f"[{theme}] connect: every card's enabled toggle stays on the badge line at {w}px", not wr, str(wr))
        pg.set_viewport_size({"width": 1366, "height": 900})
        pg.screenshot(path=str(OUT / f"{theme}_{p.replace('?', '_').replace('=', '')}.png"), full_page=True)
    # ---- library flows
    pg.goto(f"{BASE}/#/library"); pg.wait_for_selector("#results .card.item")
    for q, want in (("python", "python"), ("咖啡店", "咖啡"), ("咖啡", "咖啡"), ("nasi lemak", "nasi")):
        pg.fill("#q", q); pg.wait_for_timeout(700)
        txt = pg.inner_text("#results").lower()
        check(f"[{theme}] search {q!r} finds results", want.lower() in txt and pg.locator("#results .item").count() > 0, pg.inner_text("#count"))
    pg.fill("#q", "zzqqxx-nothing"); pg.wait_for_timeout(700)
    check(f"[{theme}] empty search shows the no-results state with tips + popular tags",
          pg.locator("#emptySearch .illu svg").count() == 1 and pg.locator("#emptySearch .tips li").count() >= 2 and pg.locator("#emptySearch .suggest .tag").count() > 0)
    pg.locator("#emptySearch .suggest .tag").first.click(); pg.wait_for_timeout(700)
    check(f"[{theme}] popular-tag suggestion clears the search and filters by tag", pg.locator("#results .item").count() > 0 and pg.locator("#tchips .tag.on").count() == 1 and pg.input_value("#q") == "")
    pg.locator("#tchips .tag.on").first.click(); pg.wait_for_timeout(600)
    for s in ("title", "platform", "author", "relevance", "saved_at"):
        pg.select_option("#sort", s); pg.wait_for_timeout(300)
        check(f"[{theme}] sort {s}", pg.locator("#results .item").count() > 0)
    pg.click("#order"); pg.wait_for_timeout(400); check(f"[{theme}] order toggle", "Asc" in pg.inner_text("#order"))
    pg.click("#order"); pg.wait_for_timeout(400)
    n_all = int(re.match(r"\d+", pg.inner_text("#count")).group())
    pg.locator("#pchips .chip").first.click(); pg.wait_for_timeout(500)
    n_f = int(re.match(r"\d+", pg.inner_text("#count")).group())
    check(f"[{theme}] platform filter narrows", 0 < n_f <= n_all, f"{n_f}/{n_all}")
    check(f"[{theme}] platform chips carry the platform logo", pg.locator("#pchips .chip .pmark img.plogo").count() >= 5)
    pg.locator("#pchips .chip").first.click(); pg.wait_for_timeout(500)
    pg.locator("#tchips .tag").first.click(); pg.wait_for_timeout(600)
    check(f"[{theme}] tag chip filter", pg.locator("#tchips .tag.on").count() == 1)
    pg.locator("#tchips .tag.on").first.click(); pg.wait_for_timeout(600)
    # clicking a tag inside a card toggles the tag filter (and does not open the card)
    pg.locator("#results .item .tag[data-tag]").first.click(); pg.wait_for_timeout(600)
    check(f"[{theme}] tag click in card filters", pg.locator("#tchips .tag.on").count() == 1 and pg.locator("#drawer.hidden").count() == 1)
    pg.locator("#tchips .tag.on").first.click(); pg.wait_for_timeout(600)
    # type filter: videos have a play badge; Files has no matches -> filter empty state -> Clear filters
    pg.click("#tseg [data-ty=video]"); pg.wait_for_timeout(600)
    check(f"[{theme}] type filter Videos shows only videos with a play icon",
          pg.locator("#results .item").count() > 0 and pg.locator("#results .item").count() == pg.locator("#results .item .cv-play").count())
    check(f"[{theme}] video durations are shown", pg.locator("#results .cv-badge.dur").count() > 0)
    pg.click("#tseg [data-ty=file]"); pg.wait_for_timeout(600)
    check(f"[{theme}] filter with no matches shows its empty state", pg.locator("#emptyFilter .illu svg").count() == 1 and "Clear filters" in pg.inner_text("#emptyFilter"))
    pg.click("#emptyFilter button:has-text('Clear filters')"); pg.wait_for_timeout(600)
    check(f"[{theme}] Clear filters restores the library", pg.locator("#results .item").count() == n_all, pg.inner_text("#count"))
    # cards: media, badges
    check(f"[{theme}] every card has a cover (image or generated)", pg.evaluate("[...document.querySelectorAll('#results .card.item')].every(c => c.querySelector('.cover .cover-img, .cover [data-gen]'))"))
    check(f"[{theme}] generated covers show the platform logo and title words", pg.locator("#results .gen .gen-logo").count() > 0 and pg.evaluate("[...document.querySelectorAll('#results .gen .gen-t')].every(t => t.textContent.trim().length > 2)"))
    check(f"[{theme}] multi-image badge shown", pg.locator("#results .cv-badge.cnt").count() > 0)
    check(f"[{theme}] account badges render with a >=14px platform logo", pg.evaluate("""(() => { const d = [...document.querySelectorAll('#results .abadge .pdot')]; return d.length > 0 && d.every(x => x.getBoundingClientRect().width >= 14 && x.querySelector('img.plogo')); })()"""))
    check(f"[{theme}] account badge tooltip names the full account", pg.evaluate("[...document.querySelectorAll('#results .abadge')].every(b => / — /.test(b.title))"))
    check(f"[{theme}] tag chips get distinct colours and +N overflow", pg.evaluate("new Set([...document.querySelectorAll('#results .tag[data-tag]')].map(t => getComputedStyle(t).backgroundColor)).size > 3") and pg.locator("#results .tag.more").count() > 0)
    # grid / list toggle
    pg.click("[data-lv=list]"); pg.wait_for_timeout(400); check(f"[{theme}] list view", pg.locator("#results .lrow.item").count() > 1)
    pg.set_viewport_size({"width": 390, "height": 844}); pg.wait_for_timeout(200)
    ov = overflowing(pg); check(f"[{theme}] library list view does not scroll the page sideways at 390px", not ov, str(ov[:5]))
    pg.set_viewport_size({"width": 1366, "height": 900}); pg.wait_for_timeout(150)
    pg.locator("#results .lrow.item").first.click(); pg.wait_for_selector("#drawer.show #dTitle")
    check(f"[{theme}] list row opens the detail view", pg.locator("#drawer.show").count() == 1); pg.keyboard.press("Escape"); pg.wait_for_timeout(350)
    pg.click("[data-lv=grid]"); pg.wait_for_timeout(500); check(f"[{theme}] grid view", pg.locator("#results .card.item").count() > 0)
    # whole card is clickable (mouse) and opens the drawer; Esc closes it and focus returns to the card
    first = pg.locator("#results .card.item").first; fid = first.get_attribute("data-id")
    first.locator(".ititle").click(); pg.wait_for_selector("#drawer.show #dTitle")
    check(f"[{theme}] mouse click on a card opens the detail drawer", pg.locator("#drawer.show").count() == 1)
    check(f"[{theme}] detail is deep-linked in the URL hash", pg.evaluate("location.hash") == f"#/library/item/{fid}", pg.evaluate("location.hash"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
    check(f"[{theme}] Esc closes the drawer", pg.locator("#drawer.hidden").count() == 1 and pg.evaluate("location.hash") in ("#/library", ""))
    check(f"[{theme}] focus returns to the card", pg.evaluate("document.activeElement && document.activeElement.dataset.id") == fid, pg.evaluate("document.activeElement.outerHTML.slice(0, 80)"))
    # keyboard: focus ring + Enter / Space open
    pg.focus(f"#results .card.item[data-id='{fid}']")
    ring = pg.evaluate("getComputedStyle(document.activeElement).boxShadow")
    check(f"[{theme}] focused card shows a focus ring", ring and ring != "none", ring)
    pg.keyboard.press("Enter"); pg.wait_for_selector("#drawer.show #dTitle"); check(f"[{theme}] Enter opens the card", pg.locator("#drawer.show").count() == 1)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
    pg.keyboard.press(" "); pg.wait_for_selector("#drawer.show #dTitle"); check(f"[{theme}] Space opens the card", pg.locator("#drawer.show").count() == 1)
    # next / prev
    h1 = pg.evaluate("location.hash"); pg.click("#dNext"); pg.wait_for_timeout(500); h2 = pg.evaluate("location.hash")
    check(f"[{theme}] next item", h1 != h2 and h2.startswith("#/library/item/") and pg.locator(f"#results .item.current[data-id='{h2.rsplit('/', 1)[1]}']").count() == 1, f"{h1} -> {h2}")
    pg.keyboard.press("k"); pg.wait_for_timeout(500)
    check(f"[{theme}] previous item via keyboard (K)", pg.evaluate("location.hash") == h1)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
    # gallery: open a multi-image item, arrows + keyboard + dots
    pg.locator("#results .card.item:has(.cv-badge.cnt)").first.click(); pg.wait_for_selector("#gallery.many")
    n_img = pg.locator("#gallery .gslide").count()
    pg.click("#gNext"); pg.wait_for_timeout(450); check(f"[{theme}] gallery next arrow", pg.inner_text("#gCount").startswith("2 /"), pg.inner_text("#gCount"))
    pg.focus("#gallery"); pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(450)
    check(f"[{theme}] gallery ArrowRight", pg.inner_text("#gCount").startswith(("3 /" if n_img >= 3 else "2 /")), pg.inner_text("#gCount"))
    pg.keyboard.press("ArrowLeft"); pg.wait_for_timeout(450); check(f"[{theme}] gallery ArrowLeft", pg.inner_text("#gCount").startswith("2 /"))
    pg.click("#gallery .gdot >> nth=0"); pg.wait_for_timeout(450); check(f"[{theme}] gallery dots", pg.inner_text("#gCount").startswith("1 /"))
    check(f"[{theme}] gallery images load", pg.evaluate("[...document.querySelectorAll('#gallery .g-img')].every(i => i.complete ? i.naturalWidth > 0 : true)"))
    check(f"[{theme}] drawer shows tags, account badge, saved date and original link",
          pg.locator("#drawer .dmeta .abadge .pdot img").count() == 1 and "Saved" in pg.inner_text("#drawer .dmeta") and pg.locator("#drawer .durl a").count() == 1)
    ap = a11y_problems_in(pg, "#drawer"); check(f"[{theme}] drawer controls named + AA contrast", not ap, "\n  " + "\n  ".join(ap[:8]))
    for w in (1024, 390):
        pg.set_viewport_size({"width": w, "height": 900 if w > 400 else 844}); pg.wait_for_timeout(250)
        ov = pg.evaluate("(() => { const p = document.querySelector('.drawer-panel'); return p.scrollWidth - p.clientWidth; })()")
        check(f"[{theme}] drawer has no sideways scroll at {w}px", ov <= 1, str(ov))
        pg.screenshot(path=str(OUT / f"{theme}_drawer_{w}.png"))
    pg.set_viewport_size({"width": 1366, "height": 900})
    pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
    # inline video player when a direct video exists
    pg.goto(f"{BASE}/#/library?q=cat"); pg.wait_for_selector("#results .card.item"); pg.locator("#results .card.item").first.click()
    pg.wait_for_selector("#drawer.show #dTitle"); pg.wait_for_timeout(600)
    check(f"[{theme}] inline video player for direct video", pg.locator("#drawer video#dVideo").count() == 1)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
    pg.fill("#q", ""); pg.wait_for_timeout(500)
    # deep link straight to an item (fresh load)
    pg.goto(f"{BASE}/#/library/item/{fid}"); pg.wait_for_selector("#drawer.show #dTitle")
    check(f"[{theme}] deep link opens the item", pg.locator("#drawer.show").count() == 1 and pg.locator("#results .card.item").count() > 0)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
    check(f"[{theme}] closing a deep-linked item lands on the library", pg.evaluate("location.hash") == "#/library" and pg.locator("#results .card.item").count() > 0, pg.evaluate("location.hash"))
    pg.select_option("#acct", index=1); pg.wait_for_timeout(500); check(f"[{theme}] account filter", pg.locator("#count").inner_text() != "")
    pg.select_option("#acct", index=0); pg.wait_for_timeout(400)
    # add link: empty URL must give feedback, valid URL saves
    pg.click("#addLink"); pg.wait_for_selector("#nurl")
    pg.click("text=Save & auto-tag"); pg.wait_for_timeout(500)
    check(f"[{theme}] add link without URL shows an error toast", pg.locator("#toast.show").count() == 1 and pg.inner_text("#toast").strip() != "", pg.inner_text("#toast"))
    url = f"https://example.com/ui-pass-{theme}"
    pg.fill("#nurl", url); pg.fill("#ntitle", f"UI pass {theme} 中文测试 rock'n'roll"); pg.click("text=Save & auto-tag"); pg.wait_for_timeout(900)
    pg.fill("#q", f"UI pass {theme}"); pg.wait_for_timeout(800)
    check(f"[{theme}] added link is searchable", f"UI pass {theme}" in pg.inner_text("#results"))
    check(f"[{theme}] item without media gets a generated cover", pg.locator("#results .card.item .gen").count() >= 1)
    pg.locator("#results .card.item").first.click(); pg.wait_for_selector("#tagEditBtn")
    pg.click("#tagEditBtn"); pg.wait_for_selector("#tagInput")
    n0 = pg.locator("#tagEdit .tag").count(); have = set(pg.eval_on_selector_all("#tagEdit .tag", "els => els.map(e => e.dataset.tag)"))
    new_tags = [f"uipass{int(time.time()) % 100000}", f"it's-{theme}"]
    pg.fill("#tagInput", new_tags[0]); pg.keyboard.press("Enter"); pg.fill("#tagInput", new_tags[1]); pg.keyboard.press(",")
    check(f"[{theme}] tag editor adds chips with Enter / comma", pg.locator("#tagEdit .tag").count() == n0 + len([t for t in new_tags if t not in have]))
    n1 = pg.locator("#tagEdit .tag").count(); pg.locator("#tagEdit .tag-x").first.click()
    check(f"[{theme}] removable chip in edit mode", pg.locator("#tagEdit .tag").count() == n1 - 1)
    pg.fill("#tagInput", new_tags[1]); pg.keyboard.press("Enter")
    pg.click("#tagSave"); pg.wait_for_timeout(800)
    saved = httpx.get(BASE + "/api/items", params={"q": f"UI pass {theme}"}, auth=AUTH).json()["items"]
    check(f"[{theme}] edit tags saved", f"it's-{theme}" in pg.inner_text("#drawer .dright") and saved and f"it's-{theme}" in saved[0]["tags"], str(saved[:1]))
    check(f"[{theme}] card shows at most 3 tags plus +N", pg.locator("#results .card.item").first.locator(".tag[data-tag]").count() <= 3 and pg.locator("#results .card.item").first.locator(".tag.more").count() == 1)
    pg.locator("#drawer .tag[data-dtag]", has_text=f"it's-{theme}").first.click(); pg.wait_for_timeout(800)
    check(f"[{theme}] tag with apostrophe is clickable (drawer tag -> library filter)", pg.locator("#drawer.hidden").count() == 1 and pg.locator("#tchips .tag.on").count() == 1 and pg.locator("#results .card.item").count() >= 1, pg.inner_text("#count"))
    pg.locator("#tchips .tag.on").first.click(); pg.wait_for_timeout(600)
    pg.fill("#q", f"UI pass {theme}"); pg.wait_for_timeout(800)
    pg.locator("#results .card.item").first.click(); pg.wait_for_selector("#dDel")
    pg.click("#dDel"); pg.wait_for_selector("#cfYes"); pg.click("#cfYes"); pg.wait_for_timeout(900)
    check(f"[{theme}] item deleted", pg.locator("#emptySearch").count() == 1 and pg.locator("#drawer.hidden").count() == 1)
    pg.fill("#q", ""); pg.wait_for_timeout(500)
    # ---- summary chips
    pg.goto(f"{BASE}/#/summary"); pg.wait_for_selector(".kpi")
    pg.click("a.chip:has-text('14 days')"); pg.wait_for_timeout(800); check(f"[{theme}] summary 14 days", "14 days" in pg.inner_text(".pagehead"))
    check(f"[{theme}] summary charts render (per day, by app with logos, by type donut)", pg.locator(".colchart .cc-col").count() == 14 and pg.locator(".hbar .pmark img").count() > 0 and pg.locator(".donut .dn-seg").count() > 0)
    pg.locator(".recent a.rcard").first.click(); pg.wait_for_selector("#drawer.show #dTitle")
    check(f"[{theme}] recent save on the summary opens its detail", pg.evaluate("location.hash").startswith("#/library/item/"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
    # ---- accounts: add (single), quick-edit, paste handles, delete
    pg.goto(f"{BASE}/#/accounts"); pg.wait_for_selector(".agroup, .acard, table", timeout=10000)
    check(f"[{theme}] account avatars carry a >=14px platform logo", pg.evaluate("[...document.querySelectorAll('.acard .avatar .pdot')].every(x => x.getBoundingClientRect().width >= 14)"))
    pg.fill("#aq", "zzqqxx-none"); pg.wait_for_timeout(500)
    check(f"[{theme}] accounts no-match empty state", pg.locator("#emptyAcctMatch .illu svg").count() == 1)
    pg.click("#emptyAcctMatch button:has-text('Clear filters')"); pg.wait_for_timeout(600)
    check(f"[{theme}] clear filters restores accounts", pg.locator(".acard").count() > 0)
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
    pg.goto(f"{BASE}/#/contacts"); pg.wait_for_selector("#ctable .ccard")
    check(f"[{theme}] contact cards have avatar, platform and quick actions", pg.locator(".ccard .pav").count() > 0 and pg.locator(".ccard a.qa[href^='tel:']").count() > 0 and pg.locator(".ccard a.qa[href^='mailto:']").count() > 0)
    pg.fill("#cq", "zzqqxx-nobody"); pg.wait_for_timeout(500)
    check(f"[{theme}] contacts no-match empty state", pg.locator("#emptyContactsSearch .illu svg").count() == 1)
    pg.click("#emptyContactsSearch button:has-text('Clear search')"); pg.wait_for_timeout(500)
    check(f"[{theme}] clear search restores contacts", pg.locator(".ccard").count() > 0)
    pg.set_input_files("input[type=file]", str(SAMPLES / "contacts.vcf")); pg.wait_for_timeout(1000)
    check(f"[{theme}] contacts vcf import toast", "Contacts:" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    pg.set_input_files("input[type=file]", str(SAMPLES / "contacts.csv")); pg.wait_for_timeout(1000)
    check(f"[{theme}] contacts csv import toast", "Contacts:" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    pg.fill("#cq", "a"); pg.wait_for_timeout(500); check(f"[{theme}] contacts search", pg.locator(".ccard").count() > 0 and "contact" in pg.inner_text("#csub"))
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
    ignorable += [f for f in fails if ".mp4" in f and "ERR_ABORTED" in f]   # a video preload cancelled when the drawer closes
    real_fails = [f for f in fails if f not in ignorable and "400 POST" not in f]   # 400 from the deliberate empty add-link
    check(f"[{theme}] no console errors / page errors", not errs, "\n  " + "\n  ".join(errs[:10] + ["responses: " + " | ".join(fails[:10])]))
    check(f"[{theme}] no failed network requests", not real_fails, "\n  " + "\n  ".join(real_fails[:10]))
    ctx.close()
    return errs, fails


EMPTY_SUMMARY = {"days": 7, "since": "2026-01-01T00:00:00+00:00", "total": 0, "by_platform": {}, "top_tags": [], "top_authors": [], "by_day": {}, "by_type": {}, "digest": "", "digest_by": "local", "recent": []}
NEEDS_LOGIN = {"available": True, "enabled": True, "interval_minutes": 10, "loop": {"running": True, "pid": 1, "where": "box"}, "backends": {},
               "accounts": [{"platform": "tiktok", "account": "@js.clips.sample", "account_id": None, "state": "needs_login",
                             "fix": "python -m savehub.collector login tiktok --account @js.clips.sample"}]}


def empty_ok(pg, theme, sel, what, action_text):
    """An empty state: illustration, headline, one helpful line, a primary action, entrance animation, AA."""
    el = pg.locator(sel)
    check(f"[{theme}] {what}: empty state shown", el.count() == 1)
    if not el.count(): return
    check(f"[{theme}] {what}: illustration + headline + helpful line", el.locator(".illu svg").count() == 1 and el.locator("h3").inner_text().strip() != "" and el.locator("p.lead").inner_text().strip() != "")
    check(f"[{theme}] {what}: primary action '{action_text}'", el.locator(".eacts .btn.big", has_text=action_text).count() == 1, el.locator(".eacts").inner_text() if el.locator(".eacts").count() else "no actions")
    check(f"[{theme}] {what}: entrance animation", pg.evaluate(f"getComputedStyle(document.querySelector('{sel}')).animationName") == "fadeup")
    check(f"[{theme}] {what}: illustration follows the theme", pg.evaluate(f"getComputedStyle(document.querySelector('{sel} .i-card')).fill") != "rgb(0, 0, 0)")
    ap = a11y_problems_in(pg, sel); check(f"[{theme}] {what}: AA contrast + named controls", not ap, "\n  " + "\n  ".join(ap[:6]))


def run_empty_states(b, theme):
    """Every empty state, produced with route mocks (nothing is deleted): empty library, no search results, filter with no
    matches, app not connected, no accounts, no contacts, empty summary, needs login."""
    ctx = b.new_context(viewport={"width": 1366, "height": 900}, http_credentials={"username": AUTH[0], "password": AUTH[1]} if AUTH else None)
    ctx.add_init_script(f"localStorage.theme='{theme}';")
    pg = ctx.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    pg.on("console", lambda m: m.type == "error" and errs.append(f"console: {m.text}"))
    shot = lambda n: pg.screenshot(path=str(OUT / f"{theme}_empty_{n}.png"))
    # real data: no search results, filter with no matches, app not connected
    pg.goto(f"{BASE}/#/library"); pg.wait_for_selector("#results .card.item")
    pg.fill("#q", "zzqqxx-nothing"); pg.wait_for_timeout(700); empty_ok(pg, theme, "#emptySearch", "no search results", "Clear search"); shot("search")
    pg.click("#emptySearch button:has-text('Clear search')"); pg.wait_for_timeout(700)
    check(f"[{theme}] Clear search action works", pg.locator("#results .card.item").count() > 0)
    pg.click("#tseg [data-ty=file]"); pg.wait_for_timeout(600); empty_ok(pg, theme, "#emptyFilter", "filter with no matches", "Clear filters"); shot("filter")
    pg.click("#emptyFilter button:has-text('Clear filters')"); pg.wait_for_timeout(600)
    zero = pg.locator("#pchips .chip:has(.n:text-is('0'))")
    if zero.count():
        zero.first.click(); pg.wait_for_timeout(600); empty_ok(pg, theme, "#emptyPlatform", "app not connected", "Connect"); shot("platform")
        pg.click("#emptyPlatform a:has-text('Connect')"); pg.wait_for_timeout(700)
        check(f"[{theme}] Connect action goes to Connect apps", pg.evaluate("location.hash") == "#/connect")
    # mocked: empty everything
    pg.route(re.compile(r"/api/items\?"), lambda r: r.fulfill(json={"total": 0, "items": []}))
    pg.route(re.compile(r"/api/accounts\?"), lambda r: r.fulfill(json={"total": 0, "accounts": []}))
    pg.route(re.compile(r"/api/contacts\?"), lambda r: r.fulfill(json={"total": 0, "contacts": []}))
    pg.route(re.compile(r"/api/summary\?"), lambda r: r.fulfill(json=EMPTY_SUMMARY))
    pg.goto(f"{BASE}/#/library"); pg.reload(); pg.wait_for_selector("#emptyLibrary"); pg.wait_for_timeout(500)   # reload: fresh filter state
    empty_ok(pg, theme, "#emptyLibrary", "empty library", "Connect an app"); shot("library")
    pg.click("#emptyLibrary button:has-text('Add a link')"); pg.wait_for_selector("#nurl"); check(f"[{theme}] empty library 'Add a link' opens the form", True); pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    pg.goto(f"{BASE}/#/accounts"); pg.wait_for_selector("#emptyAccounts"); pg.wait_for_timeout(500)
    empty_ok(pg, theme, "#emptyAccounts", "no accounts", "Add your first account"); shot("accounts")
    pg.click("#emptyAccounts button:has-text('Add your first account')"); pg.wait_for_selector(".ptile")
    check(f"[{theme}] 'Add your first account' opens the add-account dialog", pg.locator(".ptile").count() > 5); pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    pg.goto(f"{BASE}/#/contacts"); pg.wait_for_selector("#emptyContacts"); pg.wait_for_timeout(500)
    empty_ok(pg, theme, "#emptyContacts", "no contacts", "Import contacts"); shot("contacts")
    pg.goto(f"{BASE}/#/summary"); pg.wait_for_selector("#emptySummary"); pg.wait_for_timeout(500)
    empty_ok(pg, theme, "#emptySummary", "empty summary", "Show 30 days"); shot("summary")
    pg.click("#emptySummary button:has-text('Show 30 days')"); pg.wait_for_timeout(700)
    check(f"[{theme}] 'Show 30 days' switches the range", "days=30" in pg.evaluate("location.hash"))
    pg.unroute(re.compile(r"/api/accounts\?"))
    # needs login (accounts banner + connect card + sign-in sheet)
    pg.route(re.compile(r"/api/autosync$"), lambda r: r.fulfill(json=NEEDS_LOGIN) if r.request.method == "GET" else r.continue_())
    pg.goto(f"{BASE}/#/accounts"); pg.wait_for_selector("#needsLogin"); pg.wait_for_timeout(400)
    nl = pg.locator("#needsLogin")
    check(f"[{theme}] needs login: illustration + message + 'Sign in to TikTok'", nl.locator("svg.illu-svg").count() == 1 and nl.locator("button", has_text="Sign in to TikTok").count() >= 1)
    ap = a11y_problems_in(pg, "#needsLogin"); check(f"[{theme}] needs login: AA contrast", not ap, "\n  " + "\n  ".join(ap[:6]))
    shot("needs_login")
    nl.locator("button", has_text="Sign in to TikTok").first.click(); pg.wait_for_selector("#siT")
    check(f"[{theme}] sign-in sheet shows the exact login command", "python -m savehub.collector login tiktok --account @js.clips.sample" in pg.inner_text("#modalcard"))
    shot("sign_in"); pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    pg.goto(f"{BASE}/#/connect"); pg.wait_for_selector(".ccon"); pg.wait_for_timeout(400)
    check(f"[{theme}] connect: needs-login card", pg.locator(".ccon[data-p=tiktok] .needlogin button", has_text="Sign in to TikTok").count() == 1)
    check(f"[{theme}] connect: apps without data say 'Not connected yet'", pg.locator(".ccon .cstat.none").count() >= 1 or pg.locator(".ccon").count() == pg.locator(".ccon.is-on").count())
    pg.locator(".ccon[data-p=tiktok]").scroll_into_view_if_needed(); shot("connect_needs_login")
    for w in (1024, 390):
        pg.set_viewport_size({"width": w, "height": 900 if w > 400 else 844}); pg.goto(f"{BASE}/#/library"); pg.wait_for_selector("#emptyLibrary"); pg.wait_for_timeout(400)
        ov = overflowing(pg); check(f"[{theme}] empty library fits at {w}px", not ov, str(ov[:3])); shot(f"library_{w}")
    check(f"[{theme}] empty states: no JS errors", not errs, "\n  ".join(errs[:6]))
    ctx.close()


with sync_playwright() as p:
    exe = os.environ.get("CHROME", "/usr/bin/google-chrome")
    b = p.chromium.launch(headless=True, executable_path=exe if os.path.exists(exe) else None, args=["--no-sandbox"])
    only = os.environ.get("UI_ONLY", "")       # "empty" -> just the empty-state pass (quick iteration)
    for theme in ("light", "dark"):
        if only != "empty": run_theme(b, theme)
        run_empty_states(b, theme)
    b.close()
print(f"\n{sum(results)}/{len(results)} checks passed")
raise SystemExit(0 if all(results) else 1)
