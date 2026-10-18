"""Platforms that cannot be collected automatically. None are left: WeChat now has an automatic collector (wechat.py,
the official web File Transfer Assistant). Keep this module so a future stub has a home:

When adding one: subclass StubCollector, explain exactly why it cannot be automated and what the closest automatic route
is in `notes`; when implementing one: subclass FeedCollector (see _common.py / meta.py) or Collector (x.py), keep
selectors in a SEL dict, add an HTML fixture under tests/fixtures/ and a fixture test, and keep un-save opt-in + verified.
"""
from ..base import StubCollector, register  # noqa: F401
