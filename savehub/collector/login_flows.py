"""Headless credential login flows: fill username + password on the app's real sign-in form, detect the outcome
(connected / wrong password / a code challenge / a captcha / a QR scan), and save the encrypted session.

These drive the SAME Playwright browser the collectors use (runner.launch_browser / runner.new_context), so tests
inject a browser + a context_hook that answers the app from fixtures, exactly like collector_test.py. Passwords only
ever reach the app's own form; they are never logged (the flow never ev()s the value) nor returned.

A flow is config-driven (CSS selectors + a few options) so one engine covers X, Instagram, Threads, Facebook,
Messenger, TikTok, Douyin and Xiaohongshu. iCloud uses a dedicated pyicloud driver (no browser). WeChat / WhatsApp
are QR-only (no password form) and report that.

Outcome of a step: ("connected"|"wrong_password"|"needs_code"|"needs_captcha"|"needs_qr"|"error", detail, data).
When a code is needed the engine calls wait_for_code() (supplied by the orchestrator), which blocks until the user
submits a code on the dashboard (or times out), then fills it and re-checks - all in the SAME live context.
"""
from __future__ import annotations
import logging, re
from dataclasses import dataclass, field
from .base import get_collector
from .logutil import ev, scrub

# stealth: drop webdriver and a couple of the usual headless tells. A realistic UA is already set by runner.new_context.
STEALTH_JS = r"""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
try { Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']}); } catch(e){}
try { Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]}); } catch(e){}
window.chrome = window.chrome || {runtime: {}};
"""


@dataclass
class LoginFlow:
    platform: str
    login_url: str
    identifier: str = ""            # username / email / phone field
    password: str = "input[type=password]"
    submit: str = ""               # button to submit the credentials (after password)
    next_btn: str = ""             # optional: click to advance from identifier -> password screen
    success: str = ""              # a selector only present once signed in (defaults to the collector's logged_in)
    error: str = ""                # a selector only present on a wrong-password / rejected sign-in
    otp: str = ""                  # a field for an emailed/SMS/authenticator code (-> needs_code)
    otp_submit: str = ""
    captcha: str = ""              # a captcha challenge we cannot solve (-> needs_captcha)
    qr: str = ""                   # QR-only: no password form (-> needs_qr)
    password_login: bool = True    # False: QR-only platform
    extra: dict = field(default_factory=dict)

    def success_sel(self) -> str:
        if self.success: return self.success
        try: return get_collector(self.platform).SEL.get("logged_in", "")
        except Exception: return ""


