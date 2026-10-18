"""Threads saved posts + Instagram saved posts/collections (both Meta, same post JSON shape).

Strategy (most to least stable):
  1. JSON: Meta ships post objects in XHR/fetch responses (/graphql/query, /api/graphql, /api/v1/feed/saved/...) and in
     embedded <script type="application/json"> blobs. A post object = dict with `code` + `user.username`
     (+ caption.text, taken_at, image_versions2/carousel_media/video_versions). Walked generically, so it survives
     query renames.
  2. DOM: permalinks (/@user/post/<code> on Threads, /p|reel|tv/<code> on Instagram) - URL patterns, not class names.
Un-save (opt-in): Threads = post '...' (svg[aria-label="More"]) -> "Unsave"; Instagram = post page bookmark toggle
svg[aria-label="Remove"] -> svg[aria-label="Save"].
Logged-out: redirect to /login (Threads) or /accounts/login (Instagram), or a username/password form.
"""
from __future__ import annotations
import logging, re
from urllib.parse import urlparse
from ..base import CollectorError, RunConfig, register
from ..logutil import ev
from ._common import FeedCollector, dig, first_url, iso_from_epoch, mark_target, raw, urls_of, walk


def is_meta_post(d: dict) -> bool:
    return isinstance(d.get("code"), str) and bool(d.get("code")) and isinstance(d.get("user"), dict) \
        and isinstance(d["user"].get("username"), str) and ("taken_at" in d or "caption" in d or "image_versions2" in d)


def meta_media(d: dict) -> tuple[str, dict]:
    """-> (thumbnail url, extra) for an Instagram/Threads media object."""
    extra = {}
    cands = dig(d, "image_versions2", "candidates", default=[]) or []
    car = d.get("carousel_media") or []
    if car:
        extra["carousel"] = len(car)
        if not cands: cands = dig(car[0], "image_versions2", "candidates", default=[]) or []
    if d.get("video_versions") or d.get("media_type") == 2: extra["video"] = True
    url = ""
    for c in cands:
        if isinstance(c, dict) and str(c.get("url", "")).startswith("http"): url = c["url"]; break
    if not url and isinstance(d.get("display_uri"), str): url = d["display_uri"]
    return url, extra


def _cand(m: dict) -> str:
    for c in dig(m, "image_versions2", "candidates", default=[]) or []:
        if isinstance(c, dict) and str(c.get("url", "")).startswith("http"): return c["url"]
    return first_url(m.get("display_uri"), m.get("display_url"))


def meta_media_fields(d: dict) -> dict:
    """Media schema fields (savehub/media.py) for an Instagram/Threads media object: every carousel image, the direct
    video file (video_versions), duration, cover aspect ratio and the author's avatar."""
    car = [m for m in (d.get("carousel_media") or []) if isinstance(m, dict)]
    images = urls_of([_cand(m) for m in car] if car else [_cand(d)])
    vids = d.get("video_versions") or next((m.get("video_versions") for m in car if m.get("video_versions")), None) or []
    video = next((v["url"] for v in vids if isinstance(v, dict) and str(v.get("url", "")).startswith("http")), "")
    w, h = d.get("original_width") or dig(d, "image_versions2", "candidates", 0, "width"), d.get("original_height") or dig(d, "image_versions2", "candidates", 0, "height")
    out = {"images": images if not video else [], "video_url": video, "duration": d.get("video_duration"),
           "avatar": first_url(dig(d, "user", "profile_pic_url"))}
    try: out["aspect"] = round(float(w) / float(h), 4) if w and h else None
    except (TypeError, ValueError, ZeroDivisionError): pass
    if video or d.get("media_type") == 2: out["type"] = "video"
    elif car or d.get("media_type") in (1, 8): out["type"] = "photo"
    elif not images: out["type"] = "post"         # Threads text post
    return {k: v for k, v in out.items() if v}


