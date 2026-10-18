"""X (Twitter) bookmarks collector: https://x.com/i/bookmarks

DOM contract (as of 2026; X changes markup often, all selectors live in SEL so they are easy to update):
  tweet card        article[data-testid="tweet"]
  author            [data-testid="User-Name"]  (display name link, then "@handle")
  permalink + time  a[href*="/status/"] > time[datetime]   (the outer tweet's, not a quoted tweet's)
  text              [data-testid="tweetText"]
  media             [data-testid="tweetPhoto"] | [data-testid="videoPlayer"] | [data-testid="videoComponent"]
  quoted tweet      nested div[role="link"] inside the article (ignored for the outer fields, kept in extra.quoted)
  bookmark toggle   [data-testid="removeBookmark"] (bookmarked) / [data-testid="bookmark"] (not bookmarked);
                    fallback: share menu -> menuitem "Remove from Bookmarks"
  logged-out        URL /i/flow/login or /login, or a login form with no signed-in nav
"""
from __future__ import annotations
import logging, random, re
from ..base import Collector, CollectedItem, RunConfig, register
from ..logutil import ev

SEL = {
    "tweet": 'article[data-testid="tweet"]',
    "empty": '[data-testid="emptyState"]',
    "retry": '[role="button"]:has-text("Retry"), button:has-text("Retry")',
    "remove_bookmark": '[data-testid="removeBookmark"]',
    "bookmark": '[data-testid="bookmark"]',
    "share": '[data-testid="share"], button[aria-label^="Share"]',
    "menu_remove": '[role="menuitem"]:has-text("Remove from Bookmarks"), [role="menuitem"]:has-text("Remove post from Bookmarks"), [role="menuitem"]:has-text("Remove Tweet from Bookmarks")',
    "logged_in": '[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="AppTabBar_Home_Link"], [data-testid="SideNav_NewTweet_Button"]',
    "login_form": 'input[autocomplete="username"], input[name="text"][autocomplete], [data-testid="loginButton"], a[href="/login"], a[href="/i/flow/login"]',
}
LOGIN_URL_RE = re.compile(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/(?:i/flow/(?:login|signup)|login|logout|account/access)", re.I)
STATUS_RE = re.compile(r"^(?:https?://(?:www\.|mobile\.)?(?:x|twitter)\.com)?/([A-Za-z0-9_]{1,15})/status/(\d+)")

# Runs inside the page. Returns one plain object per visible tweet card (X virtualises the list, so this is called
# after every scroll). Only elements that belong to the OUTER tweet are used (not a quoted tweet nested inside it).
EXTRACT_JS = r"""
(sel) => {
  const txt = (n) => { if (!n) return ""; let s = "";
    for (const c of n.childNodes) {
      if (c.nodeType === 3) s += c.nodeValue;
      else if (c.nodeType === 1) { if (c.tagName === "IMG") s += c.getAttribute("alt") || "";
        else if (c.tagName === "BR") s += "\n"; else s += txt(c); } }
    return s; };
  const out = [];
  for (const a of document.querySelectorAll(sel)) {
    const quotes = [...a.querySelectorAll('div[role="link"]')];
    const own = (el) => el && !quotes.some(q => q.contains(el));
    const pick = (s) => [...a.querySelectorAll(s)].filter(own);
    const t = pick('a[href*="/status/"] time[datetime]')[0];
    if (!t) continue;                                   // promoted / placeholder card: no permalink
    const link = t.closest('a[href*="/status/"]');
    const un = pick('[data-testid="User-Name"]')[0];
    let name = "", handle = "";
    if (un) {
      const parts = [...un.querySelectorAll("a")].map(x => txt(x).trim()).filter(Boolean);
      name = parts.find(p => !p.startsWith("@")) || "";
      handle = (parts.find(p => p.startsWith("@")) || "").slice(1);
      if (!handle) { const m = (un.textContent || "").match(/@([A-Za-z0-9_]{1,15})/); if (m) handle = m[1]; }
    }
    const textEl = pick('[data-testid="tweetText"]')[0];
    const photos = pick('[data-testid="tweetPhoto"]');
    const video = pick('[data-testid="videoPlayer"], [data-testid="videoComponent"]');
    const img = photos.map(p => p.querySelector("img")).find(Boolean);
    const imgs = photos.map(p => p.querySelector("img")).filter(Boolean).map(i => i.getAttribute("src") || "").filter(Boolean);
    const vEl = video.map(v => v.querySelector("video")).find(Boolean);
    const av = pick('[data-testid="Tweet-User-Avatar"] img')[0];
    const dur = video.map(v => [...v.querySelectorAll("span, div")].map(x => (x.textContent || "").trim()).find(t => /^\d{1,2}:\d{2}$/.test(t))).find(Boolean) || "";
    const sc = pick('[data-testid="socialContext"]')[0];
    let quoted = null;
    if (quotes.length) { const q = quotes[0];
      const qt = q.querySelector('[data-testid="tweetText"]'); const qu = q.querySelector('[data-testid="User-Name"]');
      quoted = { text: txt(qt).trim(), user: qu ? (qu.textContent || "").trim() : "" }; }
    out.push({ href: link.getAttribute("href"), datetime: t.getAttribute("datetime"), name, handle,
               text: txt(textEl).trim(), photos: photos.length, video: video.length > 0,
               media_url: img ? img.getAttribute("src") : "", images: imgs, poster: vEl ? (vEl.getAttribute("poster") || "") : "",
               video_src: vEl && /^https?:/.test(vEl.getAttribute("src") || "") ? vEl.getAttribute("src") : "",
               avatar: av ? (av.getAttribute("src") || "") : "", duration: dur, social_context: sc ? (sc.textContent || "").trim() : "",
               card: pick('[data-testid="card.wrapper"]').length > 0, quoted });
  }
  return out;
}
"""

MARK_JS = r"""
(id) => { document.querySelectorAll('[data-savehub-target]').forEach(e => e.removeAttribute('data-savehub-target'));
  for (const a of document.querySelectorAll('article[data-testid="tweet"]')) {
    const quotes = [...a.querySelectorAll('div[role="link"]')];
    const l = [...a.querySelectorAll('a[href*="/status/"]')].find(x => !quotes.some(q => q.contains(x)) && x.querySelector('time') && new RegExp('/status/' + id + '(?:$|[/?#])').test(x.getAttribute('href')));
    if (l) { a.setAttribute('data-savehub-target', '1'); return true; } }
  return false; }
"""


def normalize_url(href: str):
    """-> (canonical https://x.com/<handle>/status/<id>, handle, id) or None."""
    m = STATUS_RE.match(href or "")
    if not m: return None
    return f"https://x.com/{m.group(1)}/status/{m.group(2)}", m.group(1), m.group(2)


def parse_raw(raws: list[dict]) -> list[CollectedItem]:
    """Turn EXTRACT_JS output into CollectedItems, de-duplicated by tweet id (first occurrence wins)."""
    seen, out = set(), []
    for r in raws or []:
        n = normalize_url(r.get("href", ""))
        if not n: continue
        url, url_handle, tid = n
        if tid in seen: continue
        seen.add(tid)
        extra = {k: r[k] for k in ("social_context", "quoted") if r.get(k)}
        if r.get("card"): extra["link_card"] = True
        if r.get("photos"): extra["photos"] = r["photos"]
        if r.get("video"): extra["video"] = True
        out.append(CollectedItem(platform="x", url=url, external_id=tid, author=r.get("name") or "",
                                 author_handle=r.get("handle") or url_handle, text=r.get("text") or "",
                                 posted_at=r.get("datetime") or "", has_media=bool(r.get("photos") or r.get("video")),
                                 media_url=r.get("media_url") or r.get("poster") or "", extra=extra,
                                 images=[u for u in (r.get("images") or []) if isinstance(u, str)][:20],
                                 thumbnail_url=r.get("media_url") or r.get("poster") or "",
                                 video_url=r.get("video_src") or "", duration=r.get("duration") or None,
                                 media_type="video" if r.get("video") else ("photo" if r.get("photos") else "post"),
                                 author_avatar=r.get("avatar") or ""))
    return out


@register
class XCollector(Collector):
    platform, name = "x", "X (Twitter)"
    home_url = "https://x.com/home"
    login_url = "https://x.com/i/flow/login"
    saved_url = "https://x.com/i/bookmarks"

    # ---- session state ----
    def is_login_page(self, page) -> bool:
        if LOGIN_URL_RE.match(page.url or ""): return True
        try:
            if page.locator(SEL["logged_in"]).count(): return False
            return page.locator(SEL["login_form"]).count() > 0
        except Exception:
            return False

    def is_logged_in(self, page) -> bool:
        if LOGIN_URL_RE.match(page.url or ""): return False
        try:   # DOM signal only: a stale auth_token cookie from an expired session must not count as logged in
            return page.locator(SEL["logged_in"]).count() > 0
        except Exception:
            return False

    def extract(self, page) -> list[CollectedItem]:
        return parse_raw(page.evaluate(EXTRACT_JS, SEL["tweet"]))

    def _wait_ready(self, page, timeout_ms):
        try:
            page.wait_for_function(
                """([t, e, l]) => document.querySelector(t) || document.querySelector(e) || document.querySelector(l)
                   || /\\/(i\\/flow\\/login|login)/.test(location.pathname)""",
                arg=[SEL["tweet"], SEL["empty"], SEL["login_form"]], timeout=timeout_ms)
        except Exception as e:
            ev("x.wait_ready_timeout", logging.WARNING, url=page.url, error=type(e).__name__)

    # ---- collect ----
    def collect(self, page, cfg: RunConfig, session_file="") -> list[CollectedItem]:
        ev("x.open", url=self.saved_url)
        page.goto(self.saved_url, wait_until="domcontentloaded", timeout=cfg.page_timeout_ms)
        self._wait_ready(page, cfg.page_timeout_ms)
        self.ensure_logged_in(page, session_file)
        if page.locator(SEL["empty"]).count() and not page.locator(SEL["tweet"]).count():
            ev("x.empty", note="no bookmarks"); return []
        items, seen, idle, retries = [], set(), 0, 0
        for n in range(cfg.max_scrolls):
            new = [i for i in self.extract(page) if i.external_id not in seen]
            for i in new:
                if len(items) >= cfg.max_items: break
                seen.add(i.external_id); items.append(i)
            ev("x.scroll", round=n, new=len(new), total=len(items))
            if len(items) >= cfg.max_items: ev("x.cap_reached", max_items=cfg.max_items); break
            if self.incremental_done(cfg, items): ev("x.incremental_stop", known_seen=cfg.stop_after_known, total=len(items)); break
            if new: idle = 0
            else:
                if page.locator(SEL["retry"]).count() and retries < 2:   # "Something went wrong. Try reloading."
                    retries += 1; ev("x.retry_button", logging.WARNING, attempt=retries)
                    page.wait_for_timeout(10000); page.locator(SEL["retry"]).first.click()
                    page.wait_for_timeout(3000); continue
                idle += 1
                if idle >= cfg.idle_rounds: ev("x.end_of_list", idle_rounds=idle); break
            if self.is_login_page(page): self.ensure_logged_in(page, session_file)
            page.evaluate("window.scrollBy(0, Math.round(window.innerHeight * 0.85))")
            page.wait_for_timeout(int(1000 * (cfg.scroll_delay + random.uniform(0, cfg.scroll_jitter))))
        return items

    # ---- unsave ----
    def unsave(self, page, item: CollectedItem, cfg: RunConfig, session_file="") -> str:
        """Open the tweet, remove it from Bookmarks, verify. Returns 'unsaved' | 'already_unsaved' | 'failed'."""
        page.goto(item.url, wait_until="domcontentloaded", timeout=cfg.page_timeout_ms)
        self._wait_ready(page, cfg.page_timeout_ms)
        self.ensure_logged_in(page, session_file)
        if not page.evaluate(MARK_JS, item.external_id):
            ev("x.unsave.not_found", logging.WARNING, url=item.url); return "failed"
        art = page.locator('[data-savehub-target="1"]').first
        btn = art.locator(SEL["remove_bookmark"])
        if btn.count():
            btn.first.click()
        else:
            if art.locator(SEL["bookmark"]).count(): return "already_unsaved"
            share = art.locator(SEL["share"])
            if not share.count(): ev("x.unsave.no_control", logging.WARNING, url=item.url); return "failed"
            share.first.click()
            mi = page.locator(SEL["menu_remove"])
            try: mi.first.wait_for(timeout=5000)
            except Exception:
                page.keyboard.press("Escape"); ev("x.unsave.no_menu_item", logging.WARNING, url=item.url); return "failed"
            mi.first.click()
        return self._verify_unsaved(page, art, used_menu=not btn.count())

    def _verify_unsaved(self, page, art, used_menu) -> str:
        if not used_menu:   # direct toggle: it flips to data-testid="bookmark"
            try: art.locator(SEL["bookmark"]).first.wait_for(timeout=6000); return "unsaved"
            except Exception: return "failed"
        # menu flow: re-open the share menu and make sure "Remove from Bookmarks" is gone
        page.wait_for_timeout(1500)
        try:
            art.locator(SEL["share"]).first.click()
            page.wait_for_selector('[role="menuitem"]', timeout=5000)
            still = page.locator(SEL["menu_remove"]).count() > 0
        except Exception:
            return "failed"
        finally:
            page.keyboard.press("Escape")
        return "failed" if still else "unsaved"
