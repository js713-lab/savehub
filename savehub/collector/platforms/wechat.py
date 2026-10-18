"""WeChat 微信 - automatic collector for the official web File Transfer Assistant (文件传输助手 / filehelper).

Why this route: WeChat has no API or web page for Favorites 收藏 and no web contact list for most accounts (web WeChat
wx.qq.com refuses login for accounts registered since ~2017; Tencent is retiring it). The one official web client that
logs in for every account is https://filehelper.weixin.qq.com/ (QR scan with the phone, like WhatsApp Web). It shows
the File Transfer chat - the place people send links, articles, notes, files and forwarded Favorites to themselves.
So the automatic WeChat route is: forward / send anything you want kept to 文件传输助手 (in the app: Favorites ->
item -> ... -> Send to Chat -> File Transfer), and auto-sync picks it up from here.

How it reads: the web app talks the documented web-WeChat protocol (webwxinit / webwxsync JSON, AddMsgList with
MsgId, MsgType, Content, Url, FileName, CreateTime). Those JSON responses are intercepted (no extra requests are made)
and the message list DOM is the fallback. Items: links -> url of the link/article, text notes / files / images ->
https://filehelper.weixin.qq.com/#msg/<MsgId>; external_id = MsgId (incremental auto-sync stops at stored ones).

Not possible from a Linux box (and why):
* Favorites 收藏 themselves: only inside the phone / desktop app (no web view, no API). Closest automatic route: the
  File Transfer forward above.
* Full contact list: needs web WeChat (blocked for most accounts), a paid iPad-protocol token (wechaty-puppet-padlocal)
  or a hooked Windows desktop client (wcferry) - none is a sanctioned, ban-safe route here. Not collected.
Read-only: nothing is deleted on WeChat (supports_unsave = False).
"""
from __future__ import annotations
import html, re
from ..base import register
from ._common import FeedCollector, iso_from_epoch, raw

HOME = "https://filehelper.weixin.qq.com/"
SEL = {
    "logged_in": '[data-msgid], .chat-panel, .msg-list, #chatInput, .chat__input, textarea.chat-input',
    "login_form": 'img.qrcode-img, .qrcode img, img[src*="qrcode"], .page-login, .login__qrcode',
    "item": "[data-msgid]",
    "empty": ".msg-list-empty",
    "scroller": ".msg-list, .chat-panel__body",
}
LINK_RE = re.compile(r"https?://[^\s<>\"'，。、）)]+")
TAG_RE = re.compile(r"<(title|des|url)>(.*?)</\1>", re.S)
MSG_TYPES = {1: "text", 3: "image", 34: "voice", 43: "video", 47: "sticker", 49: "app", 62: "video"}
APP_TYPES = {5: "link", 6: "file", 33: "mini_program", 36: "mini_program", 19: "chat_history", 24: "favorite_note", 57: "quote"}

EXTRACT_JS = r"""
(sel) => [...document.querySelectorAll(sel.item)].map(m => ({
  id: m.getAttribute('data-msgid') || '', time: m.getAttribute('data-time') || '',
  text: (m.querySelector('.msg-text, .text, .content') || m).innerText || '',
  title: ((m.querySelector('.app-title, .file-name, .title') || {}).innerText || '').trim(),
  links: [...m.querySelectorAll('a[href]')].map(a => a.href).filter(h => /^https?:/.test(h)),
  img: ((m.querySelector('img') || {}).src || '')}))
"""

SCROLL_UP_JS = r"""(scroller) => {
  for (const el of document.querySelectorAll(scroller)) el.scrollTop = 0;     // older messages load at the top
  window.scrollTo(0, 0);
}"""


def _app_fields(content: str) -> dict:
    x = html.unescape(content or "")
    return {k: html.unescape(v.strip()) for k, v in TAG_RE.findall(x) if v.strip() and "<" not in v}


