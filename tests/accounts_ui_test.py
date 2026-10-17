"""Headless UI test of the Accounts experience + screenshots. Needs a running server (default http://127.0.0.1:8900).
Run: PORT=8900 SAVEHUB_DB=/tmp/x.db python -m savehub.server &  then  python tests/accounts_ui_test.py"""
import json, os, pathlib, httpx
from playwright.sync_api import sync_playwright, expect
BASE = os.environ.get("SAVEHUB_URL", "http://127.0.0.1:8900")
OUT = pathlib.Path(__file__).resolve().parent.parent / "screenshots"; OUT.mkdir(exist_ok=True)
DASH_PW = os.environ.get("SAVEHUB_DASHBOARD_PASSWORD", "")
AUTH = ("savehub", DASH_PW) if DASH_PW else None
api = lambda p, **kw: httpx.get(BASE + p, params=kw, auth=AUTH).json()
results = []
def check(name, ok, detail=""):
    results.append(ok); print(("PASS" if ok else "FAIL"), name, detail)
def shot(pg, name, full=False):
    pg.wait_for_timeout(350); pg.screenshot(path=str(OUT / f"{name}.png"), full_page=full); print("saved", OUT / f"{name}.png")

with sync_playwright() as p:
    exe = os.environ.get("CHROME", "/usr/bin/google-chrome")
    b = p.chromium.launch(executable_path=exe if os.path.exists(exe) else None, args=["--no-sandbox"])
    def new_page(**kw):
        ctx = b.new_context(http_credentials={"username": AUTH[0], "password": AUTH[1]} if AUTH else None, permissions=["clipboard-read", "clipboard-write"], **kw)
        return ctx.new_page()
    pg = new_page(viewport={"width": 1366, "height": 900})
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.add_init_script("localStorage.theme='dark'; localStorage.acctView='grid';")
    pg.goto(BASE + "/#/accounts"); pg.wait_for_selector(".agroup")
    n0 = api("/api/accounts")["total"]
    check("overview renders grouped sections", pg.locator(".agroup").count() >= 3, f"{pg.locator('.agroup').count()} groups, {n0} accounts")
    shot(pg, "08_accounts_overview")

    # --- add flow: step 1 platform grid
    pg.click("#addAcct"); pg.wait_for_selector(".ptile")
    n_apps = len(api("/api/platforms"))
    check(f"platform grid shows all {n_apps} apps", pg.locator(".ptile").count() == n_apps, str(pg.locator(".ptile").count()))
    check("first tile focused", pg.evaluate("document.activeElement.classList.contains('ptile')"))
    shot(pg, "09_add_account_pick_platform")
    pg.keyboard.press("ArrowRight"); pg.keyboard.press("ArrowRight")   # x -> threads -> instagram
    focused = pg.evaluate("document.activeElement.dataset.p"); pg.keyboard.press("Enter")
    pg.wait_for_selector("#f_handle")
    check("arrow keys + Enter pick a platform", focused == "instagram", focused)
    check("handle field focused", pg.evaluate("document.activeElement.id") == "f_handle")
    # validation: empty save
    pg.keyboard.press("Enter")
    check("inline validation on empty save", "handle or a display name" in pg.text_content("#e_handle"))
    pg.fill("#f_handle", "@js.ui.one"); pg.fill("#f_display_name", "UI One"); pg.fill("#f_linked_email", "bad-email")
    pg.click("#f_tags"); pg.keyboard.press("Enter")
    check("email validation", "doesn’t look right" in pg.text_content("#e_linked_email"))
    pg.fill("#f_linked_email", "js@example.com"); pg.fill("#f_tags", "ui, test")
    check("auto profile URL preview", "instagram.com/js.ui.one" in pg.text_content("#urlprev"))
    shot(pg, "10_add_account_form")
    pg.focus("#f_handle"); pg.keyboard.press("Enter"); pg.wait_for_selector(".achip")
    a = api("/api/accounts", q="js.ui.one")["accounts"]
    check("Enter saves via POST /api/accounts", len(a) == 1 and a[0]["handle"] == "js.ui.one" and a[0]["profile_url"] == "https://www.instagram.com/js.ui.one/" and a[0]["status"] == "active", str(a[:1]))
    check("modal stays open on same platform", pg.is_visible("#f_handle") and "Instagram" in pg.text_content("#amT"))
    pg.fill("#f_handle", "js.ui.two"); pg.keyboard.press("Enter"); pg.wait_for_timeout(400)
    pg.fill("#f_handle", "js.ui.one"); pg.keyboard.press("Enter"); pg.wait_for_timeout(300)
    check("duplicate blocked inline", "already added" in pg.text_content("#e_handle"))
    check("running count shows 2", "2 added just now" in pg.text_content("#amAdded"))
    check("sticky tags carried to next", pg.input_value("#f_tags") == "ui, test")
    shot(pg, "11_add_another_running_count")
    # pasted URL into handle splits
    pg.fill("#f_handle", "https://www.instagram.com/js.ui.url/"); pg.keyboard.press("Tab")
    check("pasting a profile link fills handle + URL", pg.input_value("#f_handle") == "js.ui.url" and "js.ui.url" in pg.input_value("#f_profile_url"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    check("Esc closes modal", not pg.is_visible("#modalcard"))

    # --- bulk paste
    pg.click("#pasteAcct"); pg.wait_for_selector("#btext")
    pg.select_option("#bp", "tiktok")
    pg.fill("#btext", "@js.bulk.a\njs.bulk.b, Bulk B\nxhs js_red_bulk 小红书号\ninstagram @js.ui.one\nhttps://x.com/js_bulk_x")
    pg.wait_for_selector(".bprev li"); pg.wait_for_timeout(300)
    check("bulk preview counts ready/dupes", "4 ready to add" in pg.text_content(".bprev-h") and "1 already exist" in pg.text_content(".bprev-h"), pg.text_content(".bprev-h"))
    shot(pg, "12_bulk_paste")
    pg.click("#bgo"); pg.wait_for_timeout(700)
    q = {a["handle"]: a["platform"] for a in api("/api/accounts", q="bulk", limit=50)["accounts"]}
    check("bulk paste created 4 across platforms", q == {"js.bulk.a": "tiktok", "js.bulk.b": "tiktok", "js_red_bulk": "xiaohongshu", "js_bulk_x": "x"}, str(q))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(500)

    # --- search / filter / sort
    pg.fill("#aq", "bulk"); pg.wait_for_timeout(600)
    check("search filters cards", pg.locator(".acard").count() == 4, str(pg.locator(".acard").count()))
    pg.click('.pseg[data-p="tiktok"]'); pg.wait_for_timeout(200)
    check("platform segment filter", pg.locator(".acard").count() == 2)
    pg.click('.pseg[data-p=""]'); pg.fill("#aq", ""); pg.wait_for_timeout(600)
    pg.click('#sseg [data-s="dormant"]'); pg.wait_for_timeout(200)
    dorm = api("/api/accounts", status="dormant")["total"]
    check("status filter", pg.locator(".acard").count() == dorm and dorm > 0, f"{dorm} dormant")
    pg.click('#sseg [data-s=""]'); pg.select_option("#asort", "display_name"); pg.click('[data-v="list"]'); pg.wait_for_timeout(500)
    names = pg.eval_on_selector_all(".atable tbody tr td:nth-child(2) b", "els => els.map(e => e.textContent.toLowerCase())")
    api_names = [(a["display_name"] or "").lower() for a in api("/api/accounts", sort="display_name", limit=500)["accounts"]]
    check("sort by name (table view) matches API order", len(names) == len(api_names) and names[0] <= names[-1], f"{len(names)} rows")
    pg.click('[data-v="grid"]'); pg.select_option("#asort", "platform"); pg.wait_for_timeout(400)

    # --- quick edit
    aid = api("/api/accounts", q="js.bulk.b")["accounts"][0]["id"]
    pg.click(f'[data-act="qedit"][data-id="{aid}"]'); pg.wait_for_selector("form.qedit")
    pg.fill('form.qedit input[name="display_name"]', "Bulk B renamed"); pg.select_option('form.qedit select[name="status"]', "connected")
    shot(pg, "14_quick_edit")
    pg.focus('form.qedit input[name="display_name"]'); pg.keyboard.press("Enter"); pg.wait_for_timeout(600)
    a = api("/api/accounts", q="js.bulk.b")["accounts"][0]
    check("inline quick-edit saves via PUT", a["display_name"] == "Bulk B renamed" and a["status"] == "connected")
    check("toast shown", "Saved" in pg.text_content("#toast"))

    # --- delete with confirmation
    pg.click(f'[data-act="del"][data-id="{aid}"]'); pg.wait_for_selector("#cfYes")
    shot(pg, "15_delete_confirm")
    pg.click("#cfNo"); pg.wait_for_timeout(300)
    check("cancel keeps account", api("/api/accounts", q="js.bulk.b")["total"] == 1)
    pg.click(f'[data-act="del"][data-id="{aid}"]'); pg.wait_for_selector("#cfYes"); pg.click("#cfYes"); pg.wait_for_timeout(600)
    check("confirm deletes account", api("/api/accounts", q="js.bulk.b")["total"] == 0)
    check("undo action offered", pg.locator("#toast .tact").count() == 1)
    pg.click("#toast .tact"); pg.wait_for_timeout(600)
    check("undo restores", api("/api/accounts", q="js.bulk.b")["total"] == 1)

    # --- full edit modal via 'All fields…'
    aid = api("/api/accounts", q="js.ui.two")["accounts"][0]["id"]
    pg.click(f'[data-act="qedit"][data-id="{aid}"]'); pg.click('[data-act="fulledit"]'); pg.wait_for_selector("#f_handle")
    pg.fill("#f_notes", "edited in full modal"); pg.click("#amSave"); pg.wait_for_timeout(600)
    check("full edit modal saves", api("/api/accounts", q="js.ui.two")["accounts"][0]["notes"] == "edited in full modal")
    check("no plaintext password field on the page (only the masked vault field in the form)", pg.locator('input[type=password]:visible').count() == 0 and pg.locator('[name*=pass]').count() == 0)

    # --- encrypted password vault
    vs = api("/api/vault/status")
    if not vs["enabled"]:
        check("vault enabled for UI test (set SAVEHUB_VAULT_KEY + SAVEHUB_DASHBOARD_PASSWORD)", False, vs["reason"])
    else:
        PW = "UI-Vault-pässwörd-42!"
        pg.click("#addAcct"); pg.wait_for_selector(".ptile"); pg.click('.ptile[data-p="facebook"]'); pg.wait_for_selector("#f_password")
        check("password field is masked (type=password, autocomplete=new-password)", pg.get_attribute("#f_password", "type") == "password" and pg.get_attribute("#f_password", "autocomplete") == "new-password")
        check("encrypted-at-rest helper text", "Encrypted at rest" in pg.text_content("#h_password"))
        check("free-tier persistence warning visible in form", pg.locator(".vfield .vwarn").count() == 1 and "persistent disk" in pg.text_content(".vfield .vwarn"))
        pg.fill("#f_handle", "js.vault.ui"); pg.fill("#f_display_name", "Vault UI"); pg.fill("#f_password", PW)
        pg.click("#pwEye")
        check("eye toggle shows password", pg.get_attribute("#f_password", "type") == "text" and pg.get_attribute("#pwEye", "aria-pressed") == "true")
        pg.click("#pwEye"); check("eye toggle hides again", pg.get_attribute("#f_password", "type") == "password")
        pg.evaluate("document.querySelector('.modal-card').scrollTop = 200")
        shot(pg, "19_vault_password_field")
        reqs = []; pg.on("request", lambda r: reqs.append((r.method, r.url, r.post_data or "")))
        pg.click("#amSaveClose"); pg.wait_for_timeout(900)
        a = api("/api/accounts", q="js.vault.ui")["accounts"][0]
        check("account saved with has_password", a["has_password"] is True and "password" not in json.dumps({k: v for k, v in a.items() if k not in ("has_password", "password_updated_at")}))
        create = [r for r in reqs if r[0] == "POST" and r[1].endswith("/api/accounts")]
        check("plaintext not sent to the generic accounts endpoint", create and PW not in create[0][2])
        check("password sent only to the vault endpoint", any(r[0] == "PUT" and r[1].endswith(f"/api/accounts/{a['id']}/secret") for r in reqs))
        r = httpx.post(f"{BASE}/api/accounts/{a['id']}/secret/reveal", json={}); check("reveal without login refused", r.status_code == 401, str(r.status_code))
        pg.fill("#aq", "js.vault.ui"); pg.wait_for_timeout(600)
        row = pg.locator(f'.pwrow[data-pw="{a["id"]}"]')
        check("card shows masked password", row.count() == 1 and row.locator(".pwval").text_content() == "••••••••")
        check("vault strip on accounts page", "Password vault on" in pg.text_content("#vaultbar") and "persistent disk" in pg.text_content("#vaultbar"))
        shot(pg, "20a_vault_masked")
        row.locator('[data-act="reveal"]').click(); pg.wait_for_timeout(500)
        check("Reveal shows decrypted password", row.locator(".pwval").text_content() == PW)
        shot(pg, "20_vault_reveal")
        row.locator('[data-act="copypw"]').click(); pg.wait_for_timeout(500)
        check("Copy puts password on clipboard", pg.evaluate("navigator.clipboard.readText()") == PW)
        row.locator('[data-act="reveal"]').click(); check("Hide re-masks", row.locator(".pwval").text_content() == "••••••••")
        # edit: replace, then remove
        pg.click(f'[data-act="qedit"][data-id="{a["id"]}"]'); pg.click('[data-act="fulledit"]'); pg.wait_for_selector("#f_password")
        check("edit form says a password is saved", "saved" in pg.get_attribute("#f_password", "placeholder") and pg.locator("#f_pw_remove").count() == 1)
        pg.fill("#f_password", "replaced-pw"); pg.click("#amSave"); pg.wait_for_timeout(700)
        check("replace password via edit", httpx.post(f"{BASE}/api/accounts/{a['id']}/secret/reveal", json={}, auth=AUTH).json()["password"] == "replaced-pw")
        pg.click(f'[data-act="qedit"][data-id="{a["id"]}"]'); pg.click('[data-act="fulledit"]'); pg.wait_for_selector("#f_pw_remove")
        pg.check("#f_pw_remove"); pg.click("#amSave"); pg.wait_for_timeout(700)
        check("remove password via edit", api("/api/accounts", q="js.vault.ui")["accounts"][0]["has_password"] is False)
        pg.fill("#aq", ""); pg.wait_for_timeout(400)

    po = new_page(viewport={"width": 1366, "height": 900}); po.add_init_script("localStorage.theme='dark'; localStorage.acctView='grid';")
    po.goto(BASE + "/#/accounts"); po.wait_for_selector(".agroup"); shot(po, "08_accounts_overview")
    shot(po, "08b_accounts_overview_full", full=True)
    # light theme + mobile
    pl = new_page(viewport={"width": 1366, "height": 900}); pl.add_init_script("localStorage.theme='light'; localStorage.acctView='list';")
    pl.goto(BASE + "/#/accounts"); pl.wait_for_selector(".atable"); shot(pl, "13_accounts_table_light")
    pm = new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True); pm.add_init_script("localStorage.theme='dark'; localStorage.acctView='grid';")
    pm.goto(BASE + "/#/accounts"); pm.wait_for_selector(".agroup"); shot(pm, "16_accounts_mobile")
    pm.click("#addAcct"); pm.wait_for_selector(".ptile"); shot(pm, "17_add_account_mobile")
    check("no JS errors", not errors, "; ".join(errors))
    b.close()
print(f"\n{sum(results)}/{len(results)} checks passed")
raise SystemExit(0 if all(results) else 1)
