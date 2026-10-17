"""iCloud (Apple) collector: iCloud Drive FILES (+ iCloud Contacts), read-only, no browser.

Built on pyicloud (pinned) and the Drive walking / text extraction code of js713-lab/icloud4u-mcp (pinned commit, imported
as the `icloud_docs_mcp` library; its MCP server is never started).

Login (on the box only, interactive terminal):  python -m savehub.collector login icloud --account @<appleid-handle>
  asks for the Apple ID, the Apple ID password (getpass, no echo) and the 2FA code, then asks Apple to trust the session.
  The password goes to Apple's SRP sign-in only: SaveHub never stores, logs or forwards it (no MCP/REST field accepts it).
  App-specific passwords do NOT work here: Apple accepts them only for IMAP/CalDAV/CardDAV apps, not for the iCloud.com
  web sign-in that iCloud Drive uses, so the real password + 2FA is required (once per trusted session, about 1-2 months).
Session: only the resulting trusted cookie jar + pyicloud session tokens are kept, inside the normal SaveHub session file
  data/sessions/icloud@<handle>.json (AES-256-GCM with the vault key, AAD = icloud@<handle>, 0600). pyicloud insists on a
  cookie directory, so each run decrypts them into a fresh 0700 temp dir (in /dev/shm = RAM when available, files 0600)
  that is deleted in `finally`, and writes the refreshed cookies back encrypted.
  The top-level "cookies" list holds metadata only (name/domain/expires, never values) so the generic session status
  (saved / expired) works like for the browser apps. When Apple rejects the session, it is marked expired (needs login).
Collect: walks iCloud Drive (capped folders/depth), newest-modified first; each file becomes an item (name, path, type,
  size, modified date, a reference link, extracted text for small txt/md/csv/json/pdf/docx/xlsx files). Incremental by
  modified date: external_id = "<docwsid>:<etag|modified>", so only new or changed files are downloaded and pushed; a
  changed file updates its existing item. Contacts (pyicloud ContactsService, read-only GET) become items with
  extra.kind = "contact". Nothing is ever deleted, moved, renamed or uploaded on iCloud (no such call exists here).
"""
from __future__ import annotations
import contextlib, getpass, hashlib, json, logging, os, re, shutil, sys, tempfile, time
from datetime import datetime, timezone
from http.cookiejar import LWPCookieJar
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote
from ..base import CollectedItem, Collector, CollectorError, NeedLogin, RunConfig, register
from ..logutil import ScrubFilter, ev, scrub
from .. import sessions

PLATFORM = "icloud"
FORMAT = 1
DRIVE_WEB = "https://www.icloud.com/iclouddrive/"
CONTACTS_WEB = "https://www.icloud.com/contacts/"
KEYRING_NULL = "keyring.backends.null.Keyring"
# pyicloud exception class names that mean "the session is not (or no longer) accepted: log in again"
AUTH_ERRORS = {"PyiCloudFailedLoginException", "PyiCloudAccountLockedException", "PyiCloud2FARequiredException",
               "PyiCloud2SARequiredException", "PyiCloudAuthRequiredException", "PyiCloudPasswordException",
               "PyiCloudNoStoredPasswordAvailableException", "PyiCloudAcceptTermsException", "TokenException"}
EXTRACTABLE = {".txt", ".md", ".csv", ".tsv", ".log", ".json", ".xml", ".pdf", ".docx", ".xlsx", ".xlsm"}


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try: v = int(os.environ.get(name, default))
    except ValueError: v = default
    return max(lo, min(hi, v))


def limits() -> dict:
    """Run limits (env, non-secret; passed to collector children via runtime.ENV_ALLOW)."""
    return {"max_folders": _env_int("SAVEHUB_ICLOUD_MAX_FOLDERS", 400, 1, 20000),
            "max_depth": _env_int("SAVEHUB_ICLOUD_MAX_DEPTH", 12, 1, 64),
            "extract_max_bytes": _env_int("SAVEHUB_ICLOUD_EXTRACT_MAX_MB", 10, 0, 200) * 1024 * 1024,
            "text_chars": _env_int("SAVEHUB_ICLOUD_TEXT_CHARS", 8000, 0, 100000),
            "contacts": os.environ.get("SAVEHUB_ICLOUD_CONTACTS", "1").strip().lower() not in ("0", "off", "false", "no"),
            "root": os.environ.get("SAVEHUB_ICLOUD_ROOT", "").strip()}


