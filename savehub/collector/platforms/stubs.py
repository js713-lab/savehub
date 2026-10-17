"""Platforms that cannot be collected with a browser. Each raises NotImplementedPlatform with the reason.

When implementing one: subclass FeedCollector (see _common.py / meta.py) or Collector (x.py), keep selectors in a SEL
dict, add an HTML fixture under tests/fixtures/ and a fixture test, and keep un-save opt-in + verified.
"""
from ..base import StubCollector, register


@register
class WeChatCollector(StubCollector):
    platform, name = "wechat", "WeChat 微信"
    saved_url = "(none on the web) - Favorites 收藏 and the contact list exist only in the mobile/desktop apps"
    notes = ("intentionally NOT implemented - WeChat has no usable web client: (1) web WeChat (wx.qq.com / "
             "web.wechat.com) refuses login for most accounts ('此微信号不能登录网页微信' - blocked for accounts registered "
             "since ~2017) and Tencent has been retiring it; (2) even where it logs in it only shows recent chats - no "
             "Favorites 收藏 and no full contact list; (3) the Windows/macOS desktop app is a native client (no DOM), so "
             "it would need OS-level UI automation (UIAutomation/Accessibility), not Playwright, and that risks account "
             "bans. Use the paste/CSV/JSON link importer (share links / article links) instead.")
