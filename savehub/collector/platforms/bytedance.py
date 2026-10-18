"""TikTok Favorites + Douyin 收藏 (both ByteDance; data-e2e attributes + JSON item lists).

TikTok   https://www.tiktok.com/@<user>  -> Favorites tab [data-e2e="favorites-tab"]
         JSON: /api/user/collect/item_list/ (itemList[]: id, desc, createTime, author.uniqueId/nickname, video.cover,
               imagePost). The profile's own posts (/api/post/item_list, rehydration JSON) are deliberately ignored.
         DOM: [data-e2e="favorites-item"] / [data-e2e="user-post-item"] a[href*="/video/" | "/photo/"].
         Un-favorite (opt-in): video page favorite button (aria-pressed / data-e2e), verified by the state flip.
Douyin   https://www.douyin.com/user/self?showTab=favorite_collection
         JSON: /aweme/v1/web/aweme/listcollection/ (aweme_list[]: aweme_id, desc, create_time, author.nickname/unique_id,
               video.cover.url_list) - Douyin's signed XHRs are fired by the page itself, we only read the responses.
         DOM: [data-e2e="user-post-list"] li a[href*="/video/" | "/note/"].
         Un-collect (opt-in): video page [data-e2e="video-player-collect"] with data-e2e-state is-collected -> no-collect.
Both show captchas (secsdk / slider) to automation; a captcha page simply yields no items (logged as end_of_list).
"""
from __future__ import annotations
import logging, re
from ..base import CollectorError, RunConfig, register
from ..logutil import ev
from ._common import FeedCollector, dig, first_url, iso_from_epoch, raw, walk, urls_of

# ====================================================================== TikTok
TT_SEL = {
    "item": '[data-e2e="favorites-item"], [data-e2e="user-post-item"], a[href*="/video/"]',
    "logged_in": '[data-e2e="profile-icon"], [data-e2e="nav-profile"], [data-e2e="inbox-icon"], [data-e2e="upload-icon"]',
    "login_form": '[data-e2e="top-login-button"], [data-e2e="nav-login-button"], #loginContainer, [data-e2e="login-modal"]',
    "empty": '[data-e2e="favorites-empty"], :text("No favorites yet"), :text("Favorite posts will appear here")',
    "favorites_tab": '[data-e2e="favorites-tab"], [role="tab"]:has-text("Favorites")',
    "profile_link": 'a[data-e2e="nav-profile"], a[data-e2e="profile-icon"], a[href^="/@"][data-e2e*="profile"]',
    "unsave_on": ('button[aria-pressed="true"]:has([data-e2e="undefined-icon"]), button[aria-pressed="true"]:has([data-e2e="favorite-icon"]), '
                  'button[aria-pressed="true"][aria-label*="avorite" i], [data-e2e="favorite-icon"][aria-pressed="true"]'),
    "unsave_off": ('button[aria-pressed="false"]:has([data-e2e="undefined-icon"]), button[aria-pressed="false"]:has([data-e2e="favorite-icon"]), '
                   'button[aria-pressed="false"][aria-label*="avorite" i], [data-e2e="favorite-icon"][aria-pressed="false"]'),
}
TT_LOGIN_RE = re.compile(r"^https?://(?:www\.)?tiktok\.com/(?:login|signup)", re.I)
TT_ITEM_RE = re.compile(r"^(?:https?://(?:www\.)?tiktok\.com)?/@([\w.-]+)/(video|photo)/(\d+)")

TT_EXTRACT_JS = r"""
(sel) => {
  const scope = document.querySelector('[data-e2e="favorites-item-list"]') || document.querySelector('[data-e2e="user-post-item-list"]') || document;
  const out = [];
  for (const a of scope.querySelectorAll('a[href*="/video/"], a[href*="/photo/"]')) {
    const card = a.closest('[data-e2e="favorites-item"], [data-e2e="user-post-item"]') || a.parentElement || a;
    const img = card.querySelector('img');
    const desc = card.querySelector('[data-e2e="user-post-item-desc"], [data-e2e="favorites-item-desc"]');
    out.push({href: a.getAttribute('href') || '', alt: img ? (img.getAttribute('alt') || '') : '', media_url: img ? (img.getAttribute('src') || '') : '',
              desc: desc ? (desc.innerText || desc.textContent || '').trim() : '',
              views: ((card.querySelector('[data-e2e="video-views"]') || {}).textContent || '').trim()});
  }
  return out;
}
"""


def is_tt_item(d):
    return isinstance(d.get("id"), str) and d["id"].isdigit() and "desc" in d and ("createTime" in d or "author" in d) \
        and (isinstance(d.get("author"), dict) or "authorId" in d)


