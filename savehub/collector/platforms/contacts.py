"""CONTACT collectors for chat apps that have no web 'saved posts' view: Messenger and WhatsApp Web.

Items are stored like saves (same schema/sink) with extra.kind = "contact" (or "chat"/"group"/"starred_message"),
title = contact/chat name, so they are searchable in SaveHub and linked to the account they came from.
There is nothing to un-save: supports_unsave = False (--unsave is refused).

Messenger  https://www.messenger.com/ (or facebook.com/messages/ - Meta is folding messenger.com into it)
           chat list rows a[href*="/t/<thread id>"] (+ /e2ee/t/) in the Chats navigation; name = first span[dir=auto],
           last-message preview = second. Scrolls the list's own scroll container.
WhatsApp   https://web.whatsapp.com/  - QR login; the login keys live in IndexedDB, so this collector's session file
           also stores IndexedDB (Playwright >= 1.51, needs_indexed_db).
           1. IndexedDB 'model-storage' -> 'contact' + 'chat' stores (ids like 60123456789@c.us / ...@g.us): the full
              address book incl. phone numbers -> https://wa.me/<number>. Most complete and stable.
           2. fallback: the chat list DOM (#pane-side rows, span[title] names).
           3. starred messages (best-effort): menu -> "Starred messages" panel rows.
"""
from __future__ import annotations
import logging, re
from urllib.parse import quote
from ..base import RunConfig, register
from ..logutil import ev
from ._common import FeedCollector, iso_from_epoch, raw, short_hash

LIST_SCROLL_JS = r"""(rowSel) => {
  const rows = document.querySelectorAll(rowSel);
  let el = rows.length ? rows[rows.length - 1] : null, done = false;
  while (el && el !== document.body) {
    const s = getComputedStyle(el);
    if (el.scrollHeight > el.clientHeight + 4 && /(auto|scroll)/.test(s.overflowY)) { el.scrollTop += Math.round(el.clientHeight * 0.85); done = true; break; }
    el = el.parentElement;
  }
  if (!done) window.scrollBy(0, Math.round(window.innerHeight * 0.85));
}"""


class ContactsCollector(FeedCollector):
    kind = "contacts"
    supports_unsave = False

    def targets(self, page, cfg, session_file=""):
        return [(self.home_url, "")]          # saved_url is descriptive only for contact collectors

    def scroll(self, page):
        page.evaluate(LIST_SCROLL_JS, self.SEL["row"])

    def unsave(self, page, item, cfg, session_file=""):
        return "failed"


# ====================================================================== Messenger
MS_SEL = {
    "row": 'a[href*="/t/"]',
    "item": 'a[href*="/t/"]',
    "logged_in": '[aria-label="Chats"], [aria-label="New message"], a[href*="/t/"], [aria-label="Thread list"], [aria-label="Conversation list"]',
    "login_form": 'input[name="email"], input[name="pass"], #loginbutton, button[name="login"], form[action*="login"]',
    "continue_as": 'div[role="button"]:has-text("Continue as"), button:has-text("Continue as"), [aria-label*="Continue as" i]',
}
MS_LOGIN_RE = re.compile(r"^https?://(?:www\.)?(?:messenger|facebook)\.com/(?:login|checkpoint|recover)", re.I)
MS_THREAD_RE = re.compile(r"^(?:https?://(?:www\.)?(messenger|facebook)\.com)?(/messages)?/(e2ee/)?t/([\w.-]+)")

MS_EXTRACT_JS = r"""
(sel) => {
  const nav = document.querySelector('[aria-label="Chats"]') || document.querySelector('[role="navigation"]') || document;
  const out = [];
  for (const a of nav.querySelectorAll('a[href*="/t/"]')) {
    const spans = [...a.querySelectorAll('span[dir="auto"]')].filter(s => !s.querySelector('span[dir="auto"]'))
      .map(s => (s.innerText || s.textContent || '').trim()).filter(Boolean);
    const img = a.querySelector('img, image');
    out.push({href: a.getAttribute('href') || '', host: location.hostname, aria: a.getAttribute('aria-label') || '', name: spans[0] || '',
              preview: spans.slice(1).join(' ').trim(), unread: !!a.querySelector('[aria-label*="unread" i], [aria-label="Mark as read"]'),
              media_url: img ? (img.getAttribute('src') || img.getAttribute('xlink:href') || img.getAttribute('href') || '') : ''});
  }
  return out;
}
"""