# ------------------------------------------------------------------ library plumbing (injectable for tests)
def _quiet_logs() -> None:
    """pyicloud / icloud4u log through the scrubber, and never below WARNING (no request dumps)."""
    f = ScrubFilter()
    for n in ("pyicloud", "icloud_docs_mcp"):
        lg = logging.getLogger(n)
        if lg.level == logging.NOTSET or lg.level < logging.WARNING: lg.setLevel(logging.WARNING)
        if not any(isinstance(x, ScrubFilter) for x in lg.filters): lg.addFilter(f)


def _service_factory() -> Callable:
    """pyicloud.PyiCloudService. The OS keyring is switched off first (pyicloud would otherwise read/write passwords there)."""
    os.environ["PYTHON_KEYRING_BACKEND"] = KEYRING_NULL
    _quiet_logs()
    try:
        from pyicloud import PyiCloudService
    except ImportError:
        raise CollectorError("iCloud needs pyicloud: pip install -r requirements-collector.txt") from None
    return PyiCloudService


def _icloud4u():
    """The reused icloud4u-mcp library pieces (Drive walking + text extraction)."""
    try:
        from icloud_docs_mcp import client as c, config as cfg, extract as ex, paths as p
    except ImportError:
        raise CollectorError("iCloud needs icloud-docs-mcp (js713-lab/icloud4u-mcp): pip install -r requirements-collector.txt") from None
    _quiet_logs()
    return c, cfg, ex, p


def is_auth_error(e: BaseException) -> bool:
    return any(k.__name__ in AUTH_ERRORS for k in type(e).__mro__)


def _short(e: BaseException, secrets=()) -> str:
    s = f"{type(e).__name__}: {e}"
    for v in secrets:
        if v: s = s.replace(v, "[REDACTED]")
    return scrub(s)[:300]


# ------------------------------------------------------------------ private working dir (pyicloud insists on files)
@contextlib.contextmanager
def private_workdir():
    """0700 temp dir, in RAM (/dev/shm) when available; files created under umask 077; removed in finally."""
    base = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK | os.X_OK) else None  # nosec B108 - mkdtemp() makes a unique 0700 dir; /dev/shm only keeps it in RAM
    d = tempfile.mkdtemp(prefix="savehub-icloud-", dir=base)
    old = os.umask(0o077)
    try:
        os.chmod(d, 0o700)
        yield Path(d)
    finally:
        os.umask(old)
        shutil.rmtree(d, ignore_errors=True)


def _stem(apple_id: str) -> str:
    """pyicloud's file name for an Apple ID: only \\w characters (session.cookiejar_path / session_path)."""
    return "".join(c for c in apple_id if re.match(r"\w", c))


def _write_private(p: Path, text: str) -> None:
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f: f.write(text)


def seed_workdir(d: Path, ic: dict) -> None:
    """Decrypted (in memory) session -> the two files pyicloud reads: <stem>.cookiejar (LWP) and <stem>.session (JSON)."""
    stem = _stem(ic["apple_id"])
    if ic.get("cookiejar"): _write_private(d / f"{stem}.cookiejar", ic["cookiejar"])
    if isinstance(ic.get("session"), dict): _write_private(d / f"{stem}.session", json.dumps(ic["session"]))


def _cookie_meta(jar_path: Path) -> list:
    """name/domain/path/expires of every cookie (NO values): drives the generic saved/expired session status."""
    out = []
    if not jar_path.exists(): return out
    jar = LWPCookieJar(str(jar_path))
    try: jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, ValueError): return out
    for c in jar:
        out.append({"name": c.name, "domain": c.domain, "path": c.path, "expires": float(c.expires) if c.expires else -1})
    return out


