"""Structured logging: `event key=value ...` lines by default, JSON lines with --log-json."""
import json, logging, sys, time

log = logging.getLogger("savehub.collector")


class _Fmt(logging.Formatter):
    def __init__(self, as_json): super().__init__(); self.as_json = as_json

    def format(self, r):
        fields = getattr(r, "fields", {}) or {}
        if self.as_json:
            return json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(r.created)), "level": r.levelname.lower(),
                               "event": r.getMessage(), **fields}, ensure_ascii=False, default=str)
        kv = " ".join(f"{k}={json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, str) and (' ' in v or not v) else v}" for k, v in fields.items())
        return f"{time.strftime('%H:%M:%S', time.localtime(r.created))} {r.levelname:<7} {r.getMessage()}" + (f"  {kv}" if kv else "")


def setup(level="INFO", as_json=False):
    h = logging.StreamHandler(sys.stderr); h.setFormatter(_Fmt(as_json))
    log.handlers[:] = [h]; log.setLevel(getattr(logging, str(level).upper(), logging.INFO)); log.propagate = False


def ev(event, level=logging.INFO, **fields):
    log.log(level, event, extra={"fields": fields})