FLOWS: dict[str, LoginFlow] = {
    "x": LoginFlow("x", "https://x.com/i/flow/login",
                   identifier='input[autocomplete="username"], input[name="text"]', next_btn='button:has-text("Next"), [role=button]:has-text("Next")',
                   password='input[name="password"], input[type=password]', submit='button[data-testid="LoginForm_Login_Button"], button:has-text("Log in")',
                   success='[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="AppTabBar_Home_Link"], a[href="/i/bookmarks"]',
                   error='[data-testid="LoginForm_Login_Button"][aria-disabled] ~ *, div:has-text("Wrong password"), div:has-text("incorrect")',
                   otp='input[data-testid="ocfEnterTextTextInput"], input[name="verfication_code"], input[autocomplete="one-time-code"]',
                   otp_submit='button[data-testid="ocfEnterTextNextButton"], button:has-text("Next")'),
    "instagram": LoginFlow("instagram", "https://www.instagram.com/accounts/login/",
                           identifier='input[name="username"]', password='input[name="password"]', submit='button[type=submit]',
                           error='#slfErrorAlert, p[data-testid="login-error-message"], div[role=alert]',
                           otp='input[name="verificationCode"], input[name="security_code"], input[autocomplete="one-time-code"]',
                           otp_submit='button:has-text("Confirm"), button[type=submit]'),
    "threads": LoginFlow("threads", "https://www.threads.com/login",
                         identifier='input[autocomplete="username"], input[name="username"], input[placeholder*="username" i]',
                         password='input[type=password]', submit='div[role=button]:has-text("Log in"), button:has-text("Log in")',
                         error='div[role=alert], div:has-text("incorrect")',
                         otp='input[autocomplete="one-time-code"], input[name="verificationCode"]'),
    "facebook": LoginFlow("facebook", "https://www.facebook.com/login.php",
                          identifier='input[name="email"], #email', password='input[name="pass"], #pass',
                          submit='div[role="button"]:has-text("Log in"), button:has-text("Log in"), button[name="login"], #loginbutton, button[type="submit"], input[type="submit"][name="login"]',
                          success='[aria-label="Your profile"], [aria-label="Account controls and settings"], [aria-label="Account Controls and Settings"], a[href*="/notifications/"], [data-testid="royal-feed-items"]',
                          error='#error_box, div[data-testid="royal_login_error"], div:has-text("password you entered is incorrect"), div:has-text("Incorrect password")',
                          otp='input[name="approvals_code"], input[autocomplete="one-time-code"], #email_challenge_pin',
                          otp_submit='#checkpointSubmitButton, button[type=submit], button:has-text("Continue")'),
    "messenger": LoginFlow("messenger", "https://www.messenger.com/login/",
                           identifier='input[name="email"], #email', password='input[name="pass"], #pass',
                           submit='div[role="button"]:has-text("Log in"), button:has-text("Log in"), button[name="login"], #loginbutton, input[type="submit"]',
                           success='[aria-label="Chats"], [aria-label="New message"], a[href*="/t/"], [aria-label="Conversation list"]',
                           error='#error_box, ._9ay7, div[role=alert]',
                           otp='input[name="approvals_code"], input[autocomplete="one-time-code"], #email_challenge_pin',
                           otp_submit='#checkpointSubmitButton, button[type=submit], button:has-text("Continue")'),
    "tiktok": LoginFlow("tiktok", "https://www.tiktok.com/login/phone-or-email/email",
                        identifier='input[name="username"], input[type=text]', password='input[type=password]',
                        submit='button[data-e2e="login-button"], button[type=submit]',
                        error='[data-e2e="login-error"], div:has-text("incorrect"), .error-text',
                        captcha='.captcha_verify_container, #captcha-verify-image, div[class*="captcha"]',
                        otp='input[name="code"], input[autocomplete="one-time-code"]'),
    "douyin": LoginFlow("douyin", "https://www.douyin.com/",
                        identifier='input[name="username"], input[placeholder*="手机号" i], input[type=text]', password='input[type=password]',
                        submit='button[type=submit], .login-button', error='.error-tip, div:has-text("错误")',
                        captcha='.captcha_verify_container, #captcha_container, div[class*="captcha"]',
                        qr='.qrcode-image img, canvas.qrcode, img[class*="qrcode"]', password_login=True),
    "xiaohongshu": LoginFlow("xiaohongshu", "https://www.xiaohongshu.com/login",
                             identifier='input[placeholder*="手机号" i], input[name="phone"], input[type=text]', password='input[type=password]',
                             submit='.submit, button:has-text("登录"), button[type=submit]', error='.error, div:has-text("错误")',
                             otp='input[placeholder*="验证码" i], input[autocomplete="one-time-code"]',
                             captcha='.red-captcha, div[class*="captcha"]'),
    # QR-only: no username/password form exists on the web client
    "wechat": LoginFlow("wechat", "https://filehelper.weixin.qq.com/", password_login=False,
                        qr='img.qrcode-img, .qrcode img, img[src*="qrcode"]', success='[data-msgid], #chatInput, .chat-panel'),
    "whatsapp": LoginFlow("whatsapp", "https://web.whatsapp.com/", password_login=False,
                          qr='canvas[aria-label*="scan" i], div[data-ref] canvas, img[alt*="scan" i]',
                          success='#pane-side, [aria-label="Chat list"]'),
    # iCloud: handled by the dedicated pyicloud driver (login_icloud), listed so the dashboard knows it is credential-driven
    "icloud": LoginFlow("icloud", "https://www.icloud.com/", password_login=True, extra={"driver": "pyicloud"}),
}

CHALLENGE = ("needs_code", "needs_captcha", "needs_qr")


def supports_password_login(platform: str) -> bool:
    f = FLOWS.get(platform)
    return bool(f and f.password_login)


def _visible(page, sel: str) -> bool:
    if not sel: return False
    try: return page.locator(sel).first.is_visible(timeout=400)
    except Exception: return False


def _classify(page, f: LoginFlow) -> tuple:
    """Look at the page right now and decide the outcome, or ("", ...) if still loading/unknown."""
    if f.success_sel() and _visible(page, f.success_sel()): return ("connected", "", {})
    if f.captcha and _visible(page, f.captcha): return ("needs_captcha", "the app showed a captcha; solve it in the browser on the box", {})
    if f.otp and _visible(page, f.otp): return ("needs_code", "the app asked for a verification code (2FA/OTP)", {})
    if f.qr and _visible(page, f.qr): return ("needs_qr", "the app wants a QR scan", {})
    if f.error and _visible(page, f.error): return ("wrong_password", "the app rejected the username or password", {})
    return ("", "", {})