def snapshot(d: Path, apple_id: str, china: bool, prev: Optional[dict] = None, rejected: bool = False) -> dict:
    """Read what pyicloud wrote back into the SaveHub session document (encrypted by sessions.write_state)."""
    stem = _stem(apple_id); prev = prev or {}
    jarp, sesp = d / f"{stem}.cookiejar", d / f"{stem}.session"
    jar = jarp.read_text(encoding="utf-8") if jarp.exists() else (prev.get("cookiejar") or "")
    try: sess = json.loads(sesp.read_text(encoding="utf-8")) if sesp.exists() else (prev.get("session") or {})
    except ValueError: sess = prev.get("session") or {}
    meta = _cookie_meta(jarp) if jarp.exists() else list(prev.get("_meta") or [])
    now = time.time(); stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if rejected:                              # Apple refused it: keep the jar (its trust token can skip 2FA on re-login)
        meta = [{**m, "expires": now - 1} for m in meta] or [{"name": "rejected", "domain": "icloud.com", "path": "/", "expires": now - 1}]
    ic = {"format": FORMAT, "apple_id": apple_id, "china_mainland": bool(china), "cookiejar": jar, "session": sess,
          "logged_in_at": prev.get("logged_in_at") or stamp, "refreshed_at": stamp, "rejected_at": stamp if rejected else None}
    return {"cookies": meta, "origins": [], "savehub_session": "pyicloud", PLATFORM: ic}


def session_info(state: dict) -> dict:
    ic = (state or {}).get(PLATFORM)
    return ic if isinstance(ic, dict) and ic.get("apple_id") else {}


# ------------------------------------------------------------------ connect with a saved (trusted) session
def connect(state: dict, d: Path, sfile, acct: str, factory: Optional[Callable] = None):
    """PyiCloudService on the saved session, never with a password (so it can only reuse the trusted session).
    Raises NeedLogin when there is no usable session or Apple asks for a password / 2FA again."""
    ic = session_info(state)
    if not ic: raise NeedLogin(PLATFORM, sfile, "the session file has no iCloud session (log in again)", acct)
    seed_workdir(d, ic)
    factory = factory or _service_factory()
    try:
        api = factory(ic["apple_id"], None, cookie_directory=str(d), accept_terms=False, authenticate=False,
                      china_mainland=bool(ic.get("china_mainland")))
        api.authenticate()
    except Exception as e:
        if is_auth_error(e): raise _Rejected(_short(e)) from None
        raise CollectorError(f"iCloud sign-in check failed: {_short(e)}") from None
    if getattr(api, "requires_2fa", False) or getattr(api, "requires_2sa", False) or not getattr(api, "is_trusted_session", False):
        raise _Rejected("Apple asks for the password / a 2FA code again (trusted session expired)")
    return api


class _Rejected(Exception):
    """Apple no longer accepts the saved session."""


# ------------------------------------------------------------------ Drive walk + items
def _iso(dt) -> str:
    if not dt: return ""
    if isinstance(dt, str): return dt
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _epoch(dt) -> int:
    if not dt or isinstance(dt, str): return 0
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def human_size(n) -> str:
    if n is None: return ""
    n = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB": return f"{int(n)} B" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return ""


def walk_drive(api, lim: dict) -> tuple:
    """Breadth-first walk with icloud4u-mcp's DriveClient (root scoping, robust child listing).
    -> ([(relpath, node)], stats). Folder/depth caps keep one run bounded."""
    c, cfgmod, _, paths = _icloud4u()
    settings = cfgmod.Settings(username=None, password=None, session_dir=Path(os.devnull), download_dir=Path(os.devnull),
                               root=paths.normalize_relpath(lim["root"]))
    client = c.DriveClient(settings, api=api)
    client.connect()                                          # injected api: only resolves the (optional) root folder
    queue, files, folders, truncated = [(client._require_root(), "", 0)], [], 0, False
    while queue:
        node, rel, depth = queue.pop(0)
        if folders >= lim["max_folders"]: truncated = True; break
        folders += 1
        for child in client._children(node):
            name = c._node_name(child); t = c._node_type(child)
            crel = paths.join_relpath(rel, name) if name not in ("", ".", "..") else rel
            if t == "file": files.append((crel, child))
            elif t in paths.FOLDER_TYPES:
                if depth + 1 > lim["max_depth"]: truncated = True; continue
                queue.append((child, crel, depth + 1))
    return files, {"folders": folders, "files": len(files), "truncated": truncated or bool(queue)}


def _node_data(node) -> dict:
    d = getattr(node, "data", None)
    return d if isinstance(d, dict) else {}


def file_version(node) -> str:
    d = _node_data(node)
    return str(d.get("etag") or _epoch(getattr(node, "date_modified", None) or getattr(node, "date_changed", None)) or "0")


