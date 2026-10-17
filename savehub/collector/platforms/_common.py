"""Shared engine for the browser collectors (everything except x.py, which predates it and stays self-contained).

A platform module subclasses FeedCollector and provides:
  SEL            selector dict (keys used here: logged_in, login_form, item, empty, scroller)
  LOGIN_URL_RE   regex: the page URL is a login/challenge page
  EXTRACT_JS     JS `(SEL) => [raw, ...]` run on every scroll (DOM strategy)
  parse_dom(r)   -> normalized raw dict or None
  XHR_RE + json_raws(obj)   JSON strategy: bodies of matching XHR/fetch responses (page.on('response')) and of
                 embedded <script type="application/json"> blobs (EMBEDDED_SEL) are walked for post objects.
                 JSON is usually richer and more stable than the obfuscated DOM; DOM is the fallback.
  targets()      list of (url, label) pages to scroll (label = collection name for Instagram collections)

Normalized raw dict: {id, url, author, handle, text, title, posted_at, media_url, has_media, extra}.
DOM and JSON results are merged by id: the first sighting creates the item, later sightings only fill empty fields.
"""
from __future__ import annotations
import hashlib, json, logging, random, re
from datetime import datetime, timezone
from ..base import Collector, CollectedItem, RunConfig
from ..logutil import ev

EMBEDDED_JS = r"""(sel) => sel ? [...document.querySelectorAll(sel)].map(s => s.textContent || "").filter(t => t.length > 20 && t.length < 8e6) : []"""

SCROLL_JS = r"""(scroller) => {
  window.scrollBy(0, Math.round(window.innerHeight * 0.85));
  if (scroller) for (const el of document.querySelectorAll(scroller)) el.scrollTop += Math.round((el.clientHeight || 600) * 0.85);
}"""

READY_JS = r"""([sels, loginRe]) => sels.some(s => { try { return s && document.querySelector(s); } catch (e) {
  // Playwright-only pseudo selectors (:text(), :has-text()) are not valid CSS: try the plain CSS alternatives only
  return s.split(/,(?![^(]*\))/).some(p => { try { return document.querySelector(p); } catch (e2) { return false; } }); } })
  || (loginRe && new RegExp(loginRe, 'i').test(location.href))"""


def iso_from_epoch(v) -> str:
    """1697000000 / 1697000000000 / '1697000000' -> '2023-10-11T04:53:20+00:00'; '' when not a timestamp."""
    try: v = float(v)
    except (TypeError, ValueError): return ""
    if v <= 0: return ""
    if v > 1e11: v /= 1000.0
    try: return datetime.fromtimestamp(v, timezone.utc).replace(microsecond=0).isoformat()
    except (OverflowError, OSError, ValueError): return ""


def walk(obj, pred, limit=5000):
    """Yield every dict in a JSON tree for which pred(d) is true (matched dicts are not descended into)."""
    stack, n = [obj], 0
    while stack and n < 200000:
        o = stack.pop(); n += 1
        if isinstance(o, dict):
            if pred(o):
                yield o; limit -= 1
                if limit <= 0: return
                continue
            stack.extend(reversed(list(o.values())))
        elif isinstance(o, list):
            stack.extend(reversed(o))


def dig(d, *path, default=""):
    """dig(post, 'user', 'username') with lists indexed by int; returns default on any miss/None."""
    for p in path:
        try: d = d[p]
        except (KeyError, IndexError, TypeError): return default
        if d is None: return default
    return d


def short_hash(s: str, n=16) -> str:
    return hashlib.sha1(s.encode("utf-8"), usedforsecurity=False).hexdigest()[:n]   # stable id, not a security hash


def first_url(*cands) -> str:
    for c in cands:
        if isinstance(c, str) and c.startswith("http"): return c
        if isinstance(c, list) and c and isinstance(c[0], str) and c[0].startswith("http"): return c[0]
    return ""


