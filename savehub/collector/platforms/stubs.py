"""Placeholders for platforms not implemented yet. Each raises NotImplementedPlatform with its TODO.

When implementing one: subclass Collector (see x.py), keep selectors in a SEL dict, add an HTML fixture under
tests/fixtures/ and a parser test, and keep un-save opt-in + verified.
"""
from ..base import StubCollector, register


@register
class InstagramCollector(StubCollector):
    platform, name = "instagram", "Instagram"
    login_url = "https://www.instagram.com/accounts/login/"
    saved_url = "https://www.instagram.com/<username>/saved/all-posts/"
    notes = ("grid links a[href^='/p/'], a[href^='/reel/'] (shortcode = id); author/caption need the post page or "
             "the GraphQL response (page.on('response') on /graphql/query); un-save = post page, "
             "svg[aria-label='Remove'] parent button. Logged-out: redirect to /accounts/login/.")


@register
class ThreadsCollector(StubCollector):
    platform, name = "threads", "Threads"
    login_url = "https://www.threads.com/login"
    saved_url = "https://www.threads.com/saved"
    notes = ("posts div[data-pressable-container] with a[href*='/post/'] permalink + time[datetime]; author a[href^='/@']; "
             "un-save via the post's '...' (svg[aria-label='More']) menu -> 'Unsave'. Shares the Instagram login.")


@register
class FacebookCollector(StubCollector):
    platform, name = "facebook", "Facebook"
    login_url = "https://www.facebook.com/login/"
    saved_url = "https://www.facebook.com/saved/"
    notes = ("saved list inside div[role='main']; each entry has a title link + 'Saved from' source line; collections "
             "at /saved/?list_id=...; un-save via entry '...' (aria-label='More') -> 'Unsave'. Obfuscated class names: "
             "rely on role/aria-label/text only.")


@register
class TikTokCollector(StubCollector):
    platform, name = "tiktok", "TikTok"
    login_url = "https://www.tiktok.com/login"
    saved_url = "https://www.tiktok.com/@<username> -> Favorites tab"
    notes = ("tab [data-e2e='favorites-tab']; items [data-e2e='user-post-item'] a[href*='/video/']; caption via "
             "[data-e2e='user-post-item-desc']; un-favorite on the video page [data-e2e='undefined-icon'] / favorite toggle. "
             "Expect captcha (secsdk) on automation.")


@register
class DouyinCollector(StubCollector):
    platform, name = "douyin", "抖音 Douyin"
    login_url = "https://www.douyin.com/"
    saved_url = "https://www.douyin.com/user/self?showTab=favorite_collection"
    notes = ("items a[href*='/video/'] inside the collection grid; QR-code login; strong anti-bot (slider captcha, "
             "signed XHR). Prefer intercepting /aweme/v1/web/aweme/listcollection/ JSON over DOM. Un-collect on video page.")


@register
class XiaohongshuCollector(StubCollector):
    platform, name = "xiaohongshu", "小红书 RED"
    login_url = "https://www.xiaohongshu.com/explore"
    saved_url = "https://www.xiaohongshu.com/user/profile/<user_id>?tab=fav&subTab=note"
    notes = ("notes section.note-item a[href*='/explore/'] (note id) + .title, .author .name; QR/SMS login; "
             "signed API (x-s headers) so read the DOM or intercept /api/sns/web/v2/note/collect/page. "
             "Un-collect: note page .collect-wrapper toggle.")


@register
class WeChatCollector(StubCollector):
    platform, name = "wechat", "WeChat 微信"
    saved_url = "(none on the web) - Favorites 收藏 exist only in the mobile/desktop apps"
    notes = ("web WeChat does not expose Favorites; needs desktop-app UI automation (not Playwright) or manual "
             "share-link import. Keep using the paste/CSV importer.")


@register
class WhatsAppCollector(StubCollector):
    platform, name = "whatsapp", "WhatsApp"
    login_url = "https://web.whatsapp.com/"
    saved_url = "https://web.whatsapp.com/ -> menu (span[data-icon='menu']) -> 'Starred messages'"
    notes = ("QR-code login; the session lives in IndexedDB which storage_state does NOT capture -> needs "
             "launch_persistent_context(user_data_dir) instead of a JSON session. Starred list: div[role='listitem']; "
             "un-star via message menu 'Unstar'.")


@register
class MessengerCollector(StubCollector):
    platform, name = "messenger", "Messenger"
    login_url = "https://www.messenger.com/login/"
    saved_url = "(no 'saved' view) - links shared in chats; Facebook saved covers 'Save link' from Messenger"
    notes = ("decide scope first (shared links per chat?). Chats are E2EE on web and load lazily; selectors "
             "div[role='row'] / a[href^='https://l.facebook.com/l.php']. No un-save concept.")
