"""Facebook Saved: https://www.facebook.com/saved/

Facebook's class names are obfuscated and rotate, so the DOM strategy keys only on roles/aria-labels/text:
  entry   = the largest ancestor of one entry-level "More" button ([aria-label="More"], [aria-label^="More options"])
            inside [role="main"] that holds a text link (title) - one entry per More button
  title   = first text link of the entry not inside the "Saved from ..." line
  source  = the "Saved from <a>" line (-> author), type prefix "Video · " / "Link · " (-> extra.kind)
  url     = title link; l.facebook.com/l.php?u=<target> is decoded, facebook.com tracking params (__cft__, __tn__...) dropped
JSON (best-effort): GraphQL responses carrying `savable_title` objects are walked too.
Un-save (opt-in): on /saved, the entry's More -> "Unsave"; verified (entry gone, or menu no longer offers Unsave).
"""
from __future__ import annotations
import logging, re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from ..base import RunConfig, register
from ..logutil import ev
from ._common import FeedCollector, dig, first_url, raw, short_hash, walk

FB_SEL = {
    "item": '[role="main"] [aria-label="More"], [role="main"] [aria-label^="More options"]',
    "logged_in": '[aria-label="Your profile"], [aria-label="Account controls and settings"], [aria-label="Account Controls and Settings"], [aria-label="Notifications"][role="button"], a[href*="/notifications/"]',
    "login_form": 'input[name="email"], input[name="pass"], form[action*="/login"], #loginbutton, button[name="login"]',
    "empty": ':text("You haven\'t saved anything yet"), :text("Nothing saved yet")',
    "more": '[aria-label="More"], [aria-label^="More options"]',
    "menu_unsave": '[role="menuitem"]:has-text("Unsave"), [role="menuitem"]:has-text("Remove from saved")',
    "menu_save": '[role="menuitem"]:has-text("Save")',
}
FB_LOGIN_RE = re.compile(r"^https?://(?:www\.|m\.|web\.)?facebook\.com/(?:login|checkpoint|recover|r\.php)", re.I)
KEEP_Q = {"id", "story_fbid", "v", "fbid", "set", "list_id", "multi_permalinks", "t"}

FB_EXTRACT_JS = r"""
(sel) => {
  const main = document.querySelector('[role="main"]') || document.body;
  const btns = [...main.querySelectorAll(sel.more)];
  const textLink = (el, skip) => [...el.querySelectorAll('a[href]')].find(a => (a.innerText || a.textContent || '').trim() && !(skip && skip.contains(a)));
  const out = [];
  btns.forEach((b, i) => {
    let e = b, entry = null;
    for (let k = 0; k < 12 && e.parentElement && e !== main; k++) {
      e = e.parentElement;
      if (!entry && textLink(e)) entry = e;
      if (entry && (e === main || e.querySelectorAll(sel.more).length > 1)) break;
      if (entry) entry = e;
    }
    if (!entry) return;
    entry.setAttribute('data-savehub-entry', String(i));
    const savedLine = [...entry.querySelectorAll('span, div')].filter(x => /Saved from/i.test(x.textContent || '')).pop() || null;
    const title = textLink(entry, savedLine);
    if (!title) return;
    const src = savedLine ? savedLine.querySelector('a[href]') : null;
    const meta = savedLine ? (savedLine.innerText || savedLine.textContent || '').trim() : '';
    const img = [...entry.querySelectorAll('img')].find(x => (x.getAttribute('src') || '').startsWith('http'));
    out.push({idx: i, href: title.getAttribute('href') || '', title: (title.innerText || title.textContent || '').trim(), meta,
              source: src ? (src.innerText || src.textContent || '').trim() : '', source_href: src ? (src.getAttribute('href') || '') : '',
              media_url: img ? img.getAttribute('src') : '', video: !!entry.querySelector('video, [aria-label*="Play" i]')});
  });
  return out;
}
"""


def clean_url(href: str) -> str:
    if not href: return ""
    if href.startswith("/"): href = "https://www.facebook.com" + href
    u = urlparse(href)
    if u.hostname and u.hostname.endswith("l.facebook.com") and u.path.startswith("/l.php"):
        tgt = (parse_qs(u.query).get("u") or [""])[0]
        if tgt: return clean_url(tgt) if "facebook.com" in (urlparse(tgt).hostname or "") else _strip_fbclid(tgt)
    if u.hostname and (u.hostname == "facebook.com" or u.hostname.endswith(".facebook.com")):
        q = {k: v for k, v in parse_qs(u.query).items() if k in KEEP_Q}
        return urlunparse(("https", "www.facebook.com", u.path, "", urlencode(q, doseq=True), ""))
    return _strip_fbclid(href)


