"""Placeholders for platforms not implemented yet. Each raises NotImplementedPlatform with its TODO.

When implementing one: subclass Collector (see x.py), keep selectors in a SEL dict, add an HTML fixture under
tests/fixtures/ and a parser test, and keep un-save opt-in + verified.
"""
from ..base import StubCollector, register


@register
class FacebookCollector(StubCollector):
    platform, name = "facebook", "Facebook"
    login_url = "https://www.facebook.com/login/"
    saved_url = "https://www.facebook.com/saved/"
    notes = ("saved list inside div[role='main']; each entry has a title link + 'Saved from' source line; collections "
             "at /saved/?list_id=...; un-save via entry '...' (aria-label='More') -> 'Unsave'. Obfuscated class names: "
             "rely on role/aria-label/text only.")


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