@register
class MessengerCollector(ContactsCollector):
    platform, name = "messenger", "Messenger"
    home_url = "https://www.messenger.com/"
    login_url = "https://www.messenger.com/login/"
    saved_url = "https://www.messenger.com/ (chat list -> contacts)"
    notes = ("CONTACTS collector (Messenger has no saved-posts view): every conversation in the chat list (person/group, "
             "thread URL, avatar, last-message preview in extra). Works on messenger.com or facebook.com/messages "
             "(falls back automatically; uses Continue as when Facebook cookies are present). "
             "Message contents are E2EE and not read. No un-save.")
    SEL, LOGIN_URL_RE, EXTRACT_JS = MS_SEL, MS_LOGIN_RE, MS_EXTRACT_JS
    FB_MESSAGES = "https://www.facebook.com/messages/"

    def is_login_page(self, page) -> bool:
        u = (page.url or "").lower()
        if "checkpoint" in u: return True
        return super().is_login_page(page)

    def ensure_logged_in(self, page, session_file) -> None:
        from ..base import NeedLogin
        u = page.url or ""
        if "checkpoint" in u.lower():
            raise NeedLogin(self.platform, session_file,
                            f"checkpoint - verification code needed ({u})")
        super().ensure_logged_in(page, session_file)

    def _click_continue_as(self, page) -> bool:
        sel = self.SEL.get("continue_as") or ""
        if not sel: return False
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=1500):
                loc.click(timeout=5000)
                page.wait_for_timeout(2000)
                return True
        except Exception:
            pass
        return False

    def _inbox_ready(self, page) -> bool:
        return self.is_logged_in(page) and not self.is_login_page(page)

    def open(self, page, url, cfg, session_file=""):
        """messenger.com first (Continue as if FB cookies present), then facebook.com/messages."""
        from ..logutil import ev
        for attempt, target in enumerate((url or self.home_url, self.FB_MESSAGES)):
            ev("messenger.open", url=target, attempt=attempt)
            page.goto(target, wait_until="domcontentloaded", timeout=cfg.page_timeout_ms)
            self.wait_ready(page, min(cfg.page_timeout_ms, 20000))
            if self._inbox_ready(page):
                return
            if self._click_continue_as(page):
                self.wait_ready(page, min(cfg.page_timeout_ms, 20000))
                if self._inbox_ready(page):
                    return
            # cookie consent / "Not now" for notifications — ignore failures
            for dismiss in ('div[role="button"]:has-text("Not now")', 'button:has-text("Not Now")',
                            'div[role="button"]:has-text("Allow all cookies")'):
                try:
                    d = page.locator(dismiss).first
                    if d.count() and d.is_visible(timeout=400): d.click(timeout=2000); page.wait_for_timeout(800)
                except Exception: pass
            if self._inbox_ready(page):
                return
        self.ensure_logged_in(page, session_file)

    def targets(self, page, cfg, session_file=""):
        # Prefer messenger.com; open() falls back to facebook.com/messages if needed
        return [(self.home_url, "")]

    def parse_dom(self, r):
        m = MS_THREAD_RE.match(r.get("href") or "")
        if not m: return None
        site, msgs, e2ee, tid = m.groups()
        host = r.get("host") or ""
        if "facebook.com" in host or site == "facebook" or msgs:
            url = f"https://www.facebook.com/messages/{e2ee or ''}t/{tid}/"
        else:
            url = f"https://www.messenger.com/{e2ee or ''}t/{tid}/"
        name = r.get("name") or re.sub(r"^(?:Chat with|Conversation with)\s+", "", r.get("aria") or "").strip()
        if not name: return None
        extra = {"kind": "contact", "thread_id": tid}
        if e2ee: extra["e2ee"] = True
        if r.get("preview"): extra["last_message"] = r["preview"][:300]
        if r.get("unread"): extra["unread"] = True
        return raw(tid, url, author=name, title=name, text="", media_url=r.get("media_url") or "", has_media=False, extra=extra,
                   type="contact", avatar=r.get("media_url") or None)


# ====================================================================== WhatsApp
WA_SEL = {
    "row": '#pane-side [role="listitem"], #pane-side [role="row"], [aria-label="Chat list"] [role="listitem"], [aria-label="Chat list"] [role="row"]',
    "item": '#pane-side, [aria-label="Chat list"]',
    "logged_in": '#pane-side, [aria-label="Chat list"], [data-testid="chat-list"]',
    "login_form": 'canvas[aria-label*="scan" i], div[data-ref] canvas, [data-testid="qrcode"], [data-ref]',
    "menu": '[aria-label="Menu"], span[data-icon="menu"], span[data-icon="more-refreshed"]',
    "starred_entry": '[aria-label="Starred messages"], li:has-text("Starred messages"), div[role="button"]:has-text("Starred messages")',
    "starred_row": '[data-savehub-starred] [role="listitem"], [data-savehub-starred] [role="row"]',
}

