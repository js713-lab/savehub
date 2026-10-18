"""One media schema for every item, whatever collector / importer / API call produced it.

Fields (all optional, stored as real columns on `items`):
  type              post | video | photo | note | file | contact
  thumbnail_url     cover image (first image, video poster, link preview)
  images            list of image URLs (photo / carousel / note posts), thumbnail first when known
  video_url         a DIRECT, playable video file URL (mp4/webm/m3u8); never a page URL
  embed_url         the platform's official embed page for the post (stored for reference; the UI does not iframe it)
  duration          video length in whole seconds
  aspect            width / height of the cover (e.g. 0.5625 for 9:16), so the UI can size the cover before it loads
  author_avatar_url the post author's avatar image

`normalize(d)` derives them from explicit fields, the legacy `media_url`, and the flags collectors keep in `raw`
(video / photo / note / carousel / kind / ...). URLs must be http(s) or a same-origin /static/ path; anything else
(javascript:, data:, file:) is dropped.
"""
from __future__ import annotations
import json, re

TYPES = ("post", "video", "photo", "note", "file", "contact")
FIELDS = ("type", "thumbnail_url", "images", "video_url", "embed_url", "duration", "aspect", "author_avatar_url")
MAX_IMAGES = 20
_CONTACT_KINDS = {"contact", "group", "chat"}
_VIDEO_EXT = re.compile(r"\.(mp4|m4v|webm|mov|m3u8)(?:[?#]|$)", re.I)


def clean_url(u) -> str:
    """http(s) URL or a same-origin /static/ path, else ''. Strips whitespace, caps the length."""
    if not isinstance(u, str): return ""
    u = u.strip()
    if len(u) > 4000: return ""
    if re.match(r"^https?://[^\s/]+", u, re.I) and not re.search(r"\s", u): return u
    if u.startswith("/static/") and ".." not in u and "//" not in u[1:]: return u
    return ""


def _images(v) -> list:
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            try: v = json.loads(s)
            except ValueError: v = []
        else: v = [s]
    out = []
    for x in v if isinstance(v, (list, tuple)) else []:
        if isinstance(x, dict): x = x.get("url") or x.get("src") or ""
        u = clean_url(x)
        if u and u not in out: out.append(u)
        if len(out) >= MAX_IMAGES: break
    return out


def parse_duration(v):
    """seconds (int/float/'83'), 'mm:ss' / 'h:mm:ss', ISO 'PT1M23S' -> whole seconds, or None."""
    if v is None or isinstance(v, bool): return None
    if isinstance(v, (int, float)): s = float(v)
    elif isinstance(v, str):
        t = v.strip()
        m = re.fullmatch(r"P?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", t, re.I)
        if re.fullmatch(r"\d+(?:\.\d+)?", t): s = float(t)
        elif re.fullmatch(r"\d{1,3}(?::\d{1,2}){1,2}", t):
            s = 0.0
            for part in t.split(":"): s = s * 60 + int(part)
        elif t and m and any(m.groups()):
            s = int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0)
        else: return None
    else: return None
    if not (0 < s < 86400 * 2): return None
    return max(1, int(round(s)))


def _aspect(v):
    try: a = float(v)
    except (TypeError, ValueError): return None
    return round(a, 4) if 0.2 <= a <= 5 else None


def aspect_from(w, h):
    try: w, h = float(w), float(h)
    except (TypeError, ValueError): return None
    return _aspect(w / h) if w > 0 and h > 0 else None


def _raw(d) -> dict:
    r = d.get("raw")
    if isinstance(r, str):
        try: r = json.loads(r)
        except ValueError: r = {}
    return r if isinstance(r, dict) else {}