def raw(id, url, **kw) -> dict:
    d = {"id": str(id), "url": url, "author": "", "handle": "", "text": "", "title": "", "posted_at": "",
         "media_url": "", "has_media": False, "extra": {}}
    d.update({k: v for k, v in kw.items() if v is not None})
    return d


class FeedCollector(Collector):
    """Generic 'open saved page -> scroll -> extract (DOM + intercepted JSON) -> dedupe -> cap' collector."""
    SEL: dict = {}
    LOGIN_URL_RE = None
    EXTRACT_JS = "() => []"
    XHR_RE = None
    EMBEDDED_SEL = ""

    # ---------------------------------------------------------------- session state
    def _count(self, page, key) -> int:
        s = self.SEL.get(key)
        if not s: return 0
        try: return page.locator(s).count()
        except Exception: return 0

    def _login_url(self, page) -> bool:
        return bool(self.LOGIN_URL_RE and self.LOGIN_URL_RE.search(page.url or ""))

    def is_login_page(self, page) -> bool:
        if self._login_url(page): return True
        if self._count(page, "logged_in"): return False
        return self._count(page, "login_form") > 0

    def is_logged_in(self, page) -> bool:
        if self._login_url(page): return False
        return self._count(page, "logged_in") > 0 and not self._count(page, "login_wall")

    # ---------------------------------------------------------------- extraction
    def parse_dom(self, r: dict):
        return r

    def json_raws(self, obj) -> list:
        return []

    def dom_raws(self, page) -> list:
        try: rs = page.evaluate(self.EXTRACT_JS, self.SEL) or []
        except Exception as e:
            ev(f"{self.platform}.extract_error", logging.WARNING, error=str(e)[:200]); return []
        return [x for x in (self.parse_dom(r) for r in rs) if x]

    def embedded_raws(self, page) -> list:
        out = []
        if not self.EMBEDDED_SEL: return out
        try: blobs = page.evaluate(EMBEDDED_JS, self.EMBEDDED_SEL) or []
        except Exception: return out
        for b in blobs:
            try: out.extend(self.json_raws(json.loads(b)))
            except Exception: continue
        return out

    def _drain_xhr(self) -> list:
        out, pending = [], self._responses
        self._responses = []
        for resp in pending:
            try: body = resp.json()
            except Exception:
                try: body = json.loads(resp.text().removeprefix("for (;;);"))     # Meta prefixes some JSON
                except Exception: continue
            try: out.extend(self.json_raws(body))
            except Exception as e: ev(f"{self.platform}.json_error", logging.WARNING, url=resp.url[:120], error=str(e)[:200])
        return out

    def to_item(self, r: dict) -> CollectedItem:
        return CollectedItem(platform=self.platform, url=r["url"], external_id=r["id"], author=r.get("author") or "",
                             author_handle=r.get("handle") or "", text=r.get("text") or "", title=r.get("title") or "",
                             posted_at=r.get("posted_at") or "", has_media=bool(r.get("has_media") or r.get("media_url")),
                             media_url=r.get("media_url") or "", extra=dict(r.get("extra") or {}))

    @staticmethod
    def _fill(it: CollectedItem, r: dict):
        for f, k in (("author", "author"), ("author_handle", "handle"), ("text", "text"), ("title", "title"),
                     ("posted_at", "posted_at"), ("media_url", "media_url")):
            if not getattr(it, f) and r.get(k): setattr(it, f, r[k])
        if r.get("has_media") or r.get("media_url"): it.has_media = True
        for k, v in (r.get("extra") or {}).items(): it.extra.setdefault(k, v)

    def merge(self, raws, items: dict, seen_here: set, label: str, cap: int) -> int:
        """Merge raws into items (ordered dict id->item). Returns how many ids were seen for the first time on this page."""
        touched = 0
        for r in raws:
            if not r or not r.get("id") or not r.get("url"): continue
            rid = r["id"]
            if rid not in seen_here: seen_here.add(rid); touched += 1
            it = items.get(rid)
            if it is None:
                if len(items) >= cap: continue
                it = items[rid] = self.to_item(r)
            else:
                self._fill(it, r)
            if label:
                cols = it.extra.setdefault("collections", [])
                if label not in cols: cols.append(label)
        return touched

    # ---------------------------------------------------------------- navigation
    def targets(self, page, cfg: RunConfig, session_file="") -> list:
        return [(self.saved_url, "")]

    def after_open(self, page, cfg: RunConfig, label: str):
        """Hook after a target page loaded and the login check passed (e.g. click the Favorites tab)."""

    def wait_ready(self, page, timeout_ms):
        sels = [self.SEL.get(k) for k in ("item", "empty", "login_form", "logged_in") if self.SEL.get(k)]
        try:
            page.wait_for_function(READY_JS, arg=[sels, self.LOGIN_URL_RE.pattern if self.LOGIN_URL_RE else ""], timeout=timeout_ms)
        except Exception as e:
            ev(f"{self.platform}.wait_ready_timeout", logging.WARNING, url=page.url, error=type(e).__name__)

    def wait_items(self, page, timeout_ms=8000):
        sels = [self.SEL.get(k) for k in ("item", "empty") if self.SEL.get(k)]
        if not sels: return
        try: page.wait_for_function(READY_JS, arg=[sels, ""], timeout=timeout_ms)
        except Exception: pass

    def open(self, page, url, cfg: RunConfig, session_file=""):
        ev(f"{self.platform}.open", url=url)
        page.goto(url, wait_until="domcontentloaded", timeout=cfg.page_timeout_ms)
        self.wait_ready(page, cfg.page_timeout_ms)
        self.ensure_logged_in(page, session_file)

    def scroll(self, page):
        page.evaluate(SCROLL_JS, self.SEL.get("scroller") or "")

    # ---------------------------------------------------------------- collect
    def collect(self, page, cfg: RunConfig, session_file="") -> list[CollectedItem]:
        self._responses = []
        listener = (lambda resp: self._responses.append(resp) if self.XHR_RE.search(resp.url) else None) if self.XHR_RE else None
        if listener: page.on("response", listener)
        items: dict = {}
        try:
            for url, label in self.targets(page, cfg, session_file):
                self._scroll_target(page, cfg, session_file, url, label, items)
        finally:
            if listener:
                try: page.remove_listener("response", listener)
                except Exception: pass
        return list(items.values())[: cfg.max_items]

    def _scroll_target(self, page, cfg, session_file, url, label, items):
        self.open(page, url, cfg, session_file)
        self.after_open(page, cfg, label)
        self.wait_items(page)
        self.scroll_loop(page, cfg, session_file, label, items)

    def scroll_loop(self, page, cfg, session_file, label, items, extract=None, scroll=None):
        """Scroll the current page until max_items / idle_rounds / max_scrolls; merge DOM + XHR (+ embedded once)."""
        extract, scroll = extract or self.dom_raws, scroll or self.scroll
        seen_here: set = set()
        self.merge(self.embedded_raws(page), items, seen_here, label, cfg.max_items)
        idle, P = 0, self.platform
        for n in range(cfg.max_scrolls):
            raws = self._drain_xhr() + extract(page)
            before = len(items)
            touched = self.merge(raws, items, seen_here, label, cfg.max_items)
            ev(f"{P}.scroll", round=n, page=label or "saved", new=len(items) - before, seen=len(seen_here), total=len(items))
            if len(items) >= cfg.max_items and not label: ev(f"{P}.cap_reached", max_items=cfg.max_items); break
            if cfg.stop_after_known and self.incremental_done(cfg, (items[r] for r in seen_here if r in items)):
                ev(f"{P}.incremental_stop", page=label or "saved", known_seen=cfg.stop_after_known, total=len(items)); break
            if touched: idle = 0
            else:
                if n == 0 and not seen_here and self._count(page, "empty"): ev(f"{P}.empty", page=label or "saved"); break
                idle += 1
                if idle >= cfg.idle_rounds: ev(f"{P}.end_of_list", page=label or "saved", idle_rounds=idle); break
            if self.is_login_page(page): self.ensure_logged_in(page, session_file)
            scroll(page)
            page.wait_for_timeout(int(1000 * (cfg.scroll_delay + random.uniform(0, cfg.scroll_jitter))))

    # ---------------------------------------------------------------- un-save helpers
    def open_for_unsave(self, page, url, cfg: RunConfig, session_file="", ready_keys=("unsave_on", "unsave_off", "more")):
        page.goto(url, wait_until="domcontentloaded", timeout=cfg.page_timeout_ms)
        sels = [self.SEL[k] for k in ready_keys if self.SEL.get(k)] + [self.SEL.get("login_form") or ""]
        try: page.wait_for_function(READY_JS, arg=[sels, self.LOGIN_URL_RE.pattern if self.LOGIN_URL_RE else ""], timeout=cfg.page_timeout_ms)
        except Exception: ev(f"{self.platform}.unsave.page_timeout", logging.WARNING, url=url)
        self.ensure_logged_in(page, session_file)

    def toggle_unsave(self, root, on_sel, off_sel, timeout_ms=6000) -> str:
        """Saved-state toggle: click the first 'saved' control, then require that the number of 'saved' controls
        dropped AND a 'not saved' control is present (so an unrelated 'Save' button elsewhere cannot fake success)."""
        on = root.locator(on_sel)
        n_on = on.count()
        if n_on:
            on.first.click()
            waited = 0
            while waited <= timeout_ms:
                try:
                    if root.locator(on_sel).count() < n_on and root.locator(off_sel).count(): return "unsaved"
                except Exception: pass
                getattr(root, "page", root).wait_for_timeout(250)
                waited += 250
            return "failed"
        if root.locator(off_sel).count(): return "already_unsaved"
        return "failed"

    def menu_unsave(self, page, root, more_sel, item_sel, saved_off_sel="", timeout_ms=5000) -> str:
        """'...' menu -> 'Unsave'; verified by re-opening the menu and checking the item is gone."""
        more = root.locator(more_sel)
        if not more.count(): return "failed"
        menu_ready = ", ".join(x for x in (item_sel, saved_off_sel, '[role="menuitem"]', '[role="menu"]') if x)
        more.first.click()
        try: page.wait_for_selector(menu_ready, timeout=timeout_ms)
        except Exception: page.keyboard.press("Escape"); return "failed"
        mi = page.locator(item_sel)
        if not mi.count():
            gone = bool(saved_off_sel and page.locator(saved_off_sel).count())
            page.keyboard.press("Escape"); return "already_unsaved" if gone else "failed"
        mi.first.click()
        page.wait_for_timeout(1500)
        try:
            root.locator(more_sel).first.click()
            page.wait_for_selector(menu_ready, timeout=timeout_ms)
            still = page.locator(item_sel).count() > 0
        except Exception:
            return "failed"
        finally:
            page.keyboard.press("Escape")
        return "failed" if still else "unsaved"


MARK_JS = r"""([containerSel, needle]) => {
  document.querySelectorAll('[data-savehub-target]').forEach(e => e.removeAttribute('data-savehub-target'));
  for (const c of document.querySelectorAll(containerSel)) {
    if ([...c.querySelectorAll('a[href]')].some(a => (a.getAttribute('href') || '').includes(needle))) {
      // innermost matching container wins (containers can nest)
      const inner = [...c.querySelectorAll(containerSel)].filter(x => [...x.querySelectorAll('a[href]')].some(a => (a.getAttribute('href') || '').includes(needle)));
      (inner.length ? inner[0] : c).setAttribute('data-savehub-target', '1'); return true; } }
  return false; }"""


def mark_target(page, container_sel, needle):
    """Tag the container holding a link containing `needle` with data-savehub-target=1; returns its locator or None."""
    try: ok = page.evaluate(MARK_JS, [container_sel, needle])
    except Exception: ok = False
    return page.locator('[data-savehub-target="1"]').first if ok else None