def _settle(page, f: LoginFlow, timeout_ms=15000, poll=500) -> tuple:
    waited = 0
    while waited <= timeout_ms:
        st, detail, data = _classify(page, f)
        if st: return (st, detail, data)
        page.wait_for_timeout(poll); waited += poll
    return ("error", "login did not reach a known state in time", {})


def _fill(page, sel: str, value: str):
    page.locator(sel).first.fill(value)


_BANNER_BTNS = (
    'button:has-text("Allow all cookies")', 'button:has-text("Accept all")', 'button:has-text("Accept All")',
    'button:has-text("Only allow essential")', 'button:has-text("Decline optional")',
    '[data-cookiebanner="accept_button"]', 'button[title="Allow all cookies"]',
    'button:has-text("Allow essential and optional cookies")', 'button:has-text("Allow all")',
)

def _dismiss_banners(page) -> None:
    for sel in _BANNER_BTNS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=500):
                loc.click(timeout=2000); page.wait_for_timeout(600); return
        except Exception:
            pass


def run_browser_login(page, platform: str, username: str, password: str, *, wait_for_code=None,
                      settle_ms=15000) -> tuple:
    """Drive a password login on `page` (already in a hooked context). Returns (status, detail, data).
    wait_for_code(timeout_s)->str|None lets the caller supply an OTP the user typed into the dashboard."""
    f = FLOWS.get(platform)
    if f is None: return ("error", f"no login flow for '{platform}'", {})
    if not f.password_login:
        try:
            page.goto(f.login_url, wait_until="domcontentloaded")
            try: page.add_init_script(STEALTH_JS)
            except Exception: pass
        except Exception as e: return ("error", scrub(str(e))[:200], {})
        st, detail, data = _settle(page, f, 8000)
        return (st, detail, data) if st in ("connected", "needs_qr") else ("needs_qr", "scan the QR code to sign in", {})
    try:
        try: page.add_init_script(STEALTH_JS)
        except Exception: pass
        page.goto(f.login_url, wait_until="domcontentloaded")
        page.wait_for_timeout(700); _dismiss_banners(page)
        if f.identifier:
            page.wait_for_selector(f.identifier, timeout=settle_ms)
            _fill(page, f.identifier, username)
        if f.next_btn and _visible(page, f.next_btn):
            page.locator(f.next_btn).first.click(); page.wait_for_timeout(600)
        if f.password:
            try: page.wait_for_selector(f.password, timeout=settle_ms)
            except Exception: pass
            if _visible(page, f.password): _fill(page, f.password, password)
        if f.submit:
            clicked = False
            for part in [x.strip() for x in f.submit.split(",")]:
                try:
                    loc = page.locator(part).first
                    if loc.count() and loc.is_visible(timeout=400):
                        loc.click(); clicked = True; break
                except Exception: pass
            if not clicked and f.password:
                try: page.locator(f.password).first.press("Enter")
                except Exception: pass
    except Exception as e:
        return ("error", scrub(str(e))[:200], {})
    ev("autologin.submitted", platform=platform)                 # value never logged
    page.wait_for_timeout(800); _dismiss_banners(page)
    wait = max(settle_ms, 60000 if platform in ("facebook", "messenger") else settle_ms)
    st, detail, data = _settle(page, f, wait)
    # one resume round for an OTP/code challenge, in the same live context
    if st == "needs_code" and wait_for_code is not None:
        code = wait_for_code()
        if not code: return ("needs_code", detail, data)
        try:
            _fill(page, f.otp, code)
            # prefer Enter (fires the form's submit handler); fall back to clicking the button
            try: page.locator(f.otp).first.press("Enter")
            except Exception:
                if f.otp_submit and _visible(page, f.otp_submit): page.locator(f.otp_submit).first.click()
                elif f.submit and _visible(page, f.submit): page.locator(f.submit).first.click()
            page.wait_for_timeout(400)
        except Exception as e:
            return ("error", scrub(str(e))[:200], {})
        ev("autologin.code_submitted", platform=platform)
        st, detail, data = _settle(page, f, settle_ms)
        if st == "needs_code": st, detail = "wrong_password", "the verification code was not accepted"
    return (st, detail, data)