def meta_raw(d: dict, url: str) -> dict:
    media, extra = meta_media(d)
    if d.get("media_type") is not None: extra["media_type"] = d.get("media_type")
    if d.get("pk") or d.get("id"): extra["pk"] = str(d.get("pk") or d.get("id"))
    if d.get("product_type"): extra["product_type"] = d["product_type"]
    return raw(d["code"], url, author=dig(d, "user", "full_name") or "", handle=d["user"]["username"],
               text=dig(d, "caption", "text") or "", posted_at=iso_from_epoch(d.get("taken_at")),
               media_url=media, has_media=bool(media and (extra.get("video") or extra.get("carousel") or d.get("media_type") in (1, 2, 8))),
               extra=extra, **meta_media_fields(d))


# ====================================================================== Threads
THREADS_SEL = {
    "item": 'a[href*="/post/"]',
    "logged_in": 'svg[aria-label="Create"], a[href="/activity"], svg[aria-label="Notifications"], a[href^="/@"] svg[aria-label="Profile"]',
    "login_form": 'input[autocomplete="username"], input[name="username"], form[action*="login"], a[href^="/login"]',
    "empty": ':text("No saved posts"), :text("Nothing saved yet")',
    "container": 'div[data-pressable-container="true"], div[data-pressable-container]',
    "more": 'svg[aria-label="More"]',
    "menu_unsave": '[role="menuitem"]:has-text("Unsave"), [role="button"]:text-is("Unsave"), span:text-is("Unsave")',
    "menu_save": '[role="menuitem"]:text-is("Save"), [role="button"]:text-is("Save"), span:text-is("Save")',
}
THREADS_LOGIN_RE = re.compile(r"^https?://(?:www\.)?threads\.(?:net|com)/(?:login|accounts/login|logout)", re.I)
THREADS_POST_RE = re.compile(r"^(?:https?://(?:www\.)?threads\.(?:net|com))?/@([\w.]+)/post/([\w-]+)")

THREADS_EXTRACT_JS = r"""
(sel) => {
  const out = [], done = new Set();
  const links = [...document.querySelectorAll('a[href*="/post/"]')].filter(a => a.querySelector('time'));
  for (const link of links) {
    const href = link.getAttribute('href') || '';
    let c = link.closest(sel.container);
    if (!c) { c = link; for (let i = 0; i < 7 && c.parentElement; i++) c = c.parentElement; }
    if (done.has(c)) continue; done.add(c);
    // the container's own permalink is the first /post/ link with a <time>; quoted posts come later
    const t = link.querySelector('time');
    const authorA = [...c.querySelectorAll('a[href^="/@"]')].find(a => !(a.getAttribute('href') || '').includes('/post/') && (a.textContent || '').trim());
    // quoted posts: other permalinks inside the container; their subtree (up to just below the common ancestor) is excluded
    const quoteRoots = [...c.querySelectorAll('a[href*="/post/"]')].filter(a => a.querySelector('time') && (a.getAttribute('href') || '') !== href)
      .map(a => { let n = a; while (n.parentElement && n.parentElement !== c && !n.parentElement.contains(link)) n = n.parentElement; return n; });
    const inQuote = (e) => quoteRoots.some(q => q.contains(e));
    const texts = [...c.querySelectorAll('[dir="auto"]')].filter(e => !e.closest('a') && !e.querySelector('[dir="auto"]') && !inQuote(e))
        .map(e => (e.innerText || e.textContent || '').trim()).filter(Boolean);
    const imgs = [...c.querySelectorAll('img')].filter(i => !/profile picture/i.test(i.getAttribute('alt') || '') && !inQuote(i));
    out.push({href, datetime: t ? t.getAttribute('datetime') : '', handle: authorA ? (authorA.textContent || '').trim() : '',
              text: texts.sort((a, b) => b.length - a.length)[0] || '', media_url: imgs[0] ? imgs[0].getAttribute('src') : '',
              photos: imgs.length, video: !!c.querySelector('video')});
  }
  return out;
}
"""


def threads_url(handle, code): return f"https://www.threads.com/@{handle}/post/{code}"


