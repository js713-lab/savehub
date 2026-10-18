"""X bookmarks via twscrape (https://github.com/vladkens/twscrape, MIT, pinned 0.20.1).

twscrape talks to X's own web GraphQL API (the Bookmarks timeline the x.com page itself calls) with the session cookies,
so it is immune to DOM/markup changes and returns full tweet JSON (text, author, media, quoted tweet, date).

Cookie conversion: storage_state -> {name: value} for x.com / twitter.com (in memory). twscrape insists on an SQLite
"accounts pool" file: it lives in a 0700 temp dir (file pre-created 0600) that is removed in finally, so no cookie
outlives the run. twscrape's PostHog telemetry is switched off (TWS_TELEMETRY=0, DO_NOT_TRACK=1) and its loguru output is
routed through our log scrubber. Never un-saves (read-only Bookmarks query).
"""
from __future__ import annotations
import asyncio, json, logging, os
from ..base import CollectedItem, RunConfig
from ..logutil import ev, scrub
from . import Accumulator, BackendError, cookie_dict, fail, private_file, private_tempdir, require

NAME, PLATFORM = "twscrape", "x"
DOMAINS = ("x.com", "twitter.com")
REQUIRED = ("auth_token", "ct0")


def _quiet_library():
    os.environ["TWS_TELEMETRY"] = "0"; os.environ["DO_NOT_TRACK"] = "1"     # no PostHog events from this process
    from loguru import logger
    logger.remove()
    logger.add(lambda m: ev("twscrape.log", logging.WARNING, msg=scrub(str(m).strip())[:300]), level="WARNING")


def _g(o, k, default=None):
    if isinstance(o, dict): return o.get(k, default)
    return getattr(o, k, default)


def map_tweet(t) -> CollectedItem | None:
    """twscrape Tweet (model or its .dict()) -> CollectedItem, same shape as the Playwright X collector."""
    tid = str(_g(t, "id_str") or _g(t, "id") or "")
    user = _g(t, "user") or {}
    handle = _g(user, "username") or ""
    if not tid.isdigit() or not handle: return None
    media = _g(t, "media") or {}
    photos, videos, animated = (_g(media, "photos") or []), (_g(media, "videos") or []), (_g(media, "animated") or [])
    first = (_g(photos[0], "url") if photos else "") or (_g(videos[0], "thumbnailUrl") if videos else "") or \
            (_g(animated[0], "thumbnailUrl") if animated else "") or ""
    extra = {"via": NAME}
    if photos: extra["photos"] = len(photos)
    if videos or animated: extra["video"] = True
    q = _g(t, "quotedTweet")
    if q: extra["quoted"] = {"text": _g(q, "rawContent") or "", "user": "@" + (_g(_g(q, "user") or {}, "username") or ""),
                             "url": _g(q, "url") or ""}
    if _g(t, "card"): extra["link_card"] = True
    links = [_g(l, "url") for l in (_g(t, "links") or []) if _g(l, "url")]
    if links: extra["links"] = links[:10]
    for k, ek in (("likeCount", "likes"), ("retweetCount", "reposts"), ("viewCount", "views")):
        if _g(t, k) is not None: extra[ek] = _g(t, k)
    d = _g(t, "date")
    posted = d.isoformat() if hasattr(d, "isoformat") else str(d or "")
    vid = (videos or animated or [None])[0]
    variants = sorted((v for v in (_g(vid, "variants") or []) if "mp4" in str(_g(v, "contentType") or "")),
                      key=lambda v: _g(v, "bitrate") or 0, reverse=True) if vid is not None else []
    dur = _g(vid, "duration") if vid is not None else None
    return CollectedItem(platform="x", url=f"https://x.com/{handle}/status/{tid}", external_id=tid,
                         author=_g(user, "displayname") or "", author_handle=handle, text=_g(t, "rawContent") or "",
                         posted_at=posted, has_media=bool(photos or videos or animated), media_url=first, extra=extra,
                         images=[u for u in (_g(p, "url") for p in photos) if u][:20],
                         video_url=(_g(variants[0], "url") or "") if variants else "",
                         duration=(dur / 1000 if isinstance(dur, (int, float)) and dur > 0 else None),
                         media_type="video" if vid is not None else ("photo" if photos else "post"),
                         author_avatar=_g(user, "profileImageUrl") or "")


async def _collect(cookies: dict, cfg: RunConfig, account: str, pool_file: str) -> list:
    from twscrape import API, AccountsPool
    pool = AccountsPool(pool_file, raise_when_no_account=True, wait_timeout=None)
    await pool.add_account_cookies(account or "savehub", json.dumps(cookies))
    api = API(pool, raise_when_no_account=True)
    acc = Accumulator(cfg)
    gen = api.bookmarks(limit=max(cfg.max_items, 20))
    try:
        async for tw in gen:
            if acc.add(map_tweet(tw)): break
    finally:
        await gen.aclose()
    ev("twscrape.done", count=len(acc.items), incremental_stop=acc.incremental_stop)
    return acc.items


def collect(state: dict, cfg: RunConfig, account: str = "") -> list[CollectedItem]:
    cookies = cookie_dict(state, DOMAINS)
    require(cookies, REQUIRED, "twscrape")
    _quiet_library()
    try:
        with private_tempdir("savehub-twscrape-") as d:
            pool_file = private_file(d, "accounts.db")
            return asyncio.run(_collect(cookies, cfg, account, pool_file))
    except BackendError: raise
    except Exception as e: raise fail(NAME, e) from None
    finally:
        cookies.clear()