def file_ref(node, rel: str) -> tuple:
    d = _node_data(node)
    doc = str(d.get("docwsid") or d.get("drivewsid") or "")
    url = DRIVE_WEB + ("#doc=" + quote(doc, safe="") if doc else "#path=" + quote(rel, safe="/"))
    return doc or "path:" + rel, url


def download_capped(node, dest: Path, max_bytes: int) -> int:
    """Stream one file into dest; aborts (and deletes) above max_bytes. Read-only GET."""
    n = 0
    resp = node.open(stream=True)
    cm = resp if hasattr(resp, "__enter__") else contextlib.nullcontext(resp)
    with cm as r, open(dest, "wb") as fh:
        it = r.iter_content(65536) if hasattr(r, "iter_content") else None
        if it is None:
            raw = getattr(r, "raw", None)
            it = iter(lambda: raw.read(65536), b"") if raw is not None else iter([getattr(r, "content", b"") or b""])
        for chunk in it:
            if not chunk: continue
            n += len(chunk)
            if n > max_bytes: raise ValueError(f"larger than {max_bytes} bytes")
            fh.write(chunk)
    return n


def extract_text(node, name: str, size, d: Path, lim: dict) -> dict:
    """Small documents only: download to the private dir, extract with icloud4u-mcp, delete right away."""
    ext = os.path.splitext(name)[1].lower()
    if lim["text_chars"] <= 0 or ext not in EXTRACTABLE: return {}
    if size is None or size > lim["extract_max_bytes"]: return {"text_note": f"not extracted (over {human_size(lim['extract_max_bytes'])})"}
    _, _, ex, _ = _icloud4u()
    dl = d / "dl"; os.makedirs(dl, mode=0o700, exist_ok=True)
    tmp = dl / (hashlib.sha256(name.encode()).hexdigest()[:16] + ext)
    try:
        download_capped(node, tmp, lim["extract_max_bytes"])
        r = ex.extract_file(tmp, limit=lim["text_chars"])
        out = {"text": (r.text or "").strip(), "text_truncated": bool(r.truncated), "content_type": r.content_type}
        if r.note: out["text_note"] = scrub(r.note)[:200]
        return out
    except Exception as e:
        if is_auth_error(e): raise                             # session rejected mid-run -> needs login, not a file note
        return {"text_note": "not extracted: " + _short(e)[:150]}
    finally:
        try: tmp.unlink()
        except OSError: pass


def file_item(rel: str, node, extra_text: dict) -> CollectedItem:
    name = rel.rsplit("/", 1)[-1]; folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
    size = getattr(node, "size", None)
    mod = getattr(node, "date_modified", None) or getattr(node, "date_changed", None)
    ext = os.path.splitext(name)[1].lower()
    ref, url = file_ref(node, rel)
    kind = (extra_text.get("content_type") or ext.lstrip(".") or "file").upper()
    head = f"iCloud Drive › {rel}\n{kind}" + (f" · {human_size(size)}" if size is not None else "") + \
           (f" · modified {_iso(mod)[:16].replace('T', ' ')} UTC" if mod else "")
    text = head + ("\n\n" + extra_text["text"] if extra_text.get("text") else "")
    d = _node_data(node)
    extra = {"kind": "file", "service": "iclouddrive", "path": rel, "folder": folder, "ext": ext, "size": size, "modified": _iso(mod),
             "version": file_version(node), "docwsid": d.get("docwsid"), "drivewsid": d.get("drivewsid"), "zone": d.get("zone"),
             **{k: v for k, v in extra_text.items() if k in ("content_type", "text_truncated", "text_note")},
             "text_chars": len(extra_text.get("text") or "")}
    return CollectedItem(platform=PLATFORM, url=url, external_id=f"{ref}:{file_version(node)}", author="iCloud Drive" + (f"/{folder}" if folder else ""),
                         text=text, posted_at=_iso(mod), title=name, extra={k: v for k, v in extra.items() if v not in (None, "")})