@register
class TikTokCollector(FeedCollector):
    platform, name = "tiktok", "TikTok"
    home_url = "https://www.tiktok.com/"
    login_url = "https://www.tiktok.com/login"
    saved_url = "https://www.tiktok.com/@<username> -> Favorites tab"
    notes = ("Profile Favorites tab (videos + photo posts). Username = --account handle, else the nav profile link. JSON-first "
             "(/api/user/collect/item_list/ responses), DOM fallback via data-e2e. Un-favorite: video "
             "page favorite toggle (aria-pressed). Expect captchas (secsdk) under automation; Favorites must be visible to you.")
    SEL, LOGIN_URL_RE, EXTRACT_JS = TT_SEL, TT_LOGIN_RE, TT_EXTRACT_JS
    XHR_RE = re.compile(r"tiktok\.com/api/(?:user/collect|collection|favorite)[\w/]*/item_list", re.I)
    # no EMBEDDED_SEL: the profile's rehydration JSON describes the user's OWN posts, never the favorites

    def parse_dom(self, r):
        m = TT_ITEM_RE.match(r.get("href") or "")
        if not m: return None
        handle, kind, vid = m.groups()
        extra = {"photo": True} if kind == "photo" else {"video": True}
        if r.get("views"): extra["views"] = r["views"]
        return raw(vid, f"https://www.tiktok.com/@{handle}/{kind}/{vid}", handle=handle, text=r.get("desc") or r.get("alt") or "",
                   media_url=r.get("media_url") or "", has_media=True, extra=extra, type=kind, aspect=0.5625 if kind == "video" else None)

    def json_raws(self, obj):
        out = []
        for d in walk(obj, is_tt_item):
            handle = dig(d, "author", "uniqueId") or ""
            photo = bool(d.get("imagePost"))
            kind = "photo" if photo else "video"
            cover = first_url(dig(d, "video", "cover"), dig(d, "video", "originCover"),
                              dig(d, "imagePost", "cover", "imageURL", "urlList"), dig(d, "imagePost", "images", 0, "imageURL", "urlList"))
            extra = {"photo": True} if photo else {"video": True}
            if dig(d, "music", "title"): extra["music"] = dig(d, "music", "title")
            imgs = urls_of(dig(d, "imagePost", "images", default=[])) if photo else []
            vw, vh = dig(d, "video", "width", default=0), dig(d, "video", "height", default=0)
            out.append(raw(d["id"], f"https://www.tiktok.com/@{handle or 'i'}/{kind}/{d['id']}", author=dig(d, "author", "nickname") or "",
                           handle=handle, text=d.get("desc") or "", posted_at=iso_from_epoch(d.get("createTime")),
                           media_url=cover, has_media=True, extra=extra, type=kind, images=imgs or None,
                           video_url=None if photo else (first_url(dig(d, "video", "playAddr"), dig(d, "video", "downloadAddr")) or None),
                           duration=None if photo else (dig(d, "video", "duration") or None),
                           aspect=round(vw / vh, 4) if not photo and vw and vh else None,
                           avatar=first_url(dig(d, "author", "avatarThumb"), dig(d, "author", "avatarMedium")) or None))
        return out

    def resolve_username(self, page, cfg, session_file=""):
        h = (cfg.account_handle or "").strip().lstrip("@").lower()
        if h and h != "default": return h
        self.open(page, self.home_url, cfg, session_file)
        for el in page.locator(self.SEL["profile_link"]).all()[:5]:
            m = re.match(r"^(?:https?://(?:www\.)?tiktok\.com)?/@([\w.-]+)", el.get_attribute("href") or "")
            if m: return m.group(1)
        raise CollectorError("could not find your TikTok username: pass --account @<username>")

    def targets(self, page, cfg, session_file=""):
        u = self.resolve_username(page, cfg, session_file)
        ev("tiktok.user", username=u)
        return [(f"https://www.tiktok.com/@{u}", "")]

    def after_open(self, page, cfg, label):
        tab = page.locator(self.SEL["favorites_tab"])
        try: tab.first.wait_for(timeout=10000)
        except Exception:
            raise CollectorError("TikTok Favorites tab not found on your profile (captcha, or wrong --account?)") from None
        tab.first.click()
        page.wait_for_timeout(1500)

    def dom_raws(self, page):
        # before the Favorites list renders, the DOM still holds the profile's own videos: only trust the favorites list
        if not page.locator('[data-e2e="favorites-item-list"], [data-e2e="favorites-item"]').count(): return []
        return super().dom_raws(page)

    def unsave(self, page, item, cfg: RunConfig, session_file="") -> str:
        self.open_for_unsave(page, item.url, cfg, session_file)
        return self.toggle_unsave(page, self.SEL["unsave_on"], self.SEL["unsave_off"])


# ====================================================================== Douyin
DY_SEL = {
    "item": '[data-e2e="user-post-list"] a[href*="/video/"], [data-e2e="user-post-list"] a[href*="/note/"], [data-e2e="scroll-list"] a[href*="/video/"]',
    "logged_in": '[data-e2e="live-avatar"], [data-e2e="user-info"], [data-e2e="user-detail"], [data-e2e="profile-avatar"]',
    "login_form": '#login-panel-new, [id^="login-panel"], [data-e2e="login-panel"], .login-mask, [data-e2e="login-button"]',
    "empty": ':text("暂无收藏"), :text("还没有收藏")',
    "favorite_tab": '[data-e2e="user-tab-favorite"], [role="tab"]:has-text("收藏"), span:text-is("收藏")',
    "unsave_on": '[data-e2e="video-player-collect"][data-e2e-state="video-player-is-collected"]',
    "unsave_off": '[data-e2e="video-player-collect"][data-e2e-state="video-player-no-collect"]',
}
DY_LOGIN_RE = re.compile(r"^https?://(?:www\.)?douyin\.com/(?:login|passport)|^https?://(?:sso|passport)\.douyin\.com/", re.I)
DY_ITEM_RE = re.compile(r"^(?:https?://(?:www\.)?douyin\.com)?/(video|note)/(\d+)")

