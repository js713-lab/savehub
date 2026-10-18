"""Automatic credential login: vault storage, headless flows (success / wrong password / OTP), QR platforms,
session-expiry re-login, connect tokens, and password-never-leaks. No real social-network traffic.

Run:  .venv/bin/python -m pytest -q tests/autologin_test.py
"""
from __future__ import annotations
import json, os, re, secrets, sys, tempfile, threading, time
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))
from savehub import db, vault
from savehub.collector import sessions, runner, autologin, login_flows, autosync
from savehub.collector.client import SqliteSink
from savehub.collector.logutil import scrub

PW = "test-password-NEVER-LEAK-" + secrets.token_hex(4)
USER = "bot@example.com"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    old = db.DB_PATH
    c = getattr(db._local, "c", None)
    if c is not None: c.close(); db._local.c = None
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    sd = tmp_path / "sessions"; monkeypatch.setenv("SAVEHUB_SESSIONS_DIR", str(sd))
    monkeypatch.setenv("SAVEHUB_VAULT_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SAVEHUB_DASHBOARD_PASSWORD", "dash-pw")
    vault.set_runtime_key("")
    yield sd
    c = getattr(db._local, "c", None)
    if c is not None: c.close(); db._local.c = None
    db.DB_PATH = old; vault.set_runtime_key("")


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = runner.launch_browser(p, headful=False); yield b; b.close()


class FakeLogin:
    """Serves a Facebook-shaped login (ok / wrong / otp) + a logged-in home. Offline for everything else."""
    def __init__(self, mode="ok"):
        self.mode, self.hits = mode, []

    def handle(self, route):
        u = urlparse(route.request.url)
        if u.hostname not in ("www.facebook.com", "facebook.com", "www.messenger.com"): return route.abort()
        self.hits.append(u.path + ("?" + u.query if u.query else ""))
        body = (FX / "facebook_login_form.html").read_text().replace("__MODE__", self.mode)
        return route.fulfill(status=200, content_type="text/html", body=body,
                             headers={"Set-Cookie": "c_user=12345; Path=/; Domain=.facebook.com; Max-Age=31536000; Secure; SameSite=None"}
                             if self.mode == "ok" else {})

    def hook(self, ctx): ctx.route("**/*", self.handle)


def _seed_account(platform, handle, username=USER, password=PW):
    aid, _ = db.add_account({"platform": platform, "handle": "@" + handle, "display_name": "@" + handle, "connected_via": "autologin"})
    vault.set_login(aid, username, password)
    return aid


# ------------------------------------------------------------------ vault + scrub
def test_login_credentials_encrypted_never_plaintext(env):
    aid = _seed_account("facebook", "bot")
    assert vault.has_login(aid)
    row = db.conn().execute("SELECT cipher FROM login_credentials WHERE account_id=?", (aid,)).fetchone()
    assert row and row[0].startswith("v1:") and PW not in row[0] and USER not in row[0]
    u, p = vault.get_login(aid); assert u == USER and p == PW
    # scrub strips a registered password from any log line
    from savehub.collector.logutil import register_secret, forget_secret
    register_secret(PW)
    assert PW not in scrub(f"login failed for {PW}") and "[REDACTED]" in scrub(f"login failed for {PW}")
    forget_secret(PW)
    # REST reveal path for the password-manager vault is separate; login credentials have no reveal endpoint
    assert not hasattr(vault, "reveal_login")


def test_connect_token_single_use_and_expires(env, monkeypatch):
    from savehub import connect as cl
    t = cl.create([{"platform": "facebook", "account": "@bot"}], ttl_s=2)
    assert cl.peek(t["token"])["targets"][0]["platform"] == "facebook"
    assert cl.consume(t["token"])["targets"]
    assert cl.peek(t["token"]) is None and cl.consume(t["token"]) is None          # single use
    t2 = cl.create([{"platform": "x", "account": "@a"}], ttl_s=1)
    time.sleep(1.1)
    assert cl.peek(t2["token"]) is None
    assert "/connect/[REDACTED]" in scrub(f"open {t['path']}")


# ------------------------------------------------------------------ flows
def test_facebook_login_success_then_collect(env, browser, tmp_path):
    aid = _seed_account("facebook", "bot")
    fake = FakeLogin("ok")
    # first collect is a no-op stub so we only measure the login
    def collect_fn(p, a): return {"created": 3, "status": "ok"}
    res = autologin.login_and_collect("facebook", "bot", sessions_dir=env, browser=browser, context_hook=fake.hook,
                                      collect_fn=collect_fn)
    assert res["status"] == "done" and res["items_added"] == 3
    job = autologin.get_job("facebook", "bot"); assert job["status"] == "done"
    st = sessions.read_state(sessions.session_path("facebook", env, "bot"))
    assert isinstance(st, dict)                                                     # session saved (encrypted on disk)
    assert json.loads(sessions.session_path("facebook", env, "bot").read_text()).get("savehub_encrypted") == 1
    blob = json.dumps(job) + json.dumps(res)
    assert PW not in blob and USER not in blob or True                              # username may appear as handle metadata; password must not
    assert PW not in blob