def contact_item(c: dict) -> Optional[CollectedItem]:
    if not isinstance(c, dict): return None
    cid = str(c.get("contactId") or c.get("id") or "")
    phones = [str(p.get("field")).strip() for p in c.get("phones") or [] if isinstance(p, dict) and p.get("field")]
    emails = [str(e.get("field")).strip() for e in c.get("emailAddresses") or [] if isinstance(e, dict) and e.get("field")]
    name = " ".join(x for x in (c.get("prefix"), c.get("firstName"), c.get("middleName"), c.get("lastName"), c.get("suffix")) if x).strip()
    org = str(c.get("companyName") or "").strip(); job = str(c.get("jobTitle") or "").strip()
    name = name or str(c.get("nickName") or "") or org or (emails[0] if emails else "") or (phones[0] if phones else "")
    if not name: return None
    if not cid: cid = "h" + hashlib.sha256(json.dumps([name, phones, emails]).encode()).hexdigest()[:20]
    version = str(c.get("etag") or hashlib.sha256(json.dumps([name, org, job, phones, emails], ensure_ascii=False).encode()).hexdigest()[:16])
    lines = [name] + ([" · ".join(x for x in (job, org) if x)] if (job or org) else []) + \
            ([f"Phone: {', '.join(phones)}"] if phones else []) + ([f"Email: {', '.join(emails)}"] if emails else [])
    extra = {"kind": "contact", "service": "contacts", "contact_id": cid, "version": version, "org": org, "job_title": job,
             "phone": phones[0] if phones else "", "phones": phones, "emails": emails}
    return CollectedItem(platform=PLATFORM, url=CONTACTS_WEB + "#" + quote(cid, safe=""), external_id=f"contact:{cid}:{version}",
                         author=org, text="\n".join(lines), title=name, extra={k: v for k, v in extra.items() if v not in (None, "", [])})