WA_IDB_JS = r"""
async () => {
  try {
    if (indexedDB.databases) { const dbs = await indexedDB.databases(); if (!dbs.some(d => d.name === 'model-storage')) return null; }
    const db = await new Promise((res, rej) => { const r = indexedDB.open('model-storage');
      r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
      r.onupgradeneeded = (e) => { e.target.transaction.abort(); rej('absent'); }; });
    const read = (store) => new Promise(res => { if (!db.objectStoreNames.contains(store)) return res([]);
      const q = db.transaction(store, 'readonly').objectStore(store).getAll(); q.onsuccess = () => res(q.result || []); q.onerror = () => res([]); });
    const sid = (v) => typeof v === 'string' ? v : (v && (v._serialized || (v.user && v.server && v.user + '@' + v.server))) || '';
    const contacts = (await read('contact')).map(c => ({id: sid(c.id), name: c.name || '', short: c.shortName || '', push: c.pushname || '',
      biz: !!c.isBusiness, mine: !!c.isAddressBookContact || !!c.isMyContact, verified: c.verifiedName || ''}));
    const chats = (await read('chat')).map(c => ({id: sid(c.id), name: c.name || c.formattedTitle || '', t: c.t || 0, unread: c.unreadCount || 0, archive: !!c.archive}));
    const groups = (await read('group-metadata')).map(g => ({id: sid(g.id), subject: g.subject || ''}));
    db.close();
    return {contacts, chats, groups};
  } catch (e) { return null; }
}
"""

WA_DOM_JS = r"""
(sel) => {
  const out = [];
  for (const row of document.querySelectorAll(sel.row)) {
    const titles = [...row.querySelectorAll('span[title]')].map(s => s.getAttribute('title')).filter(Boolean);
    if (!titles.length) continue;
    const img = row.querySelector('img');
    out.push({name: titles[0], preview: titles.slice(1).join(' '), media_url: img ? (img.getAttribute('src') || '') : '',
              group: !!row.querySelector('[data-icon="default-group"], [data-icon="default-group-refreshed"]')});
  }
  return out;
}
"""

WA_STARRED_JS = r"""
(sel) => {
  const hdr = [...document.querySelectorAll('header, h1, div[title], span')].find(e => /^Starred messages$/i.test((e.textContent || '').trim()));
  if (!hdr) return null;
  let panel = hdr; for (let i = 0; i < 8 && panel.parentElement; i++) { panel = panel.parentElement; if (panel.querySelector('[role="listitem"], [role="row"]')) break; }
  panel.setAttribute('data-savehub-starred', '1');
  return [...panel.querySelectorAll('[role="listitem"], [role="row"]')].map(r => {
    const titles = [...r.querySelectorAll('span[title]')].map(s => s.getAttribute('title')).filter(Boolean);
    const body = r.querySelector('.selectable-text, span[dir="ltr"], span[dir="auto"]');
    const pre = r.querySelector('[data-pre-plain-text]');
    return {who: titles.join(' > '), text: body ? (body.innerText || body.textContent || '').trim() : (r.innerText || '').trim().slice(0, 500),
            meta: pre ? pre.getAttribute('data-pre-plain-text') : '', time: ((r.querySelector('[data-testid="msg-meta"], span[dir="auto"]:last-child') || {}).textContent || '').trim()};
  });
}
"""


def wa_item(id_: str, name: str, **extra) -> dict | None:
    user, _, server = id_.partition("@")
    if server == "c.us" and user.isdigit():
        url, kind = f"https://wa.me/{user}", "contact"; extra.setdefault("phone", "+" + user)
    elif server == "g.us":
        url, kind = f"https://web.whatsapp.com/#group/{id_}", "group"
    elif server == "lid":
        url, kind = f"https://web.whatsapp.com/#lid/{id_}", "contact"
    else:
        return None          # status@broadcast, newsletters, ...
    name = name or extra.get("phone") or id_
    return raw(id_, url, author=name, title=name, type="contact", extra={"kind": kind, "wa_id": id_, **{k: v for k, v in extra.items() if v not in ("", None, False, 0)}})