DY_EXTRACT_JS = r"""
(sel) => {
  const scope = document.querySelector('[data-e2e="user-post-list"]') || document.querySelector('[data-e2e="scroll-list"]') || document;
  const out = [];
  for (const a of scope.querySelectorAll('a[href*="/video/"], a[href*="/note/"]')) {
    const li = a.closest('li') || a;
    const img = li.querySelector('img');
    const t = li.querySelector('p, [class*="title"]');
    out.push({href: a.getAttribute('href') || '', alt: img ? (img.getAttribute('alt') || '') : '', media_url: img ? (img.getAttribute('src') || '') : '',
              title: t ? (t.innerText || t.textContent || '').trim() : ''});
  }
  return out;
}
"""


def is_aweme(d): return isinstance(d.get("aweme_id"), (str, int)) and str(d.get("aweme_id")).isdigit() and "desc" in d


@register
class DouyinCollector(FeedCollector):
    platform, name = "douyin", "抖音 Douyin"
    home_url = "https://www.douyin.com/"
    login_url = "https://www.douyin.com/"
    saved_url = "https://www.douyin.com/user/self?showTab=favorite_collection"
    notes = ("收藏 videos/notes on /user/self?showTab=favorite_collection. JSON-first (aweme_list from "
             "/aweme/v1/web/aweme/listcollection/ responses fired by the page), DOM fallback on /video|note/<id> links. "
             "QR-code login; slider captchas are common. Un-collect: video page [data-e2e=video-player-collect] state toggle.")
    SEL, LOGIN_URL_RE, EXTRACT_JS = DY_SEL, DY_LOGIN_RE, DY_EXTRACT_JS
    XHR_RE = re.compile(r"douyin\.com/aweme/v\d+/web/(?:aweme/listcollection|collects?/[\w/]*list)", re.I)

    def parse_dom(self, r):
        m = DY_ITEM_RE.match(r.get("href") or "")
        if not m: return None
        kind, vid = m.groups()
        return raw(vid, f"https://www.douyin.com/{kind}/{vid}", text=r.get("title") or r.get("alt") or "",
                   media_url=r.get("media_url") or "", has_media=True, extra={"note": True} if kind == "note" else {"video": True},
                   type="note" if kind == "note" else "video", aspect=None if kind == "note" else 0.5625)

    def json_raws(self, obj):
        out = []
        for d in walk(obj, is_aweme):
            vid = str(d["aweme_id"])
            note = bool(d.get("images")) or d.get("aweme_type") == 68
            cover = first_url(dig(d, "video", "cover", "url_list"), dig(d, "video", "origin_cover", "url_list"),
                              dig(d, "images", 0, "url_list"))
            extra = {"note": True} if note else {"video": True}
            if dig(d, "author", "sec_uid"): extra["author_sec_uid"] = dig(d, "author", "sec_uid")
            dur = dig(d, "video", "duration", default=0) or d.get("duration") or 0     # Douyin reports milliseconds
            vw, vh = dig(d, "video", "width", default=0), dig(d, "video", "height", default=0)
            out.append(raw(vid, f"https://www.douyin.com/{'note' if note else 'video'}/{vid}", author=dig(d, "author", "nickname") or "",
                           handle=str(dig(d, "author", "unique_id") or dig(d, "author", "short_id") or ""), text=d.get("desc") or "",
                           posted_at=iso_from_epoch(d.get("create_time")), media_url=cover, has_media=True, extra=extra,
                           type="note" if note else "video", images=urls_of(d.get("images") or []) or None if note else None,
                           video_url=None if note else (first_url(dig(d, "video", "play_addr", "url_list")) or None),
                           duration=(dur / 1000 if dur > 1000 else dur) or None if not note else None,
                           aspect=round(vw / vh, 4) if not note and vw and vh else None,
                           avatar=first_url(dig(d, "author", "avatar_thumb", "url_list")) or None))
        return out

    def after_open(self, page, cfg, label):
        self.wait_items(page, 6000)
        if page.locator(self.SEL["item"]).count(): return
        tab = page.locator(self.SEL["favorite_tab"])       # showTab param ignored: click 收藏
        if tab.count():
            try: tab.first.click(); page.wait_for_timeout(1500)
            except Exception as e: ev("douyin.tab_click_failed", logging.WARNING, error=str(e)[:200])

    def unsave(self, page, item, cfg: RunConfig, session_file="") -> str:
        self.open_for_unsave(page, item.url, cfg, session_file)
        return self.toggle_unsave(page, self.SEL["unsave_on"], self.SEL["unsave_off"])
