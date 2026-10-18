"""Authenticated image proxy + cache for remote thumbnails (GET /api/media?u=<url>).

Social CDNs often refuse hotlinked images (Referer / Origin checks) or hand out URLs that expire, so the dashboard
loads remote thumbnails through this endpoint instead of from the CDN. It is deliberately narrow:
  * http(s) only, default ports only, no credentials in the URL, host must be on ALLOWED_HOSTS (platform CDNs);
  * SSRF: the host is resolved and EVERY address must be public (no private, loopback, link-local, multicast,
    reserved, CGNAT or unspecified ranges); the connection is then pinned to the checked address (TLS still verifies
    the real host name via SNI), so DNS rebinding between check and connect does not work;
  * redirects are followed by hand (max 3) and every hop is checked again;
  * timeout, size cap (SAVEHUB_MEDIA_MAX_MB, default 8), content type must be a raster image (no SVG: it can carry
    script) and the bytes must look like that image type;
  * cached under <db dir>/media-cache (SHA-256 file names, 0600 files), total size capped (SAVEHUB_MEDIA_CACHE_MB,
    default 200), oldest entries evicted first.
"""
from __future__ import annotations
import hashlib, ipaddress, json, os, socket, threading, time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
import httpx

ALLOWED_HOSTS = (
    # X
    "pbs.twimg.com", "video.twimg.com", "abs.twimg.com",
    # Instagram / Threads / Facebook / Messenger
    "cdninstagram.com", "fbcdn.net", "fbsbx.com",
    # TikTok / Douyin (ByteDance CDNs)
    "tiktokcdn.com", "tiktokcdn-us.com", "tiktokcdn-eu.com", "ibytedtos.com", "byteimg.com", "douyinpic.com",
    "douyinstatic.com", "douyincdn.com", "bytecdn.cn", "pstatp.com",
    # 小红书
    "xhscdn.com", "xhscdn.net", "xiaohongshu.com",
    # WeChat articles, WhatsApp avatars
    "qpic.cn", "qlogo.cn", "whatsapp.net",
    # YouTube link previews
    "ytimg.com", "ggpht.com",
)
IMAGE_TYPES = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",), "image/gif": (b"GIF87a", b"GIF89a"),
               "image/webp": (b"RIFF",), "image/avif": (), "image/heic": (), "image/bmp": (b"BM",)}
MAX_BYTES = int(float(os.environ.get("SAVEHUB_MEDIA_MAX_MB", "8")) * 1024 * 1024)
CACHE_MAX = int(float(os.environ.get("SAVEHUB_MEDIA_CACHE_MB", "200")) * 1024 * 1024)
TIMEOUT = float(os.environ.get("SAVEHUB_MEDIA_TIMEOUT", "8"))
MAX_REDIRECTS = 3
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 SaveHub-media/1"
_lock = threading.Lock()


class MediaError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg); self.status = status


def host_allowed(host: str) -> bool:
    h = (host or "").lower().rstrip(".")
    return any(h == a or h.endswith("." + a) for a in ALLOWED_HOSTS)


def ip_public(ip: str) -> bool:
    try: a = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError: return False
    if a.version == 6 and a.ipv4_mapped: a = a.ipv4_mapped
    if a.version == 6 and a.sixtofour: a = a.sixtofour
    return a.is_global and not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved
                                or a.is_unspecified or a in ipaddress.ip_network("100.64.0.0/10"))


def resolve(host: str, port: int) -> list:
    """All addresses of host; raises MediaError unless every one is public (an attacker-controlled name that resolves
    to one public and one private address is refused, too)."""
    try: ipaddress.ip_address(host); raise MediaError(403, "IP-literal hosts are not allowed")
    except ValueError: pass
    try: infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror: raise MediaError(502, "host does not resolve") from None
    ips = sorted({i[4][0] for i in infos})
    if not ips: raise MediaError(502, "host does not resolve")
    bad = [ip for ip in ips if not ip_public(ip)]
    if bad: raise MediaError(403, "host resolves to a private/reserved address")
    return ips


def check_url(u: str):
    """-> (scheme, host, port, path+query) or MediaError. Only plain http(s) URLs to allow-listed CDN hosts."""
    if not isinstance(u, str) or len(u) > 4000: raise MediaError(400, "bad url")
    try: p = urlsplit(u.strip())
    except ValueError: raise MediaError(400, "bad url") from None
    if p.scheme not in ("http", "https"): raise MediaError(400, "only http(s) URLs")
    if p.username or p.password or "@" in p.netloc: raise MediaError(400, "credentials in URL are not allowed")
    host = (p.hostname or "").lower()
    try: port = p.port
    except ValueError: raise MediaError(400, "bad port") from None
    if port not in (None, 80, 443): raise MediaError(403, "non-default ports are not allowed")
    if not host or not host_allowed(host): raise MediaError(403, "host is not an allowed media CDN")
    path = urlunsplit(("", "", p.path or "/", p.query, ""))
    return p.scheme, host, port or (443 if p.scheme == "https" else 80), path