def _strip_fbclid(url: str) -> str:
    u = urlparse(url)
    q = {k: v for k, v in parse_qs(u.query, keep_blank_values=True).items() if k not in ("fbclid",) and not k.startswith("utm_")}
    return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(q, doseq=True), ""))


def is_savable(d): return isinstance(d.get("savable_title"), dict) or (isinstance(d.get("savable"), dict) and "savable_title" in d["savable"])


@register
class FacebookCollector(FeedCollector):
    platform, name = "facebook", "Facebook"
    home_url = "https://www.facebook.com/"
    login_url = "https://www.facebook.com/login/"
    saved_url = "https://www.facebook.com/saved/"
    notes = ("All saved items on /saved/ (posts, links, videos, Marketplace...). Role/aria/text-only DOM (one entry per "
             "'More' button), l.php links decoded, tracking params dropped; GraphQL savable_title objects as best-effort "
             "JSON. Un-save: entry More -> Unsave on /saved, verified.")
    SEL, LOGIN_URL_RE, EXTRACT_JS = FB_SEL, FB_LOGIN_RE, FB_EXTRACT_JS
    XHR_RE = re.compile(r"facebook\.com/api/graphql", re.I)

    def parse_dom(self, r):
        url = clean_url(r.get("href") or "")
        if not url or url.rstrip("/").endswith("facebook.com/saved"): return None
        meta = r.get("meta") or ""
        kind = meta.split("·")[0].strip() if "·" in meta else ""
        extra = {"idx": r.get("idx")}
        if kind: extra["kind"] = kind
        if r.get("source_href"): extra["saved_from_url"] = clean_url(r["source_href"])
        if r.get("video"): extra["video"] = True
        author = re.sub(r"(?:'s|’s) (?:post|video|reel|photo)$", "", r.get("source") or "").strip()
        return raw(short_hash(url), url, author=author, title=r.get("title") or "", text=r.get("title") or "",
                   media_url=r.get("media_url") or "", has_media=bool(r.get("media_url") or r.get("video")), extra=extra,
                   type="video" if r.get("video") or re.search(r"/(?:videos|reel|watch)/", url) else ("photo" if r.get("media_url") else "post"))

    def json_raws(self, obj):
        out = []
        for d in walk(obj, is_savable):
            s = d.get("savable") if isinstance(d.get("savable"), dict) else d
            url = clean_url(s.get("url") or dig(s, "savable_permalink") or d.get("url") or "")
            if not url: continue
            title = dig(s, "savable_title", "text") or ""
            img = first_url(dig(s, "savable_image", "uri"), dig(d, "image", "uri"))
            out.append(raw(short_hash(url), url, title=title, text=title, media_url=img, extra={},
                           type="video" if re.search(r"/(?:videos|reel|watch)/", url) else ("photo" if img else "post")))
        return out

    def dom_raws(self, page):
        out = super().dom_raws(page)
        for r in out: r["extra"].pop("idx", None)
        return out

    def unsave(self, page, item, cfg: RunConfig, session_file="") -> str:
        self.open(page, self.saved_url, cfg, session_file)
        self.wait_items(page)
        idx = None
        for _ in range(min(cfg.max_scrolls, 60)):
            for r in (self.parse_dom(x) for x in (page.evaluate(self.EXTRACT_JS, self.SEL) or [])):
                if r and r["url"] == item.url: idx = r["extra"]["idx"]; break
            if idx is not None: break
            self.scroll(page); page.wait_for_timeout(int(1000 * cfg.scroll_delay))
        if idx is None:
            ev("facebook.unsave.not_found", logging.WARNING, url=item.url); return "already_unsaved"
        root = page.locator(f'[data-savehub-entry="{idx}"]').first
        more = root.locator(self.SEL["more"]).last
        more.click()
        mi = page.locator(self.SEL["menu_unsave"])
        try: mi.first.wait_for(timeout=5000)
        except Exception:
            page.keyboard.press("Escape"); ev("facebook.unsave.no_menu_item", logging.WARNING, url=item.url); return "failed"
        mi.first.click()
        page.wait_for_timeout(1500)
        try:   # Facebook either drops the entry from the list or keeps it with the menu now offering "Save"
            if not root.count() or not root.is_visible(): return "unsaved"
            root.locator(self.SEL["more"]).last.click()
            page.wait_for_selector('[role="menuitem"]', timeout=5000)
            still = page.locator(self.SEL["menu_unsave"]).count() > 0
        except Exception:
            return "failed"
        finally:
            page.keyboard.press("Escape")
        return "failed" if still else "unsaved"