def embed_for(platform: str, url: str) -> str:
    """Official embed page for a post URL (reference only)."""
    u = url or ""
    m = re.search(r"tiktok\.com/@[\w.-]+/video/(\d+)", u)
    if platform == "tiktok" and m: return f"https://www.tiktok.com/embed/v2/{m.group(1)}"
    m = re.search(r"instagram\.com/(?:[\w.]+/)?(p|reel|tv)/([\w-]+)", u)
    if platform == "instagram" and m: return f"https://www.instagram.com/{m.group(1)}/{m.group(2)}/embed"
    m = re.search(r"douyin\.com/video/(\d+)", u)
    if platform == "douyin" and m: return f"https://open.douyin.com/player/video?vid={m.group(1)}"
    m = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{11})", u)
    if m: return f"https://www.youtube.com/embed/{m.group(1)}"
    return ""


_PAGE_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "douyin.com", "www.douyin.com", "v.douyin.com",
               "instagram.com", "www.instagram.com", "threads.net", "www.threads.net", "x.com", "twitter.com", "mobile.twitter.com",
               "facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch", "xiaohongshu.com", "www.xiaohongshu.com",
               "xhslink.com", "youtube.com", "www.youtube.com", "youtu.be"}


def _page_host(u: str) -> bool:
    """True for a platform's web-page host (a post page is never a playable video file)."""
    m = re.match(r"https?://([^/:?#]+)", u or "", re.I)
    return bool(m) and m.group(1).lower() in _PAGE_HOSTS


def infer_type(platform: str, url: str, raw: dict, has_video: bool, n_images: int, explicit: str = "") -> str:
    if explicit in TYPES: return explicit
    kind = str(raw.get("kind") or "").lower()
    if kind in _CONTACT_KINDS: return "contact"
    if kind == "file" or (platform == "icloud" and ("path" in raw or "docwsid" in raw or "extension" in raw)): return "file"
    if platform == "icloud" and "/contacts/" in (url or ""): return "contact"
    if has_video or raw.get("video"): return "video"
    if raw.get("note") or platform == "xiaohongshu" and (n_images or raw.get("has_media")): return "note"
    if raw.get("photo") or raw.get("carousel") or n_images: return "photo"
    if re.search(r"/(?:video|reel|reels|watch)/|/v\.douyin\.com/", url or "") and platform in ("tiktok", "douyin", "instagram", "facebook"): return "video"
    return "post"


def normalize(d: dict) -> dict:
    """-> {field: value} for FIELDS, from an item dict (explicit fields win, then legacy media_url, then raw flags)."""
    raw = _raw(d)
    rm = raw.get("media") if isinstance(raw.get("media"), dict) else {}
    pick = lambda k: d.get(k) if d.get(k) not in (None, "", []) else rm.get(k)   # noqa: E731
    images = _images(pick("images"))
    thumb = clean_url(pick("thumbnail_url")) or clean_url(d.get("media_url")) or (images[0] if images else "")
    if thumb and not images and (raw.get("photo") or raw.get("carousel") or raw.get("note")): images = [thumb]
    video = clean_url(pick("video_url"))
    if video and ((not _VIDEO_EXT.search(video) and "video" not in video.lower()) or _page_host(video)): video = ""   # a page link is not a video file
    has_video = bool(video) or bool(raw.get("video"))
    typ = infer_type(d.get("platform") or "", d.get("url") or "", raw, has_video, len(images), str(pick("type") or "").lower())
    out = {"type": typ, "thumbnail_url": thumb or None, "images": images, "video_url": video or None,
           "embed_url": clean_url(pick("embed_url")) or embed_for(d.get("platform") or "", d.get("url") or "") or None,
           "duration": parse_duration(pick("duration")), "aspect": _aspect(pick("aspect")),
           "author_avatar_url": clean_url(pick("author_avatar_url")) or None}
    if typ != "video": out["duration"] = out["duration"] if video else None
    return out


def for_db(d: dict) -> dict:
    """normalize() with images serialised for the SQLite column."""
    n = normalize(d)
    n["images"] = json.dumps(n["images"], ensure_ascii=False)
    return n
