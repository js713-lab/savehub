"""Xiaohongshu / RED 收藏 (collected notes): https://www.xiaohongshu.com/user/profile/<user_id>?tab=fav&subTab=note

JSON: /api/sns/web/v2/note/collect/page responses (data.notes[]: note_id, display_title, type, user.nickname/user_id,
      cover.url_default, xsec_token) - the request is signed (x-s headers) by the page itself; we only read responses.
DOM:  section.note-item a[href*="/explore/<24-hex>"] or a[href*="/user/profile/<uid>/<24-hex>"] (+ .title, .author .name).
Opening a note needs its xsec_token, kept in extra.xsec_token (the stored URL stays canonical /explore/<id>).
User id: the sidebar "我" link (a[href*="/user/profile/"]); --account can be the 24-hex user id.
Un-collect (opt-in): note page .collect-wrapper toggle (collected state -> not collected).
"""
from __future__ import annotations
import logging, re
from urllib.parse import parse_qs, urlparse
from ..base import CollectorError, RunConfig, register
from ..logutil import ev
from ._common import FeedCollector, dig, first_url, raw, urls_of, walk

XHS_SEL = {
    "item": 'section.note-item, a[href*="/explore/"]',
    "logged_in": '.side-bar a[href*="/user/profile/"], li.user a[href*="/user/profile/"], .user.side-bar-component, a.link-wrapper[href*="/user/profile/"]',
    "login_form": '.login-container, .login-modal, #login-btn, button.login-btn, .qrcode-img',
    "empty": ':text("你还没有收藏任何内容"), :text("还没有收藏")',
    "me_link": '.side-bar a[href*="/user/profile/"], li.user a[href*="/user/profile/"], a[href*="/user/profile/"]:has-text("我")',
    "fav_tab": '.reds-tab-item:has-text("收藏"), .tab-item:has-text("收藏"), [role="tab"]:has-text("收藏"), .sub-tab-list span:text-is("收藏")',
    "unsave_on": '.collect-wrapper:has(.collected), .collect-wrapper.collected, .collect-wrapper:has(.active), .collect-wrapper:has(use[href="#collected"])',
    "unsave_off": '.collect-wrapper:not(.collected):not(:has(.collected)):not(:has(.active)):not(:has(use[href="#collected"]))',
}
XHS_LOGIN_RE = re.compile(r"^https?://(?:www\.)?xiaohongshu\.com/(?:login|website-login)", re.I)
XHS_NOTE_RE = re.compile(r"/(?:explore|discovery/item|user/profile/[0-9a-zA-Z]+)/([0-9a-f]{24})(?:[/?#]|$)")
XHS_UID_RE = re.compile(r"/user/profile/([0-9a-zA-Z]{16,32})")

XHS_EXTRACT_JS = r"""
(sel) => {
  const out = [], done = new Set();
  const cards = document.querySelectorAll('section.note-item');
  const list = cards.length ? [...cards] : [...document.querySelectorAll('a[href*="/explore/"]')].map(a => a.parentElement);
  for (const c of list) {
    if (!c || done.has(c)) continue; done.add(c);
    const links = [...c.querySelectorAll('a[href]')].map(a => a.getAttribute('href') || '');
    const note = links.find(h => /\/(explore|discovery\/item)\/[0-9a-f]{24}/.test(h)) || links.find(h => /\/user\/profile\/[0-9a-zA-Z]+\/[0-9a-f]{24}/.test(h)) || '';
    const author = c.querySelector('.author .name, .author-wrapper .name, .name');
    const authorA = links.find(h => /\/user\/profile\/[0-9a-zA-Z]+\/?(\?|$)/.test(h)) || '';
    const title = c.querySelector('.title span, .title, .note-title');
    const img = c.querySelector('img:not(.author-avatar):not([class*="avatar"])');
    out.push({href: note, author: author ? (author.innerText || author.textContent || '').trim() : '', author_href: authorA,
              title: title ? (title.innerText || title.textContent || '').trim() : '', media_url: img ? (img.getAttribute('src') || '') : '',
              avatar: ((c.querySelector('img.author-avatar, .author img, [class*="avatar"] img, img[class*="avatar"]') || {}).src || ''),
              video: !!c.querySelector('.play-icon, [class*="play"]'), likes: ((c.querySelector('.like-wrapper .count, .count') || {}).textContent || '').trim()});
  }
  return out;
}
"""


def is_xhs_note(d):
    nid = d.get("note_id") or d.get("noteId")
    return isinstance(nid, str) and bool(re.fullmatch(r"[0-9a-f]{24}", nid)) and \
        any(k in d for k in ("display_title", "displayTitle", "title", "user", "cover", "xsec_token", "xsecToken"))


def note_url(nid): return f"https://www.xiaohongshu.com/explore/{nid}"


