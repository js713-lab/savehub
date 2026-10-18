"""Instagram saved posts via Instaloader (https://github.com/instaloader/instaloader, MIT, pinned 4.15.4).

Uses Instaloader's own `Profile.get_saved_posts()` (Instagram web GraphQL `edge_saved_media`) with the session cookies
loaded IN MEMORY (`Instaloader.load_session(username, cookie_dict)` - nothing is written to disk, no session file, no
resume file, no downloads). Profile id comes from the ds_user_id cookie, username from --account (else test_login()).
Limits: no collection names (the browser path adds extra.collections); Instagram throttles this GraphQL endpoint at times -
the rate controller fails fast instead of sleeping for minutes so `auto` can fall back to the browser. Never un-saves.
"""
from __future__ import annotations
import logging, time
from ..base import CollectedItem, RunConfig
from ..logutil import ev
from . import Accumulator, BackendError, cookie_dict, fail, require
from ..platforms._common import dig, iso_from_epoch

NAME, PLATFORM = "instaloader", "instagram"
DOMAINS = ("instagram.com",)
REQUIRED = ("sessionid", "csrftoken", "ds_user_id")
MAX_SLEEP_S = 20


def map_node(n: dict) -> CollectedItem | None:
    """GraphQL saved-media node (Post._node) -> CollectedItem, same URL/id scheme as the Playwright collector."""
    code = n.get("shortcode") or n.get("code")
    if not code: return None
    reel = n.get("product_type") == "clips"
    url = f"https://www.instagram.com/{'reel' if reel else 'p'}/{code}/"
    owner = n.get("owner") or {}
    cap = dig(n, "edge_media_to_caption", "edges", 0, "node", "text") or dig(n, "caption", "text") or \
          (n.get("caption") if isinstance(n.get("caption"), str) else "") or ""
    tn = n.get("__typename") or ""
    extra = {"via": NAME}
    if n.get("is_video") or tn == "GraphVideo" or reel: extra["video"] = True
    side = dig(n, "edge_sidecar_to_children", "edges", default=[]) or []
    if tn == "GraphSidecar" or side: extra["carousel"] = len(side) or True
    if n.get("id"): extra["pk"] = str(n["id"])
    if reel: extra["product_type"] = "clips"
    if n.get("accessibility_caption"): extra["alt"] = str(n["accessibility_caption"])[:500]
    media = n.get("display_url") or n.get("thumbnail_src") or ""
    imgs = [u for u in (dig(e, "node", "display_url") for e in side) if isinstance(u, str) and u.startswith("http")] or ([media] if media and not extra.get("video") else [])
    dims = n.get("dimensions") or {}
    try: aspect = round(float(dims["width"]) / float(dims["height"]), 4)
    except (KeyError, TypeError, ValueError, ZeroDivisionError): aspect = None
    return CollectedItem(platform="instagram", url=url, external_id=str(code), author=owner.get("full_name") or "",
                         author_handle=owner.get("username") or "", text=cap,
                         posted_at=iso_from_epoch(n.get("taken_at_timestamp") or n.get("taken_at")),
                         has_media=bool(media), media_url=media, extra=extra,
                         images=imgs[:20], video_url=n.get("video_url") or "", duration=n.get("video_duration"),
                         media_type="video" if extra.get("video") else "photo", author_avatar=owner.get("profile_pic_url") or "",
                         aspect=aspect)


def _loader():
    import instaloader

    class FailFast(instaloader.RateController):
        def sleep(self, secs):
            if secs > MAX_SLEEP_S:
                raise BackendError(f"instaloader: Instagram rate limit (would wait {round(secs)}s); falling back")
            time.sleep(secs)

    return instaloader, instaloader.Instaloader(
        sleep=True, quiet=True, download_pictures=False, download_videos=False, download_video_thumbnails=False,
        download_geotags=False, download_comments=False, save_metadata=False, compress_json=False,
        max_connection_attempts=1, request_timeout=30, rate_controller=lambda ctx: FailFast(ctx),
        resume_prefix=None, iphone_support=False, fatal_status_codes=[400, 401, 403, 429])


def collect(state: dict, cfg: RunConfig, account: str = "") -> list[CollectedItem]:
    cookies = cookie_dict(state, DOMAINS)
    require(cookies, REQUIRED, "instaloader")
    try:
        il, L = _loader()
        user = (account or "").strip().lstrip("@").lower()
        uid = cookies["ds_user_id"]
        L.load_session(user or "_", dict(cookies))               # cookie jar in memory only
        if not user or user == "default":
            user = L.test_login()
            if not user: raise BackendError("instaloader: Instagram did not accept the session (log in again on the box)")
            L.context.username = user
        prof = il.Profile(L.context, {"id": int(uid), "username": user})
        acc = Accumulator(cfg)
        for post in prof.get_saved_posts():
            node = getattr(post, "_node", None) or {}
            if not node.get("shortcode"):
                node = {"shortcode": post.shortcode, **node}
            if acc.add(map_node(node)): break
        ev("instaloader.done", count=len(acc.items), incremental_stop=acc.incremental_stop)
        return acc.items
    except BackendError: raise
    except Exception as e: raise fail(NAME, e) from None
    finally:
        cookies.clear()