def cache_dir() -> Path:
    from . import db
    return Path(os.environ.get("SAVEHUB_MEDIA_CACHE") or (Path(db.DB_PATH).parent / "media-cache"))


def _key(u: str) -> str: return hashlib.sha256(u.encode("utf-8")).hexdigest()


def cache_get(u: str):
    d = cache_dir(); k = _key(u)
    f, m = d / (k + ".bin"), d / (k + ".json")
    try:
        meta = json.loads(m.read_text()); data = f.read_bytes()
        os.utime(f)                                         # LRU-ish: touched on hit
        return data, meta["content_type"]
    except (OSError, ValueError, KeyError): return None


def cache_put(u: str, data: bytes, ctype: str):
    d = cache_dir(); k = _key(u)
    old = os.umask(0o077)
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (k + ".tmp")
        tmp.write_bytes(data); os.replace(tmp, d / (k + ".bin"))
        (d / (k + ".json")).write_text(json.dumps({"content_type": ctype, "url_sha256": k, "stored": int(time.time())}))
    finally: os.umask(old)
    prune()


def cache_size() -> int:
    d = cache_dir()
    return sum(f.stat().st_size for f in d.glob("*.bin")) if d.is_dir() else 0


def prune(limit: int | None = None) -> int:
    """Evict least recently used entries until the cache is under its size cap. Returns how many were removed."""
    limit = CACHE_MAX if limit is None else limit
    d = cache_dir()
    if not d.is_dir(): return 0
    with _lock:
        files = sorted(d.glob("*.bin"), key=lambda f: f.stat().st_mtime)
        total, n = sum(f.stat().st_size for f in files), 0
        for f in files:
            if total <= limit: break
            total -= f.stat().st_size
            for x in (f, f.with_suffix(".json")):
                try: x.unlink()
                except OSError: pass
            n += 1
        return n


def sniff_ok(ctype: str, head: bytes) -> bool:
    if ctype in ("image/avif", "image/heic"): return head[4:8] == b"ftyp"
    if ctype == "image/webp": return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return any(head.startswith(m) for m in IMAGE_TYPES.get(ctype, ()))


def _fetch_once(client: httpx.Client, u: str):
    scheme, host, port, path = check_url(u)
    ip = resolve(host, port)[0]
    netloc = f"[{ip}]" if ":" in ip else ip
    if port not in (80, 443): netloc += f":{port}"   # pragma: no cover - check_url only allows default ports
    req = client.build_request("GET", f"{scheme}://{netloc}{path}", headers={"Host": host, "User-Agent": UA,
                               "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8"},
                               extensions={"sni_hostname": host} if scheme == "https" else {})
    return client.send(req, stream=True)


def fetch(u: str, client: httpx.Client | None = None):
    """-> (bytes, content_type). Cache first; otherwise a checked, pinned, size-capped download."""
    check_url(u)
    hit = cache_get(u)
    if hit: return hit
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(TIMEOUT), follow_redirects=False, trust_env=False)
    try:
        cur = u
        for _hop in range(MAX_REDIRECTS + 1):
            try: r = _fetch_once(client, cur)
            except httpx.HTTPError as e: raise MediaError(502, f"fetch failed: {type(e).__name__}") from None
            try:
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location") or ""
                    if not loc: raise MediaError(502, "redirect without location")
                    cur = urljoin(cur, loc); continue          # next hop goes through check_url + resolve again
                if r.status_code != 200: raise MediaError(502, f"upstream HTTP {r.status_code}")
                ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
                if ctype not in IMAGE_TYPES: raise MediaError(415, "not an image")
                cl = r.headers.get("content-length")
                if cl and cl.isdigit() and int(cl) > MAX_BYTES: raise MediaError(413, "image too large")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > MAX_BYTES: raise MediaError(413, "image too large")
                if not sniff_ok(ctype, bytes(buf[:16])): raise MediaError(415, "content does not match its image type")
                data = bytes(buf)
            finally: r.close()
            cache_put(u, data, ctype)
            return data, ctype
        raise MediaError(502, "too many redirects")
    finally:
        if own: client.close()