def parse_msg(m: dict):
    """One AddMsgList entry -> normalized raw (or None for status/system messages)."""
    if not isinstance(m, dict): return None
    mid = str(m.get("MsgId") or m.get("NewMsgId") or "")
    mtype = int(m.get("MsgType") or 0)
    if not mid or mtype not in MSG_TYPES: return None          # 51 status notify, 10000/10002 system, ...
    to, frm = str(m.get("ToUserName") or ""), str(m.get("FromUserName") or "")
    if to and frm and "filehelper" not in (to, frm): return None     # only the File Transfer chat
    content = html.unescape(str(m.get("Content") or "")).replace("<br/>", "\n")
    kind = MSG_TYPES[mtype]
    extra = {"kind": "message", "msg_type": kind, "chat": "File Transfer 文件传输助手"}
    title, text, url = "", "", ""
    if mtype == 49:
        app = _app_fields(content)
        at = int(m.get("AppMsgType") or 0); extra["app_type"] = APP_TYPES.get(at, str(at))
        title = str(m.get("FileName") or app.get("title") or "")
        text = app.get("des") or ""
        url = str(m.get("Url") or app.get("url") or "")
        if m.get("FileSize"): extra["file_size"] = str(m["FileSize"])
    elif mtype == 1:
        text = content
        links = LINK_RE.findall(text)
        if links: url = links[0]; extra["links"] = links[:20]
    else:
        title = {"image": "[Image 图片]", "voice": "[Voice 语音]", "video": "[Video 视频]", "sticker": "[Sticker 表情]"}[kind]
    url = html.unescape(url).strip()
    if not url.startswith("http"): url = HOME + "#msg/" + mid
    return raw(mid, url, author="File Transfer 文件传输助手", handle="filehelper", text=text.strip(), title=title.strip(),
               posted_at=iso_from_epoch(m.get("CreateTime")), has_media=mtype in (3, 43, 47, 62) or extra.get("app_type") == "file",
               extra=extra)


@register
class WeChatCollector(FeedCollector):
    platform, name = "wechat", "WeChat 微信"
    kind = "saves"
    supports_unsave = False             # read-only: messages are never deleted on WeChat
    needs_indexed_db = True             # the web app may keep login keys in IndexedDB; saved with the session
    home_url = login_url = saved_url = HOME
    notes = ("AUTOMATIC via the official web File Transfer Assistant (filehelper.weixin.qq.com): scan the QR code once "
             "on the box (`python -m savehub.collector login wechat --account @<wechat id>`), then auto-sync collects every "
             "link, article, note and file in 文件传输助手. Favorites 收藏 have no web/API anywhere: forward them to File "
             "Transfer and they are collected. Contacts are not reachable from the web for most accounts. Read-only.")
    SEL = SEL
    LOGIN_URL_RE = re.compile(r"^https?://login\.(?:wx|weixin)\.qq\.com/", re.I)
    XHR_RE = re.compile(r"/cgi-bin/mmwebwx-bin/(?:webwxinit|webwxsync|webwxgetmsg)")
    EXTRACT_JS = EXTRACT_JS

    def targets(self, page, cfg, session_file=""):
        return [(HOME, "")]

    def is_login_page(self, page) -> bool:
        if self._login_url(page): return True
        if self._count(page, "item"): return False
        return self._count(page, "login_form") > 0

    def is_logged_in(self, page) -> bool:
        return not self.is_login_page(page) and self._count(page, "logged_in") > 0

    def json_raws(self, obj) -> list:
        if not isinstance(obj, dict): return []
        base = obj.get("BaseResponse") or {}
        if base and int(base.get("Ret") or 0) != 0: return []
        msgs = list(obj.get("AddMsgList") or []) + list(obj.get("MsgList") or [])
        out = [r for r in (parse_msg(m) for m in msgs) if r]
        out.sort(key=lambda r: r.get("posted_at") or "", reverse=True)         # newest first, like every saved list
        return out

    def parse_dom(self, r: dict):
        mid = str(r.get("id") or "")
        if not mid: return None
        links = r.get("links") or []
        text = (r.get("text") or "").strip()
        if not links: links = LINK_RE.findall(text)
        url = links[0] if links else HOME + "#msg/" + mid
        return raw(mid, url, author="File Transfer 文件传输助手", handle="filehelper", text=text, title=(r.get("title") or "").strip(),
                   posted_at=iso_from_epoch(r.get("time")), media_url=r.get("img") or "",
                   extra={"kind": "message", "chat": "File Transfer 文件传输助手", **({"links": links[:20]} if links else {})})

    def scroll(self, page):
        page.evaluate(SCROLL_UP_JS, self.SEL["scroller"])

    def unsave(self, page, item, cfg, session_file=""):
        return "failed"