@register
class XiaohongshuCollector(FeedCollector):
    platform, name = "xiaohongshu", "小红书 RED"
    home_url = "https://www.xiaohongshu.com/explore"
    login_url = "https://www.xiaohongshu.com/explore"
    saved_url = "https://www.xiaohongshu.com/user/profile/<user_id>?tab=fav&subTab=note"
    notes = ("收藏 notes. JSON-first (note/collect/page responses fired by the page), DOM fallback on section.note-item / "
             "24-hex note links. xsec_token kept in extra (needed to open notes). QR/SMS login. Un-collect: note page "
             ".collect-wrapper toggle.")
    SEL, LOGIN_URL_RE, EXTRACT_JS = XHS_SEL, XHS_LOGIN_RE, XHS_EXTRACT_JS
    XHR_RE = re.compile(r"xiaohongshu\.com/api/sns/web/v\d+/note/collect/page", re.I)

    def is_login_page(self, page) -> bool:
        if self._login_url(page): return True
        # XHS shows a login modal over any page when logged out, even if the page itself rendered
        if self._count(page, "logged_in") and not page.locator(".login-container:visible, .login-modal:visible").count(): return False
        return self._count(page, "login_form") > 0

    def parse_dom(self, r):
        m = XHS_NOTE_RE.search(r.get("href") or "")
        if not m: return None
        nid = m.group(1)
        q = parse_qs(urlparse(r["href"]).query)
        extra = {}
        if q.get("xsec_token"): extra["xsec_token"] = q["xsec_token"][0]
        if r.get("video"): extra["video"] = True
        if r.get("likes"): extra["likes"] = r["likes"]
        um = XHS_UID_RE.search(r.get("author_href") or "")
        if um: extra["author_user_id"] = um.group(1)
        title = r.get("title") or ""
        return raw(nid, note_url(nid), author=r.get("author") or "", handle=um.group(1) if um else "", title=title, text=title,
                   media_url=r.get("media_url") or "", has_media=bool(r.get("media_url")), extra=extra,
                   type="video" if r.get("video") else "note", avatar=r.get("avatar") or None)

    def json_raws(self, obj):
        out = []
        for d in walk(obj, is_xhs_note):
            nid = d.get("note_id") or d.get("noteId")
            user = d.get("user") or {}
            title = d.get("display_title") or d.get("displayTitle") or d.get("title") or ""
            extra = {}
            tok = d.get("xsec_token") or d.get("xsecToken")
            if tok: extra["xsec_token"] = tok
            if d.get("type") == "video": extra["video"] = True
            cover = first_url(dig(d, "cover", "url_default"), dig(d, "cover", "urlDefault"), dig(d, "cover", "url"),
                              dig(d, "cover", "info_list", 0, "url"))
            uid = user.get("user_id") or user.get("userId") or ""
            if uid: extra["author_user_id"] = uid
            imgs = urls_of(d.get("image_list") or d.get("imageList") or [])
            w, h = dig(d, "cover", "width", default=0), dig(d, "cover", "height", default=0)
            vid = d.get("type") == "video"
            out.append(raw(nid, note_url(nid), author=user.get("nickname") or user.get("nick_name") or user.get("nickName") or "",
                           handle=uid, title=title, text=d.get("desc") or title, media_url=cover, has_media=bool(cover), extra=extra,
                           type="video" if vid else "note", images=None if vid else (imgs or ([cover] if cover else None)),
                           video_url=first_url(dig(d, "video", "media", "stream", "h264", 0, "master_url")) or None,
                           duration=dig(d, "video", "capa", "duration", default=None) or None,
                           aspect=round(w / h, 4) if isinstance(w, (int, float)) and isinstance(h, (int, float)) and w and h else None,
                           avatar=first_url(user.get("avatar"), user.get("image")) or None))
        return out

    def resolve_user_id(self, page, cfg, session_file=""):
        h = (cfg.account_handle or "").strip().lstrip("@").lower()
        if re.fullmatch(r"[0-9a-f]{24}", h): return h
        self.open(page, self.home_url, cfg, session_file)
        for el in page.locator(self.SEL["me_link"]).all()[:5]:
            m = XHS_UID_RE.search(el.get_attribute("href") or "")
            if m: return m.group(1)
        raise CollectorError("could not find your Xiaohongshu user id (sidebar '我' link): pass --account <24-hex user id>")

    def targets(self, page, cfg, session_file=""):
        uid = self.resolve_user_id(page, cfg, session_file)
        ev("xiaohongshu.user", user_id=uid)
        return [(f"https://www.xiaohongshu.com/user/profile/{uid}?tab=fav&subTab=note", "")]

    def after_open(self, page, cfg, label):
        tab = page.locator(self.SEL["fav_tab"])
        if tab.count():
            try:
                cls = tab.first.get_attribute("class") or ""
                if "active" not in cls: tab.first.click(); page.wait_for_timeout(1500)
            except Exception as e: ev("xiaohongshu.tab_click_failed", logging.WARNING, error=str(e)[:200])

    def dom_raws(self, page):
        # the profile's own notes panel can be in the DOM too: once a fav tab exists it must be the active one
        tab = page.locator(self.SEL["fav_tab"])
        try:
            if tab.count() and "active" not in (tab.first.get_attribute("class") or ""): return []
        except Exception: pass
        return super().dom_raws(page)

    def unsave(self, page, item, cfg: RunConfig, session_file="") -> str:
        tok = item.extra.get("xsec_token")
        url = item.url + (f"?xsec_token={tok}&xsec_source=pc_collect" if tok else "")
        self.open_for_unsave(page, url, cfg, session_file)
        return self.toggle_unsave(page, self.SEL["unsave_on"], self.SEL["unsave_off"])