@register
class WhatsAppCollector(ContactsCollector):
    platform, name = "whatsapp", "WhatsApp"
    home_url = "https://web.whatsapp.com/"
    login_url = "https://web.whatsapp.com/"
    saved_url = "https://web.whatsapp.com/ (contacts + chats + starred messages)"
    notes = ("CONTACTS collector: WhatsApp Web's IndexedDB address book + chat list (phone -> https://wa.me/<n>, groups), "
             "DOM chat list as fallback, plus starred messages (best-effort, extra.kind=starred_message). QR login; the "
             "session file includes IndexedDB (Playwright >= 1.51). No un-save/un-star.")
    SEL, LOGIN_URL_RE, EXTRACT_JS = WA_SEL, None, WA_DOM_JS
    needs_indexed_db = True
    _idb_names: set = set()

    def wait_ready(self, page, timeout_ms):
        super().wait_ready(page, timeout_ms)
        if self._count(page, "logged_in"): page.wait_for_timeout(2500)        # let the first sync settle

    def embedded_raws(self, page):
        """IndexedDB address book + chats (merged by WhatsApp id)."""
        self._idb_names = set()
        try: data = page.evaluate(WA_IDB_JS)
        except Exception as e:
            ev("whatsapp.idb_error", logging.WARNING, error=str(e)[:200]); data = None
        if not data: ev("whatsapp.idb_unavailable", note="falling back to the chat list DOM"); return []
        by: dict = {}
        subjects = {g["id"]: g["subject"] for g in data.get("groups") or [] if g.get("id")}
        for c in data.get("contacts") or []:
            if not c.get("id"): continue
            by[c["id"]] = {"name": c.get("name") or c.get("verified") or c.get("push") or "", "pushname": c.get("push"),
                           "in_address_book": c.get("mine"), "business": c.get("biz")}
        for ch in data.get("chats") or []:
            if not ch.get("id"): continue
            d = by.setdefault(ch["id"], {"name": ""})
            d["name"] = d.get("name") or ch.get("name") or subjects.get(ch["id"], "")
            d["last_chat_at"] = iso_from_epoch(ch.get("t")); d["unread"] = ch.get("unread"); d["archived"] = ch.get("archive")
        out = []
        for wid, d in by.items():
            name = d.pop("name", "") or subjects.get(wid, "")
            r = wa_item(wid, name, **d)
            if r: out.append(r); self._idb_names.add(r["title"])
        ev("whatsapp.idb", contacts=len(data.get("contacts") or []), chats=len(data.get("chats") or []), items=len(out))
        return out

    def parse_dom(self, r):
        name = (r.get("name") or "").strip()
        if not name or name in self._idb_names: return None
        digits = re.sub(r"[^\d]", "", name)
        if name.startswith("+") and 7 <= len(digits) <= 15:
            return wa_item(f"{digits}@c.us", name, last_message=(r.get("preview") or "")[:300])
        kind = "group" if r.get("group") else "chat"
        return raw("chat:" + short_hash(name), f"https://web.whatsapp.com/#chat/{quote(name)}", author=name, title=name,
                   media_url=r.get("media_url") or "", extra={"kind": kind, **({"last_message": r["preview"][:300]} if r.get("preview") else {})},
                   type="contact", avatar=r.get("media_url") or None)

    def collect(self, page, cfg: RunConfig, session_file=""):
        items = {it.external_id: it for it in super().collect(page, cfg, session_file)}
        if len(items) < cfg.max_items:
            try: self._starred(page, cfg, session_file, items)
            except Exception as e: ev("whatsapp.starred_skipped", logging.WARNING, error=str(e)[:200])
        return list(items.values())[: cfg.max_items]

    def _starred(self, page, cfg, session_file, items):
        menu = page.locator(self.SEL["menu"])
        if not menu.count(): ev("whatsapp.starred_skipped", reason="menu not found"); return
        menu.first.click(timeout=5000)
        entry = page.locator(self.SEL["starred_entry"])
        try: entry.first.wait_for(timeout=5000)
        except Exception: page.keyboard.press("Escape"); ev("whatsapp.starred_skipped", reason="no 'Starred messages' entry"); return
        entry.first.click(timeout=5000); page.wait_for_timeout(1500)

        def extract(pg):
            rows = pg.evaluate(WA_STARRED_JS, self.SEL) or []
            out = []
            for r in rows:
                if not r.get("text"): continue
                key = short_hash(f'{r.get("who")}|{r.get("meta")}|{r.get("time")}|{r["text"]}')
                out.append(raw("starred:" + key, f"https://web.whatsapp.com/#starred/{key}", author=r.get("who") or "",
                               text=r["text"], title=r["text"][:120], extra={"kind": "starred_message", "chat": r.get("who") or "",
                                                                              **({"meta": r["meta"]} if r.get("meta") else {}), **({"time": r["time"]} if r.get("time") else {})}))
            return out
        before = len(items)
        self.scroll_loop(page, cfg, session_file, "", items, extract=extract,
                         scroll=lambda pg: pg.evaluate(LIST_SCROLL_JS, self.SEL["starred_row"]))
        ev("whatsapp.starred", count=len(items) - before)
