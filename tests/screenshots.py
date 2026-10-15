"""Headless screenshots of the running app (default http://127.0.0.1:8765)."""
import os, pathlib
from playwright.sync_api import sync_playwright
BASE = os.environ.get("SAVEHUB_URL", "http://127.0.0.1:8765")
OUT = pathlib.Path(__file__).resolve().parent.parent / "screenshots"; OUT.mkdir(exist_ok=True)
shots = [("01_dashboard_home", "#/library", None, "dark"), ("02_search_result", "#/library?q=咖啡店", None, "dark"),
         ("03_summary", "#/summary", None, "dark"), ("04_connect_apps", "#/connect", None, "dark"),
         ("05_accounts", "#/accounts", None, "dark"), ("06_contacts_light", "#/contacts", None, "light"),
         ("07_library_table_light", "#/library?q=", "table", "light")]
with sync_playwright() as p:
    exe = os.environ.get("CHROME", "/usr/bin/google-chrome")
    b = p.chromium.launch(executable_path=exe if os.path.exists(exe) else None, args=["--no-sandbox"])
    for name, hash_, view, theme in shots:
        pg = b.new_page(viewport={"width": 1366, "height": 900}, device_scale_factor=1)
        pg.add_init_script(f"localStorage.theme='{theme}'; localStorage.view='{view or 'cards'}';")
        pg.goto(BASE + "/" + hash_); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(700)
        pg.screenshot(path=str(OUT / f"{name}.png"), full_page=name in ("03_summary", "04_connect_apps"))
        print("saved", OUT / f"{name}.png"); pg.close()
    b.close()