@register
class ThreadsCollector(FeedCollector):
    platform, name = "threads", "Threads"
    home_url = "https://www.threads.com/"
    login_url = "https://www.threads.com/login"
    saved_url = "https://www.threads.com/saved"
    notes = ("JSON-first (post objects with code+user.username in /graphql + /api/graphql responses and embedded "
             "script JSON), DOM fallback on /@user/post/<code> permalinks. Un-save: post '...' menu -> Unsave. "
             "Login is shared with Instagram (log in with `login threads`).")
    SEL, LOGIN_URL_RE, EXTRACT_JS = THREADS_SEL, THREADS_LOGIN_RE, THREADS_EXTRACT_JS
    XHR_RE = re.compile(r"threads\.(?:net|com)/(?:api/graphql|graphql/query|api/v1/)", re.I)
    EMBEDDED_SEL = 'script[type="application/json"]'

    def parse_dom(self, r):
        m = THREADS_POST_RE.match(r.get("href") or "")
        if not m: return None
        handle, code = m.group(1), m.group(2)
        extra = {"video": True} if r.get("video") else {}
        return raw(code, threads_url(handle, code), handle=handle or r.get("handle", "").lstrip("@"), text=r.get("text") or "",
                   posted_at=r.get("datetime") or "", media_url=r.get("media_url") or "", has_media=bool(r.get("photos") or r.get("video")),
                   extra=extra)

    def json_raws(self, obj):
        return [meta_raw(d, threads_url(d["user"]["username"], d["code"])) for d in walk(obj, is_meta_post)]

    def unsave(self, page, item, cfg: RunConfig, session_file="") -> str:
        self.open_for_unsave(page, item.url, cfg, session_file, ready_keys=("more",))
        root = mark_target(page, self.SEL["container"], f"/post/{item.external_id}") or page
        return self.menu_unsave(page, root, self.SEL["more"], self.SEL["menu_unsave"], self.SEL["menu_save"])


# ====================================================================== Instagram
IG_SEL = {
    "item": 'a[href*="/p/"], a[href*="/reel/"]',
    "logged_in": 'svg[aria-label="Home"], a[href="/direct/inbox/"], svg[aria-label="New post"], a[href="/explore/"]',
    "login_form": 'input[name="username"], input[name="password"], form#loginForm',
    "empty": ':text("Save photos and videos that you want to see again"), :text("No saved posts")',
    "profile_link": 'a[href^="/"]:has(img[alt*="profile picture" i]), a[role="link"]:has(span:text-is("Profile"))',
    "collection_link": 'a[href*="/saved/"]',
    "unsave_on": 'svg[aria-label="Remove"]',
    "unsave_off": 'svg[aria-label="Save"]',
}
IG_LOGIN_RE = re.compile(r"^https?://(?:www\.)?instagram\.com/(?:accounts/login|accounts/emailsignup|challenge|accounts/suspended)", re.I)
IG_POST_RE = re.compile(r"^(?:https?://(?:www\.)?instagram\.com)?/(?:([\w.]+)/)?(p|reel|tv)/([\w-]+)")
IG_RESERVED = {"accounts", "explore", "direct", "reels", "stories", "p", "reel", "tv", "about", "legal", "developer", "web"}

IG_EXTRACT_JS = r"""
(sel) => {
  const out = [];
  for (const a of document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"], a[href*="/tv/"]')) {
    const img = a.querySelector('img');
    out.push({href: a.getAttribute('href') || '', alt: img ? (img.getAttribute('alt') || '') : '',
              media_url: img ? (img.getAttribute('src') || '') : '',
              video: !!a.querySelector('svg[aria-label="Clip"], svg[aria-label="Reel"], svg[aria-label="Video"]'),
              carousel: !!a.querySelector('svg[aria-label="Carousel"]')});
  }
  return out;
}
"""
IG_COLLECTIONS_JS = r"""
(user) => {
  const out = [], seen = new Set();
  const re = new RegExp('^/' + user + '/saved/([^/]+)/(\\d+)/?$');
  for (const a of document.querySelectorAll('a[href*="/saved/"]')) {
    const h = (a.getAttribute('href') || '').split('?')[0], m = h.match(re);
    if (!m || m[1] === 'all-posts' || seen.has(h)) continue; seen.add(h);
    const name = (a.getAttribute('aria-label') || a.innerText || a.textContent || '').trim().split('\n')[0] || decodeURIComponent(m[1]).replace(/-/g, ' ');
    out.push({href: h, name, id: m[2]});
  }
  return out;
}
"""
IG_ALT_RE = re.compile(r"^(?:Photo|Video|Reel|Carousel)(?: shared)? by (.+?)(?: on [A-Z][a-z]+ \d{1,2}, \d{4})?(?:\.|$)", re.I)