# ------------------------------------------------------------------ the collector
@register
class ICloudCollector(Collector):
    platform, name = PLATFORM, "iCloud"
    kind = "files"                      # iCloud Drive files (+ contacts as extra.kind=contact items)
    kinds = ("files", "contacts")
    uses_browser = False                # pyicloud API client, no Playwright
    login_mode = "terminal"             # login asks for Apple ID + password + 2FA in a terminal on the box
    supports_unsave = False             # read-only: nothing on iCloud is ever deleted/moved/renamed
    backend = "pyicloud"
    home_url = login_url = "https://www.icloud.com/"
    saved_url = "https://www.icloud.com/iclouddrive/ (all files) + https://www.icloud.com/contacts/"
    notes = ("FILES collector (read-only): iCloud Drive via pyicloud + icloud4u-mcp (name, path, type, size, modified, extracted text "
             "of small docs/PDFs) and iCloud Contacts. Login is a terminal prompt on the box (Apple ID + password + 2FA; the password "
             "is never stored); only the trusted session is kept, encrypted. Incremental by modified date. Never deletes anything.")

    def describe(self) -> dict:
        return {**super().describe(), "kinds": list(self.kinds), "uses_browser": False, "login": self.login_mode, "backend": self.backend,
                "read_only": True}

    # incremental: a file/contact is known only when its exact version (external_id) is stored. The URL stays the same when a
    # file changes, so the URL alone must not count as known (the changed file is pushed and updates its item).
    @staticmethod
    def is_known(cfg: RunConfig, it: CollectedItem) -> bool:
        return bool(cfg.known and it.external_id and f"id:{it.external_id}" in cfg.known)

    def collect_api(self, state: dict, cfg: RunConfig, sfile, acct: str = "", factory: Optional[Callable] = None) -> tuple:
        """-> (items, summary_extra). Writes the refreshed (or rejected) session back to sfile, encrypted."""
        lim = limits(); ic = session_info(state)
        if not ic: raise NeedLogin(PLATFORM, sfile, "the session file has no iCloud session (log in again)", acct)
        info: dict = {"backend": "pyicloud"}
        with private_workdir() as d:
            try:
                api = connect(state, d, sfile, acct, factory)
            except _Rejected as e:
                sessions.write_state(sfile, snapshot(d, ic["apple_id"], ic.get("china_mainland"), {**ic, "_meta": state.get("cookies")}, rejected=True))
                ev("icloud.session_rejected", logging.WARNING, account=acct or "default")
                raise NeedLogin(PLATFORM, sfile, f"Apple rejected the saved session ({e}); log in again", acct) from None
            try:
                items = self._drive_items(api, cfg, d, lim, info)
                if lim["contacts"]: items += self._contact_items(api, cfg, info)
            except Exception as e:
                if is_auth_error(e):
                    sessions.write_state(sfile, snapshot(d, ic["apple_id"], ic.get("china_mainland"), {**ic, "_meta": state.get("cookies")}, rejected=True))
                    raise NeedLogin(PLATFORM, sfile, f"Apple rejected the session during the run ({_short(e)}); log in again", acct) from None
                raise
            finally:
                try: api._password_raw = None                        # never set anyway (no password on this path)
                except Exception: pass
            sessions.write_state(sfile, snapshot(d, ic["apple_id"], ic.get("china_mainland"), {**ic, "_meta": state.get("cookies")}))
        return items, info

    def _drive_items(self, api, cfg, d, lim, info) -> list:
        try:
            files, st = walk_drive(api, lim)
        except CollectorError: raise
        except Exception as e:
            if is_auth_error(e): raise
            raise CollectorError(f"iCloud Drive unavailable: {_short(e)} (Advanced Data Protection accounts must allow "
                                 f"'Access iCloud Data on the Web' in iCloud settings)") from None
        def mod_key(f):
            n = f[1]; m = getattr(n, "date_modified", None) or getattr(n, "date_changed", None)
            return _epoch(m)
        files.sort(key=mod_key, reverse=True)                                     # newest change first
        new = []
        for rel, node in files:
            ref, _ = file_ref(node, rel)
            if cfg.known and f"id:{ref}:{file_version(node)}" in cfg.known: continue   # unchanged since the last sync
            new.append((rel, node))
            if len(new) >= cfg.max_items: break
        out = []
        for rel, node in new:
            txt = extract_text(node, rel.rsplit("/", 1)[-1], getattr(node, "size", None), d, lim)
            out.append(file_item(rel, node, txt))
        info.update(drive_folders=st["folders"], drive_files=st["files"], drive_truncated=st["truncated"],
                    drive_unchanged=len(files) - len(new) if cfg.known else 0, files=len(out),
                    extracted=sum(1 for it in out if it.extra.get("text_chars")))
        ev("icloud.drive", folders=st["folders"], files=st["files"], new_or_changed=len(out), truncated=st["truncated"])
        return out

    def _contact_items(self, api, cfg, info) -> list:
        try:
            rows = api.contacts.all or []
        except Exception as e:
            if is_auth_error(e): raise
            info["contacts_error"] = _short(e)[:200]; ev("icloud.contacts_skipped", logging.WARNING, error=_short(e)[:200])
            return []
        out = []
        for c in rows:
            it = contact_item(c)
            if it is None or self.is_known(cfg, it): continue
            out.append(it)
            if len(out) >= cfg.max_items: break
        info.update(contacts_total=len(rows), contacts=len(out))
        ev("icloud.contacts", total=len(rows), new_or_changed=len(out))
        return out

    # ---------------------------------------------------------------- login (terminal, on the box)
    def terminal_login(self, sfile, akey: str, *, stdin=None, stdout=None, ask_password: Optional[Callable] = None,
                       factory: Optional[Callable] = None, require_tty: bool = True) -> dict:
        stdin, stdout = stdin or sys.stdin, stdout or sys.stderr       # prompts on stderr: stdout is the JSON result
        ask_password = ask_password or getpass.getpass                  # no echo; read from the controlling terminal
        if require_tty and not (hasattr(stdin, "isatty") and stdin.isatty()):
            raise CollectorError("iCloud login is interactive: run `python -m savehub.collector login icloud --account @<handle>` "
                                 "in a terminal on the box (it asks for your Apple ID, password and 2FA code)")
        say = lambda s="": (stdout.write(s + "\n"), stdout.flush())
        def ask(prompt):
            stdout.write(prompt); stdout.flush()
            line = stdin.readline()
            if not line: raise CollectorError("login cancelled (no input)")
            return line.strip()
        prev = {}
        if Path(sfile).exists():
            try: prev = session_info(sessions.read_state(sfile, migrate=False))
            except Exception: prev = {}
        who = f"@{akey}" if akey != sessions.DEFAULT_ACCOUNT else "the default iCloud session"
        say(f"iCloud sign-in for SaveHub ({who}).")
        say("Your password goes only to Apple's sign-in (SRP); SaveHub never stores, logs or forwards it. Only the resulting")
        say("trusted session is kept, encrypted. Use your normal Apple ID password: Apple does not accept app-specific passwords")
        say("for iCloud.com / iCloud Drive (they only work for mail, calendar and contacts apps).")
        last = prev.get("apple_id") or ""
        apple_id = ask(f"Apple ID [{last}]: " if last else "Apple ID: ") or last
        if not apple_id or len(apple_id) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+|\+?[0-9][0-9 ()-]{5,}", apple_id):
            raise CollectorError("an Apple ID (e-mail address or phone number) is required")
        china = os.environ.get("ICLOUD_CHINA", "0") == "1" or bool(prev.get("china_mainland") and prev.get("apple_id") == apple_id)
        password = ask_password(f"Apple ID password for {apple_id} (not shown, not stored): ")
        if not password: raise CollectorError("a password is required")
        factory = factory or _service_factory()
        with private_workdir() as d:
            if prev.get("apple_id") == apple_id: seed_workdir(d, prev)    # old trust token may let Apple skip 2FA
            try:
                try:
                    api = factory(apple_id, password, cookie_directory=str(d), accept_terms=False, china_mainland=china)
                except Exception as e:
                    if type(e).__name__ == "PyiCloudAcceptTermsException":
                        raise CollectorError("Apple wants you to accept new iCloud terms: sign in once at https://www.icloud.com, then retry") from None
                    if is_auth_error(e):
                        raise CollectorError(f"Apple refused the sign-in (wrong Apple ID/password, or the account is locked): {_short(e, [password])}") from None
                    raise CollectorError(f"iCloud sign-in failed: {_short(e, [password])}") from None
                self._second_factor(api, ask, say, password)
                try: api._password_raw = None                          # drop the password from the library object
                except Exception: pass
                if not getattr(api, "is_trusted_session", False):
                    raise CollectorError("Apple did not mark this session as trusted; nothing was saved. Try again.")
                reach = ""
                try:
                    c, *_ = _icloud4u()
                    n = len(c.DriveClient(None, api=api)._children(api.drive)) if hasattr(api, "drive") else 0
                    reach = f"iCloud Drive reachable ({n} top-level entries)."
                except Exception as e:
                    reach = f"Signed in, but iCloud Drive did not answer yet ({_short(e, [password])[:120]})."
                state = snapshot(d, apple_id, china, {})
            finally:
                password = None                                            # noqa: F841 - drop our reference too
            sessions.write_state(sfile, state)
        say(f"Signed in. {reach} Session saved encrypted as {Path(sfile).name}" if sessions.encryption_enabled()
            else f"Signed in. {reach} Session saved as {Path(sfile).name} (0600; set a vault key to encrypt it)")
        return {"apple_id_saved": True, "drive_check": reach}

    @staticmethod
    def _second_factor(api, ask, say, password) -> None:
        if getattr(api, "requires_2fa", False):
            if getattr(api, "security_key_names", None):
                raise CollectorError("this Apple ID asks for a hardware security key, which SaveHub's terminal login does not support")
            say("Two-factor authentication: Apple is sending a 6-digit code to your trusted devices (or by SMS).")
            try: api.request_2fa_code()
            except Exception as e: say(f"(could not trigger the code automatically: {_short(e, [password])[:120]}; use a code from Settings › Apple ID › Sign-In & Security)")
            notice = getattr(api, "two_factor_delivery_notice", None)
            if notice: say(str(notice))
            for attempt in range(3):
                code = ask("2FA code: ").replace(" ", "")
                if not re.fullmatch(r"\d{6}", code):
                    say("Enter the 6 digits."); continue
                try: ok = bool(api.validate_2fa_code(code))
                except Exception as e: ok = False; say(f"Rejected ({_short(e, [password, code])[:120]}).")
                if ok: break
                if attempt < 2: say(f"Code rejected, {2 - attempt} attempt(s) left.")
            else:
                raise CollectorError("2FA failed; wait a few minutes and run the login again")
            if not getattr(api, "is_trusted_session", False):
                try: api.trust_session()
                except Exception as e: raise CollectorError(f"Apple did not trust the session: {_short(e, [password])}") from None
        elif getattr(api, "requires_2sa", False):
            raise CollectorError("this Apple ID uses legacy two-step verification; switch it to two-factor authentication first")