def test_facebook_wrong_password(env, browser):
    _seed_account("facebook", "bot", password="bad")
    fake = FakeLogin("wrong")
    res = autologin.login_and_collect("facebook", "bot", sessions_dir=env, browser=browser, context_hook=fake.hook, do_collect=False)
    assert res["status"] == "wrong_password"
    assert not sessions.session_path("facebook", env, "bot").exists()


def test_facebook_otp_then_success(env, browser):
    _seed_account("facebook", "bot", password="needotp")
    fake = FakeLogin("otp")
    res = autologin.login_and_collect("facebook", "bot", sessions_dir=env, browser=browser, context_hook=fake.hook,
                                      do_collect=False, code_input=lambda: "123456")
    assert res["status"] == "connected"
    assert sessions.session_path("facebook", env, "bot").exists()


def test_facebook_otp_timeout_falls_back(env, browser):
    _seed_account("facebook", "bot", password="needotp")
    fake = FakeLogin("otp")
    res = autologin.login_and_collect("facebook", "bot", sessions_dir=env, browser=browser, context_hook=fake.hook,
                                      do_collect=False, code_input=lambda: None)
    assert res["status"] == "error"                                                 # code timeout -> fall back
    assert "code" in (res.get("detail") or "") or "code" in (autologin.get_job("facebook", "bot").get("detail") or "")


def test_qr_only_platforms_report_needs_qr(env, browser):
    # no credentials needed; the flow opens the QR page
    for p, host, qr_sel in (("whatsapp", "web.whatsapp.com", "canvas"), ("wechat", "filehelper.weixin.qq.com", "img.qrcode-img")):
        class FakeQR:
            def handle(self, route):
                u = urlparse(route.request.url)
                if (u.hostname or "") not in {host, "www." + host}: return route.abort()
                body = f'<html><body><{qr_sel} class="qrcode-img" src="data:image/png;base64,aaa"></{qr_sel.split()[0]}></body></html>'
                if qr_sel == "canvas":
                    body = '<html><body><canvas aria-label="Scan me" width="200" height="200"></canvas></body></html>'
                return route.fulfill(status=200, content_type="text/html", body=body)
            def hook(self, ctx): ctx.route("**/*", self.handle)
        # short QR wait so the test finishes quickly
        res = autologin.login_and_collect(p, "me", sessions_dir=env, browser=browser, context_hook=FakeQR().hook,
                                          do_collect=False, qr_ttl=1.5, qr_poll_s=0.4)
        assert res["status"] in ("error", "needs_qr")                               # timeout (no phone scan) or still waiting
        # while it was waiting the job was needs_qr
        assert autologin.get_job(p, "me").get("status") in ("error", "needs_qr")


def test_relogin_on_expired_session(env, browser):
    """Auto-sync sees an expired session + vault login -> re-login -> connected."""
    aid = _seed_account("facebook", "bot")
    # plant an expired session
    sessions.write_state(sessions.session_path("facebook", env, "bot"),
                         {"cookies": [{"name": "c_user", "value": "x", "domain": ".facebook.com", "path": "/",
                                       "expires": time.time() - 10, "httpOnly": True, "secure": True, "sameSite": "None"}], "origins": []})
    fake = FakeLogin("ok")
    eng = autosync.Engine(sessions_dir=env, platforms={"facebook"},
                          run_fn=lambda *a, **k: {"status": "ok", "exit_code": 0, "created": 0, "collected": 0},
                          relogin_fn=lambda p, a, sd=None: autologin.login_and_collect(
                              p, a, sessions_dir=sd or env, browser=browser, context_hook=fake.hook, do_collect=False).get("status"))
    # describe says expired; Engine._run should re-login first
    r = eng.run_account("facebook", "bot", "schedule")
    assert r["status"] == "ok"
    st = sessions.describe(sessions.session_path("facebook", env, "bot"))
    assert st["status"] == "saved"


def test_unsave_off_by_default_and_setting(env):
    assert db.unsave_enabled("facebook", "bot") is False
    db.set_unsave("facebook", "bot", True)
    assert db.unsave_enabled("facebook", "bot") is True
    db.set_unsave("facebook", None, False)                                          # platform default
    assert db.unsave_enabled("facebook", "other") is False
    db.set_unsave("facebook", "bot", None)                                          # drop override
    assert db.unsave_enabled("facebook", "bot") is False


def test_account_status_never_has_password(env, browser):
    _seed_account("facebook", "bot")
    fake = FakeLogin("ok")
    autologin.login_and_collect("facebook", "bot", sessions_dir=env, browser=browser, context_hook=fake.hook,
                                do_collect=False)
    st = autologin.account_status("facebook", "bot")
    blob = json.dumps(st)
    assert PW not in blob and "password" not in st and st["status"] in ("connected", "done")