def ig_url(kind, code): return f"https://www.instagram.com/{'reel' if kind == 'reel' else 'p'}/{code}/"


@register
class InstagramCollector(FeedCollector):
    platform, name = "instagram", "Instagram"
    home_url = "https://www.instagram.com/"
    login_url = "https://www.instagram.com/accounts/login/"
    saved_url = "https://www.instagram.com/<username>/saved/all-posts/"
    notes = ("All saved posts (/<user>/saved/all-posts/) then every collection (/<user>/saved/<name>/<id>/) - items get "
             "extra.collections. Username = --account handle, else the profile link in the nav. JSON-first (media objects in "
             "/api/v1/feed/saved + graphql responses), DOM fallback on /p|reel/<code> grid links. Un-save: post page "
             "bookmark toggle (svg 'Remove' -> 'Save').")
    SEL, LOGIN_URL_RE, EXTRACT_JS = IG_SEL, IG_LOGIN_RE, IG_EXTRACT_JS
    XHR_RE = re.compile(r"instagram\.com/(?:api/v1/(?:feed/saved|collections)|graphql/query|api/graphql)", re.I)
    EMBEDDED_SEL = 'script[type="application/json"]'
    max_collections = 30

    def parse_dom(self, r):
        m = IG_POST_RE.match(r.get("href") or "")
        if not m: return None
        handle, kind, code = (m.group(1) or ""), m.group(2), m.group(3)
        if handle in IG_RESERVED: handle = ""
        alt = r.get("alt") or ""
        am = IG_ALT_RE.match(alt)
        author = am.group(1).strip() if am else ""
        extra = {k: True for k in ("video", "carousel") if r.get(k)}
        if alt: extra["alt"] = alt[:500]
        if kind == "reel": extra["video"] = True
        return raw(code, ig_url(kind, code), author=author, handle=handle, media_url=r.get("media_url") or "",
                   has_media=bool(r.get("media_url")), extra=extra)

    def json_raws(self, obj):
        out = []
        for d in walk(obj, is_meta_post):
            kind = "reel" if d.get("product_type") == "clips" else "p"
            out.append(meta_raw(d, ig_url(kind, d["code"])))
        return out

    def resolve_username(self, page, cfg: RunConfig, session_file="") -> str:
        h = (cfg.account_handle or "").strip().lstrip("@").lower()
        if h and h != "default": return h
        self.open(page, self.home_url, cfg, session_file)
        for el in page.locator(self.SEL["profile_link"]).all()[:5]:
            href = (el.get_attribute("href") or "").strip("/")
            if href and "/" not in href and href not in IG_RESERVED: return href
        raise CollectorError("could not find your Instagram username: pass --account @<username>")

    def targets(self, page, cfg: RunConfig, session_file=""):
        u = self.resolve_username(page, cfg, session_file)
        ev("instagram.user", username=u)
        out = [(f"https://www.instagram.com/{u}/saved/all-posts/", "")]
        try:
            self.open(page, f"https://www.instagram.com/{u}/saved/", cfg, session_file)
            page.wait_for_timeout(1500)
            cols = page.evaluate(IG_COLLECTIONS_JS, re.escape(u)) or []
        except CollectorError: raise
        except Exception as e:
            ev("instagram.collections_error", logging.WARNING, error=str(e)[:200]); cols = []
        ev("instagram.collections", count=len(cols), names=[c["name"] for c in cols][:30])
        out += [("https://www.instagram.com" + c["href"], c["name"]) for c in cols[: self.max_collections]]
        return out

    def unsave(self, page, item, cfg: RunConfig, session_file="") -> str:
        self.open_for_unsave(page, item.url, cfg, session_file)
        art = page.locator("article")
        root = art.first if art.count() and art.first.locator(f'{self.SEL["unsave_on"]}, {self.SEL["unsave_off"]}').count() else page
        return self.toggle_unsave(root, self.SEL["unsave_on"], self.SEL["unsave_off"])
