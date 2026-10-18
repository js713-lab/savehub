"""Structured logging: `event key=value ...` lines by default, JSON lines with --log-json.

Every line goes through scrub(): values of secret-looking environment variables (GITHUB_TOKEN, SAVEHUB_*_PASSWORD,
SAVEHUB_VAULT_KEY, *_API_KEY, ...), the in-memory vault key, bearer/basic credentials, GitHub tokens, Cookie /
Set-Cookie / Authorization headers and cookie "value" fields are replaced with [REDACTED] before anything is written.
"""
import json, logging, os, re, sys, time

log = logging.getLogger("savehub.collector")
REDACTED = "[REDACTED]"
_SECRET_NAME = re.compile(r"PASSWORD|PASSWD|SECRET|TOKEN|VAULT|API_?KEY|PRIVATE|CREDENTIAL|COOKIE", re.I)
_PATTERNS = [
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), REDACTED),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + REDACTED),
    (re.compile(r"(?i)\b(set-cookie|cookie|authorization|x-csrf-token|x-auth-token)(\"?\s*[:=]\s*\"?)[^\"\n]{4,}"), r"\1\2" + REDACTED),
    (re.compile(r'(?i)("value"\s*:\s*")[^"]{4,}(")'), r"\1" + REDACTED + r"\2"),
    (re.compile(r"(?i)\b(auth_token|sessionid|sid|ds_user_id|c_user|xs|ct0|web_session|sessionid_ss|a1)=([^;\s&]{6,})"), r"\1=" + REDACTED),
    (re.compile(r"https?://[^/\s:@]+:[^/\s@]+@"), "https://" + REDACTED + "@"),
    (re.compile(r"(/connect/)[A-Za-z0-9_-]{16,}"), r"\1" + REDACTED),          # single-use MCP connect links
]
_LIVE_SECRETS: set = set()                     # passwords of login jobs in flight (never logged; scrubbed if they leak)


def register_secret(v: str):
    if v and len(v) >= 4: _LIVE_SECRETS.add(v)


def forget_secret(v: str):
    _LIVE_SECRETS.discard(v)


def _secret_values():
    vals = [v for k, v in os.environ.items() if _SECRET_NAME.search(k) and v and len(v) >= 8] + list(_LIVE_SECRETS)
    try:
        from savehub import vault
        if vault._runtime_key: vals.append(vault._runtime_key)
    except Exception:
        pass
    return sorted(set(vals), key=len, reverse=True)


def scrub(s) -> str:
    s = s if isinstance(s, str) else str(s)
    for v in _secret_values():
        if v in s: s = s.replace(v, REDACTED)
    for rx, rep in _PATTERNS: s = rx.sub(rep, s)
    return s


class ScrubFilter(logging.Filter):
    """Attach to any handler: scrubs the record's message and string args in place (keeps the args shape, which
    formatters such as uvicorn's access log rely on)."""
    def filter(self, record):
        try:
            if isinstance(record.msg, str): record.msg = scrub(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(scrub(a) if isinstance(a, str) else a for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: scrub(v) if isinstance(v, str) else v for k, v in record.args.items()}
        except Exception:
            pass
        return True


def install_scrubber(names=("", "uvicorn", "uvicorn.error", "uvicorn.access", "savehub", "mcp", "httpx")):
    """Add ScrubFilter to every handler of the given loggers (call after logging is configured)."""
    f = ScrubFilter()
    for n in names:
        for h in logging.getLogger(n).handlers:
            if not any(isinstance(x, ScrubFilter) for x in h.filters): h.addFilter(f)


class _Fmt(logging.Formatter):
    def __init__(self, as_json): super().__init__(); self.as_json = as_json

    def format(self, r):
        fields = getattr(r, "fields", {}) or {}
        if self.as_json:
            return scrub(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(r.created)), "level": r.levelname.lower(),
                                     "event": r.getMessage(), **fields}, ensure_ascii=False, default=str))
        kv = " ".join(f"{k}={json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, str) and (' ' in v or not v) else v}" for k, v in fields.items())
        return scrub(f"{time.strftime('%H:%M:%S', time.localtime(r.created))} {r.levelname:<7} {r.getMessage()}" + (f"  {kv}" if kv else ""))


def setup(level="INFO", as_json=False):
    h = logging.StreamHandler(sys.stderr); h.setFormatter(_Fmt(as_json))
    log.handlers[:] = [h]; log.setLevel(getattr(logging, str(level).upper(), logging.INFO)); log.propagate = False


def ev(event, level=logging.INFO, **fields):
    log.log(level, event, extra={"fields": fields})
