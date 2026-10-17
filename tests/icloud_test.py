"""iCloud collector (pyicloud + icloud4u-mcp): offline tests with a mocked pyicloud service.

No Apple traffic: savehub.collector.platforms.icloud._service_factory is replaced by FakeService, which mimics pyicloud's
file-based cookie directory, SRP-password login, 2FA + trust, trusted-session reuse, Drive nodes and Contacts.
The text extraction itself is the real icloud4u-mcp code (pypdf / python-docx) on generated files.

Covers: file indexing (name/path/type/size/modified/link), text extraction (txt, pdf, docx; EN + 中文 search), size cap,
contacts, incremental sync by modified date (only new/changed files downloaded; changed files update their item),
expired/rejected session -> needs_login (collect exit code, login_status, sync_status), password never in MCP/REST schemas,
logs or files, encrypted session round-trip (AAD bound, wrong key = locked), temp dirs removed, read-only (no write calls).

Run:  .venv/bin/python -m pytest -q tests/icloud_test.py
"""
import asyncio, glob, io, json, logging, os, pathlib, re, stat, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from http.cookiejar import Cookie, LWPCookieJar
from types import SimpleNamespace as NS
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cryptography.fernet import Fernet
from savehub import db, vault
from savehub.collector import RunConfig, runner, sessions, autosync, runtime, backends, cli
from savehub.collector.base import REGISTRY, CollectorError, NeedLogin, EXIT_NEED_LOGIN, get_collector
from savehub.collector.client import SqliteSink
from savehub.collector.platforms import icloud

PASSWORD = "S3cr3t-Apple-Pass!42"
APPLE_ID = "js.tester@icloud.com"
TOKEN = "FAKEwebauthTOKENvalue-0123456789abcdef"
CODE = "123456"


# ------------------------------------------------------------------ fake pyicloud
class PyiCloudFailedLoginException(Exception): pass
class PyiCloudAuthRequiredException(Exception): pass


def _dt(s): return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def make_pdf(text: str) -> bytes:
    content = f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return out


def make_docx(text: str) -> bytes:
    from docx import Document
    d = Document(); d.add_paragraph(text); b = io.BytesIO(); d.save(b); return b.getvalue()


class FakeResp:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def iter_content(self, n):
        for i in range(0, len(self.data), n): yield self.data[i:i + n]


def _forbidden(self, *a, **k):
    WORLD["forbidden"].append(getattr(self, "name", "?")); raise AssertionError("write call on iCloud")


class Node:
    def __init__(self, name, kind="file", children=None, content=b"", modified="2026-10-01T10:00:00Z", etag="e1", doc=None):
        self.name, self.type, self._kids, self.content = name, kind, list(children or []), content
        self.size = len(content) if kind == "file" else None
        self.date_modified = _dt(modified) if kind == "file" else None
        self.data = {"docwsid": doc or f"DOC-{name}", "drivewsid": f"FILE::com.apple.CloudDocs::{name}", "etag": etag,
                     "zone": "com.apple.CloudDocs", "items": []}
    def get_children(self): return list(self._kids)
    def dir(self): return [k.name for k in self._kids]
    def __getitem__(self, k):
        for c in self._kids:
            if c.name == k: return c
        raise KeyError(k)
    def open(self, **kw):
        if WORLD["revoke_midrun"]: raise PyiCloudAuthRequiredException("Authentication required for Account.")
        WORLD["opens"].append(self.name); return FakeResp(self.content)
    delete = rename = move_to_trash = upload = mkdir = delete_forever = recover = _forbidden


def tree():
    big = b"x" * (2 * 1024 * 1024)
    return Node("root", "folder", [
        Node("Documents", "folder", [
            Node("会议纪要.txt", content="会议纪要：预算审批 已通过".encode(), modified="2026-10-05T09:00:00Z"),
            Node("Invoice-2026.pdf", content=make_pdf("Quarterly invoice for the ocean kayak trip"), modified="2026-10-03T08:00:00Z"),
            Node("Work", "folder", [Node("季度报告.docx", content=make_docx("季度报告 第三季度 收入增长"), modified="2026-09-30T12:00:00Z")]),
        ]),
        Node("Photos backup.zip", content=big, modified="2026-10-04T07:00:00Z"),
        Node("Pages", "app_library", [Node("Notes.md", content=b"# Shopping list\nkopi beans", modified="2026-08-01T00:00:00Z")]),
    ])


class FakeService:
    instances = []

    def __init__(self, apple_id, password=None, cookie_directory=None, accept_terms=False, authenticate=True, china_mainland=None, **kw):
        assert accept_terms is False, "SaveHub must not auto-accept Apple's terms"
        d = pathlib.Path(cookie_directory)
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
        stem = re.sub(r"\W", "", apple_id)
        self.apple_id, self._password_raw, self.jar, self.sess = apple_id, password, d / f"{stem}.cookiejar", d / f"{stem}.session"
        for f in (self.jar, self.sess):
            if f.exists(): assert stat.S_IMODE(f.stat().st_mode) == 0o600
        self.data = json.loads(self.sess.read_text()) if self.sess.exists() else {}
        self.requires_2fa = self.requires_2sa = self.is_trusted_session = False
        self.security_key_names, self.two_factor_delivery_notice = None, "Code sent to your trusted devices."
        FakeService.instances.append(self)
        if authenticate: self.authenticate()

    def authenticate(self):
        WORLD["auth_calls"] += 1
        if self.data.get("session_token") == WORLD["token"] and not WORLD["revoked"]:
            self.is_trusted_session = not WORLD["ask_2fa_again"]; self.requires_2fa = WORLD["ask_2fa_again"]
            self._save(); return
        if self._password_raw is None: raise PyiCloudFailedLoginException("No password set")
        if self._password_raw != PASSWORD:      # like some library errors, echoes input: SaveHub must redact it
            raise PyiCloudFailedLoginException(f"Invalid email/password combination ({self._password_raw}).")
        if self.data.get("trust_token") == WORLD["trust"] and not WORLD["revoked"]: self._trust()
        else: self.requires_2fa = True
        self._save()

    def request_2fa_code(self): WORLD["2fa_sent"] += 1; return True
    def validate_2fa_code(self, code):
        ok = code == CODE
        if ok: self.requires_2fa = False
        return ok
    def trust_session(self): self._trust(); self._save(); return True
    def _trust(self):
        WORLD["token"] = WORLD["token"] or "session-" + os.urandom(4).hex()
        self.data.update(session_token=WORLD["token"], trust_token=WORLD["trust"], client_id="cid-1")
        self.is_trusted_session = True; self.requires_2fa = False

    def _save(self):
        self.sess.write_text(json.dumps(self.data))
        jar = LWPCookieJar(str(self.jar))
        exp = int(time.time()) + WORLD["cookie_ttl"]
        jar.set_cookie(Cookie(0, "X-APPLE-WEBAUTH-TOKEN", TOKEN, None, False, ".icloud.com", True, True, "/", True, True, exp,
                              False, None, None, {"HttpOnly": None}))
        jar.save(ignore_discard=True, ignore_expires=True)

    @property
    def drive(self):
        if WORLD["revoke_midrun"] == "drive": raise PyiCloudAuthRequiredException("Authentication required")
        return NS(get_children=lambda: WORLD["tree"].get_children(), __getitem__=None, delete=_forbidden) if False else _Drive()

    @property
    def contacts(self): return NS(all=WORLD["contacts"])


class _Drive:
    def get_children(self): return WORLD["tree"].get_children()
    def __getitem__(self, k): return WORLD["tree"][k]
    delete_items = move_items_to_trash = rename_items = send_file = create_folders = _forbidden


CONTACTS = [{"contactId": "C1", "etag": "c1v1", "firstName": "Aisyah", "lastName": "Rahman", "companyName": "Kopi Sdn Bhd",
             "phones": [{"label": "MOBILE", "field": "+60 12-111 1111"}], "emailAddresses": [{"label": "HOME", "field": "aisyah@example.com"}]},
            {"contactId": "C2", "etag": "c2v1", "firstName": "伟", "lastName": "王", "phones": [{"field": "+86 138 0000 0000"}]},
            {"contactId": "C3", "etag": "c3v1", "companyName": "Apple Store KLCC"}]

WORLD: dict = {}


@pytest.fixture(autouse=True)
def world(monkeypatch):
    WORLD.clear()
    WORLD.update(token="", trust="trust-" + os.urandom(4).hex(), revoked=False, ask_2fa_again=False, revoke_midrun=False,
                 tree=tree(), contacts=[dict(c) for c in CONTACTS], opens=[], forbidden=[], auth_calls=0, **{"2fa_sent": 0},
                 cookie_ttl=86400 * 60)
    FakeService.instances.clear()
    monkeypatch.setattr(icloud, "_service_factory", lambda: FakeService)
    for k in list(os.environ):
        if k.startswith("SAVEHUB_ICLOUD_"): monkeypatch.delenv(k)
    yield
    from savehub.collector.logutil import log
    log.handlers[:] = []                                   # cli.main() bound a handler to this test's captured stderr
    assert WORLD["forbidden"] == [], "a write/delete call reached iCloud"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    c = getattr(db._local, "c", None)
    if c is not None: c.close(); db._local.c = None
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    sd = tmp_path / "sessions"
    monkeypatch.setenv("SAVEHUB_SESSIONS_DIR", str(sd))
    monkeypatch.setenv("SAVEHUB_VAULT_KEY", Fernet.generate_key().decode())      # throwaway key: sessions encrypted
    monkeypatch.setenv("SAVEHUB_VAULT_KEY_FILE", str(tmp_path / "no.key"))
    for k in ("RENDER", "RENDER_EXTERNAL_HOSTNAME", "SAVEHUB_COLLECTOR", "SAVEHUB_DASHBOARD_PASSWORD", "SAVEHUB_ENABLED_PLATFORMS"):
        monkeypatch.delenv(k, raising=False)
    vault.set_runtime_key("")
    autosync._last_trigger.clear()
    yield NS(sd=sd, db=tmp_path / "t.db", tmp=tmp_path)
    c = getattr(db._local, "c", None)
    if c is not None: c.close(); db._local.c = None


def _leftovers():
    pats = [os.path.join(tempfile.gettempdir(), "savehub-icloud-*"), "/dev/shm/savehub-icloud-*"]
    return {p for pat in pats for p in glob.glob(pat)}


def login(sd, account="me", password=PASSWORD, code=CODE, apple_id=APPLE_ID, out=None):
    col = get_collector("icloud")
    sfile = sessions.session_path("icloud", sd, account)
    stdin = io.StringIO(f"{apple_id}\n" + f"{code}\n" * 3)
    return col.terminal_login(sfile, account, stdin=stdin, stdout=out or io.StringIO(), ask_password=lambda prompt: password,
                              require_tty=False), sfile


def collect(e, account="me", **kw):
    cfg = RunConfig(account_handle=account, **kw)
    sink = SqliteSink(str(e.db))
    try: return runner.run_collect("icloud", cfg, sink, e.sd)
    finally: sink.close()


def stored(platform="icloud"):
    return {r["title"]: dict(r) for r in db.conn().execute("SELECT * FROM items WHERE platform=?", (platform,))}


# ------------------------------------------------------------------ registry / wiring
def test_registered_everywhere():
    cls = REGISTRY["icloud"]
    assert cls.implemented and cls.kind == "files" and "contacts" in cls.kinds and not cls.supports_unsave
    assert cls.uses_browser is False and cls.login_mode == "terminal"
    assert db.PLATFORMS["icloud"]["name"] == "iCloud" and db.PLATFORMS["icloud"]["method"] == "collector"
    d = backends.describe("icloud", read_db=False)
    assert d["backend"] == "pyicloud" and d["options"] == ["pyicloud"] and d["native"] and d["unsave_backend"] is None
    assert {l["name"] for l in d["libraries"]} == {"pyicloud", "icloud-docs-mcp"}
    assert runtime.runtime_for("icloud")["available"] is True              # no browser needed
    assert (ROOT / "static" / "logos" / "icloud.svg").read_text().startswith('<svg fill="#fff" role="img" viewBox="0 0 24 24"')
    js = (ROOT / "static" / "app.js").read_text()
    assert "icloud:" in js and "'icloud', 'web'" in js
    assert "pyicloud==" in (ROOT / "requirements-collector.txt").read_text()
    for k in ("SAVEHUB_ICLOUD_ROOT", "SAVEHUB_ICLOUD_TEXT_CHARS"): assert k in runtime.ENV_ALLOW


# ------------------------------------------------------------------ login
def test_terminal_login_keeps_only_encrypted_trusted_session(env, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    before = _leftovers(); out = io.StringIO()
    info, sfile = login(env.sd, out=out)
    assert WORLD["2fa_sent"] == 1 and info["apple_id_saved"]
    assert stat.S_IMODE(sfile.stat().st_mode) == 0o600 and stat.S_IMODE(sfile.parent.stat().st_mode) == 0o700
    raw = sfile.read_text()
    doc = json.loads(raw)
    assert sessions.is_encrypted(doc)
    for secret in (PASSWORD, TOKEN, APPLE_ID, WORLD["trust"]): assert secret not in raw
    st = sessions.read_state(sfile)
    plain = json.dumps(st)
    assert PASSWORD not in plain and TOKEN in st["icloud"]["cookiejar"] and st["icloud"]["apple_id"] == APPLE_ID
    assert all("value" not in c for c in st["cookies"])                  # status metadata only, never cookie values
    assert sessions.describe(sfile)["status"] == "saved"
    assert "app-specific" in out.getvalue() and PASSWORD not in out.getvalue()
    cap = capsys.readouterr()
    assert PASSWORD not in cap.out + cap.err and PASSWORD not in caplog.text
    assert FakeService.instances[-1]._password_raw is None               # dropped from the library object
    assert _leftovers() == before                                        # temp cookie dir removed


def test_login_wrong_password_redacted_and_nothing_saved(env, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(CollectorError) as ei:
        login(env.sd, password="Wrong-Pass-987654")
    assert "Wrong-Pass-987654" not in str(ei.value) and "[REDACTED]" in str(ei.value)
    assert "Wrong-Pass-987654" not in caplog.text
    assert not sessions.session_path("icloud", env.sd, "me").exists()
    with pytest.raises(CollectorError, match="2FA failed"):
        login(env.sd, code="000000")
    assert not sessions.session_path("icloud", env.sd, "me").exists()


def test_login_requires_a_terminal(env, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))                  # not a TTY (e.g. MCP / REST / cron)
    with pytest.raises(CollectorError, match="interactive"):
        runner.run_login("icloud", env.sd, account="me")


def test_run_login_cli_path(env, monkeypatch, capsys):
    class TTY(io.StringIO):
        def isatty(self): return True
    monkeypatch.setattr(sys, "stdin", TTY(f"{APPLE_ID}\n{CODE}\n"))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": PASSWORD)
    rc = cli.main(["--sessions-dir", str(env.sd), "login", "icloud", "--account", "@Me"])
    cap = capsys.readouterr(); res = json.loads(cap.out)
    assert rc == 0 and res["status"] == "logged_in" and res["account"] == "@me" and res["encrypted"] is True
    assert PASSWORD not in cap.out + cap.err and TOKEN not in cap.out + cap.err


# ------------------------------------------------------------------ collect
def test_collect_indexes_files_text_and_contacts(env, monkeypatch):
    monkeypatch.setenv("SAVEHUB_ICLOUD_EXTRACT_MAX_MB", "1")
    login(env.sd); before = _leftovers()
    res = collect(env)
    assert res["status"] == "ok" and res["backend"] == "pyicloud" and res["files"] == 5 and res["contacts"] == 3
    assert res["created"] == 8 and res["drive_folders"] == 4 and res["drive_truncated"] is False
    it = stored()
    t = it["会议纪要.txt"]; raw = json.loads(t["raw"])
    assert raw["kind"] == "file" and raw["path"] == "Documents/会议纪要.txt" and raw["size"] == len("会议纪要：预算审批 已通过".encode())
    assert raw["modified"].startswith("2026-10-05T09:00:00") and t["url"] == "https://www.icloud.com/iclouddrive/#doc=DOC-%E4%BC%9A%E8%AE%AE%E7%BA%AA%E8%A6%81.txt"
    assert "预算审批" in t["content"] and t["author"] == "iCloud Drive/Documents" and t["source"] == "collector:icloud:me"
    assert "ocean kayak" in it["Invoice-2026.pdf"]["content"] and json.loads(it["Invoice-2026.pdf"]["raw"])["content_type"] == "pdf"
    assert "收入增长" in it["季度报告.docx"]["content"] and json.loads(it["季度报告.docx"]["raw"])["path"] == "Documents/Work/季度报告.docx"
    assert "kopi beans" in it["Notes.md"]["content"]                       # app_library folders are walked too
    z = json.loads(it["Photos backup.zip"]["raw"])
    assert z["size"] == 2 * 1024 * 1024 and "text_chars" in z and "Photos backup.zip" not in WORLD["opens"]   # never downloaded
    # contacts
    c = it["Aisyah Rahman"]; cr = json.loads(c["raw"])
    assert cr["kind"] == "contact" and cr["phone"] == "+60 12-111 1111" and cr["emails"] == ["aisyah@example.com"]
    assert "王" in "".join(it) and "Apple Store KLCC" in it
    # account link + search (EN and 中文)
    acc = db.list_accounts("", "icloud")["accounts"]
    assert len(acc) == 1 and acc[0]["handle"] == "@me" and all(r["account_id"] == acc[0]["id"] for r in it.values())
    for q, title in (("预算审批", "会议纪要.txt"), ("季度报告", "季度报告.docx"), ("kayak", "Invoice-2026.pdf"), ("Aisyah", "Aisyah Rahman"),
                     ("Documents/Work", "季度报告.docx")):
        assert title in [x["title"] for x in db.list_items(q=q, platforms=["icloud"])["items"]], q
    # session refreshed + still encrypted; nothing left on disk
    assert sessions.is_encrypted(json.loads(sessions.session_path("icloud", env.sd, "me").read_text()))
    assert _leftovers() == before
    ct = db.search_contacts("Aisyah", platform="icloud")["contacts"]       # contacts mirrored into the contacts table
    assert len(ct) == 1 and ct[0]["org"] == "Kopi Sdn Bhd" and ct[0]["account_id"] == acc[0]["id"] and ct[0]["phones"] and ct[0]["emails"]
    assert db.search_contacts("王", platform="icloud")["total"] >= 1


def test_incremental_by_modified_date_updates_changed_files(env):
    login(env.sd); collect(env)
    WORLD["opens"].clear()
    docs = WORLD["tree"]["Documents"]
    t = docs["会议纪要.txt"]
    t.content = "会议纪要：预算审批 第二版 修订".encode(); t.size = len(t.content); t.data["etag"] = "e2"; t.date_modified = _dt("2026-10-10T10:00:00Z")
    docs._kids.append(Node("new-notes.txt", content=b"brand new file about sourdough", modified="2026-10-11T10:00:00Z"))
    docs._kids = [k for k in docs._kids if k.name != "Invoice-2026.pdf"]       # deleted on iCloud: stays in SaveHub
    WORLD["contacts"][0] = {**WORLD["contacts"][0], "etag": "c1v2", "jobTitle": "Barista"}
    old_id = stored()["会议纪要.txt"]["id"]
    # the auto-sync engine, running the real CLI child command in-process (incremental, sqlite sink)
    outs = []
    def run_fn(platform, account, max_items, timeout, sessions_dir):
        cmd = autosync.build_cmd(platform, account, max_items, sessions_dir)
        assert "--incremental" in cmd and not any("unsave" in c for c in cmd)
        buf = io.StringIO(); real = sys.stdout; sys.stdout = buf
        runtime.RUNNING.discard((platform, account))          # a real child is another process: only its flock applies
        try: code = cli.main(cmd[3:])
        finally: sys.stdout = real; runtime.RUNNING.add((platform, account))
        r = json.loads(buf.getvalue()); r["exit_code"] = code; outs.append(r); return r
    eng = autosync.Engine(sessions_dir=env.sd, run_fn=run_fn)
    autosync._update_state("icloud", "me", last_success=time.time() - 3600, status="ok", last_run=time.time() - 3600)
    res = eng.run_account("icloud", "me", "test")
    r = outs[0]
    assert res["status"] == "ok" and r["incremental"] is True, r
    assert sorted(WORLD["opens"]) == ["new-notes.txt", "会议纪要.txt"]       # unchanged files are not downloaded again
    assert r["files"] == 2 and r["drive_unchanged"] == 3 and r["created"] == 1 and r["updated"] == 2   # file + contact updated
    it = stored()
    assert it["会议纪要.txt"]["id"] == old_id and "第二版" in it["会议纪要.txt"]["content"]
    assert json.loads(it["会议纪要.txt"]["raw"])["version"] == "e2"
    assert "Invoice-2026.pdf" in it and "new-notes.txt" in it            # never un-saves / deletes
    assert "Barista" in it["Aisyah Rahman"]["content"]
    # a third pass with nothing changed downloads nothing and pushes nothing
    WORLD["opens"].clear(); autosync._update_state("icloud", "me", last_run=time.time() - 3600)
    eng.run_account("icloud", "me", "test")
    assert WORLD["opens"] == [] and outs[-1]["files"] == 0 and outs[-1]["created"] == 0
    st = autosync.status(env.sd)["accounts"][0]
    assert st["platform"] == "icloud" and st["account"] == "@me" and st["state"] == "synced"


def test_max_items_newest_first(env):
    login(env.sd)
    res = collect(env, max_items=2)
    assert res["files"] == 2 and res["contacts"] == 2
    assert {"会议纪要.txt", "Photos backup.zip"} <= set(stored())          # the two most recently modified files


# ------------------------------------------------------------------ expiry -> needs login
def test_rejected_session_needs_login_everywhere(env, capsys):
    from savehub import mcp_server
    login(env.sd); collect(env)
    WORLD["revoked"] = True                                               # Apple dropped the trusted session
    with pytest.raises(NeedLogin) as ei:
        collect(env)
    assert "login icloud --account @me" in ei.value.fix
    sfile = sessions.session_path("icloud", env.sd, "me")
    assert sessions.describe(sfile)["status"] == "expired"               # marked, still encrypted, jar kept for re-login
    assert sessions.is_encrypted(json.loads(sfile.read_text()))
    # CLI exit code 2 (what MCP collect / auto-sync see)
    rc = cli.main(["--sessions-dir", str(env.sd), "collect", "icloud", "--sink", "sqlite", "--db", str(env.db), "--account", "me"])
    out = json.loads(capsys.readouterr().out)
    assert rc == EXIT_NEED_LOGIN and out["status"] == "need_login" and out["fix"].endswith("login icloud --account @me")
    # MCP login_status
    ls = mcp_server.login_status("icloud")
    a = ls["accounts"][0]
    assert ls["logged_in"] is False and a["session"] == "expired" and a["needs_login"] and "login icloud --account @me" in a["fix"]
    assert "Apple ID" in ls["fix"] and ls["login"] == "terminal"
    # auto-sync: needs_login, not retried until the session file changes
    eng = autosync.Engine(sessions_dir=env.sd, run_fn=lambda *a: pytest.fail("must not run an expired session"))
    assert eng.run_account("icloud", "me")["status"] == "needs_login"
    st = mcp_server.sync_status()["accounts"][0]
    assert st["state"] == "needs_login" and st["fix"] == "python -m savehub.collector login icloud --account @me"
    assert eng.due() == []
    # logging in again (trust token reused: no new 2FA prompt) makes it valid and due again
    WORLD["revoked"] = False; WORLD["token"] = ""; sent = WORLD["2fa_sent"]
    time.sleep(0.01); login(env.sd)
    assert WORLD["2fa_sent"] == sent and sessions.describe(sfile)["status"] == "saved"
    assert ("icloud", "me") in eng.due()


def test_2fa_requested_again_or_midrun_rejection_is_need_login(env):
    login(env.sd)
    WORLD["ask_2fa_again"] = True
    with pytest.raises(NeedLogin): collect(env)
    login(env.sd); WORLD["ask_2fa_again"] = False; WORLD["revoked"] = False
    WORLD["revoke_midrun"] = True
    with pytest.raises(NeedLogin): collect(env)
    assert sessions.describe(sessions.session_path("icloud", env.sd, "me"))["status"] == "expired"


def test_cookie_expiry_alone_is_expired(env):
    WORLD["cookie_ttl"] = -10
    _, sfile = login(env.sd)
    assert sessions.describe(sfile)["status"] == "expired"


# ------------------------------------------------------------------ multi-account
def test_two_accounts(env):
    login(env.sd, "me"); login(env.sd, "work", apple_id="work.id@icloud.com")
    assert {s["account"] for s in sessions.list_sessions(env.sd, "icloud")} == {"me", "work"}
    collect(env, "me"); r = collect(env, "work")
    assert r["created"] == 0 and r["files"] == 0 and r["contacts"] == 0  # same files already stored: nothing re-downloaded
    assert {a["handle"] for a in db.list_accounts("", "icloud")["accounts"]} == {"@me", "@work"}


# ------------------------------------------------------------------ encrypted session round-trip
def test_session_roundtrip_aad_and_wrong_key(env, monkeypatch):
    _, sfile = login(env.sd)
    st = sessions.read_state(sfile)
    sessions.write_state(sfile, st)
    assert sessions.read_state(sfile) == st
    other = sfile.with_name("icloud@other.json"); other.write_text(sfile.read_text())
    with pytest.raises(sessions.SessionLocked): sessions.read_state(other)     # AAD = icloud@<account>
    monkeypatch.setenv("SAVEHUB_VAULT_KEY", Fernet.generate_key().decode())
    assert sessions.describe(sfile)["status"] == "locked"
    with pytest.raises(sessions.SessionLocked): collect(env)


# ------------------------------------------------------------------ password / secrets never exposed
def test_password_never_in_mcp_or_rest_schemas():
    from savehub import mcp_server, app as webapp
    tools = asyncio.run(mcp_server.mcp.list_tools())
    bad = re.compile(r"pass|secret|otp|2fa|cookie|apple_?id|token", re.I)
    for t in tools:
        for prop in (t.inputSchema or {}).get("properties", {}):
            assert not bad.search(prop), (t.name, prop)
    spec = webapp.app.openapi()
    for path, ops in spec["paths"].items():
        assert "icloud" not in path.lower() and "login" not in path.lower(), path
        for op in ops.values():
            for p in op.get("parameters", []): assert not bad.search(p["name"]), (path, p["name"])
            body = json.dumps(op.get("requestBody", {}))
            assert not re.search(r'"(password|apple_id|otp|code)"', body), path


def test_password_env_never_reaches_children(monkeypatch):
    monkeypatch.setenv("ICLOUD_PASSWORD", PASSWORD); monkeypatch.setenv("SAVEHUB_ICLOUD_ROOT", "Documents")
    env = runtime.child_env("/tmp/x.db")
    assert "ICLOUD_PASSWORD" not in env and PASSWORD not in json.dumps(env) and env["SAVEHUB_ICLOUD_ROOT"] == "Documents"


def test_mcp_collect_refuses_unsave(env):
    from savehub import mcp_server
    login(env.sd)
    r = asyncio.run(mcp_server.collect("icloud", unsave=True))
    assert r["status"] == "unsave_not_supported"
    with pytest.raises(CollectorError, match="un-save"):
        runner.run_collect("icloud", RunConfig(account_handle="me", unsave=True), SqliteSink(str(env.db)), env.sd)


def test_source_has_no_icloud_write_calls():
    src = (ROOT / "savehub" / "collector" / "platforms" / "icloud.py").read_text()
    assert not re.search(r"\.(delete|delete_items|delete_forever|move_to_trash|move_items_to_trash|rename|rename_items|upload|"
                         r"send_file|mkdir|create_folders|recover)\(", src)
    assert "accept_terms=True" not in src and "store_password_in_keyring" not in src


def test_root_scoping(env, monkeypatch):
    monkeypatch.setenv("SAVEHUB_ICLOUD_ROOT", "Documents/Work")
    login(env.sd); r = collect(env)
    assert r["files"] == 1 and list(json.loads(x["raw"])["path"] for x in stored().values() if json.loads(x["raw"]).get("kind") == "file") == ["季度报告.docx"]


def test_real_pyicloud_reads_and_writes_our_seeded_files(env, monkeypatch):
    """Compatibility with the pinned real pyicloud (no network: authenticate=False): it must pick up the seeded
    <stem>.session / <stem>.cookiejar from the private dir, and snapshot() must read back what it writes."""
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", icloud.KEYRING_NULL)
    from pyicloud import PyiCloudService
    WORLD["token"] = "tok-123"
    with icloud.private_workdir() as d:
        fake = FakeService(APPLE_ID, PASSWORD, cookie_directory=str(d), authenticate=False)
        fake.data.update(session_token="tok-123", trust_token="tr-1", client_id="cid-xyz"); fake._save()
        st = icloud.snapshot(d, APPLE_ID, False)
    with icloud.private_workdir() as d2:
        icloud.seed_workdir(d2, st["icloud"])
        api = PyiCloudService(APPLE_ID, None, cookie_directory=str(d2), accept_terms=False, authenticate=False)
        assert api.session.data.get("session_token") == "tok-123" and api.params["clientId"] == "cid-xyz"
        assert any(c.name == "X-APPLE-WEBAUTH-TOKEN" for c in api.session.cookies)
        api.session._save_session_data()                          # pyicloud rewrites both files in place
        again = icloud.snapshot(d2, APPLE_ID, False, st["icloud"])
        assert again["icloud"]["session"]["session_token"] == "tok-123" and TOKEN in again["icloud"]["cookiejar"]
        assert api._password_raw is None


def test_login_and_collect_refuse_without_vault_key(env, monkeypatch):
    """The trusted session is never written in plaintext: no vault key -> login refuses before asking for the password."""
    login(env.sd)                                                      # with a key: fine
    monkeypatch.delenv("SAVEHUB_VAULT_KEY"); vault.set_runtime_key("")
    asked = []
    with pytest.raises(CollectorError, match="vault key"):
        icloud.ICloudCollector().terminal_login(sessions.session_path("icloud", env.sd, "two"), "two", stdin=io.StringIO(APPLE_ID + "\n"),
                                                stdout=io.StringIO(), ask_password=lambda p: asked.append(p) or PASSWORD, require_tty=False)
    assert not asked and not sessions.session_path("icloud", env.sd, "two").exists()
    with pytest.raises(CollectorError, match="vault key"):
        icloud.ICloudCollector().collect_api({"cookies": [], "icloud": {"apple_id": APPLE_ID}}, RunConfig(), env.sd / "icloud@x.json", "x")
    assert not (env.sd / "icloud@x.json").exists()


# ======================================================================== login-terminal launcher (hand the login to the user)
class _FakeProc:
    pid = 4242


def _launch(monkeypatch, tmp_path, **kw):
    from savehub.collector import terminal
    calls = []
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_SHOULD_NOT_LEAK")
    monkeypatch.setenv("SAVEHUB_DASHBOARD_PASSWORD", "dash-SHOULD_NOT_LEAK")
    monkeypatch.setenv("SAVEHUB_DB", str(tmp_path / "t.db"))
    which = kw.pop("which", lambda n: f"/usr/bin/{n}" if n in ("xterm", "xfce4-terminal") else None)
    r = terminal.launch_login_terminal("icloud", kw.pop("account", "@js.yip"), tmp_path / "sess", kw.pop("display", ":7"),
                                       kw.pop("terminal", None), popen=lambda argv, **o: calls.append((argv, o)) or _FakeProc(), which=which)
    return r, calls


def test_login_terminal_argv_env_and_no_password(monkeypatch, tmp_path):
    r, calls = _launch(monkeypatch, tmp_path)
    assert r["status"] == "launched" and r["terminal"] == "xterm" and r["display"] == ":7" and r["account"] == "@js.yip" and r["pid"] == 4242
    (argv, opts), = calls
    assert argv[:4] == ["/usr/bin/xterm", "-T", "SaveHub login: iCloud @js.yip", "-e"] and argv[4:6] == ["sh", "-c"]
    tail = argv[argv.index("savehub-login") + 1:]
    assert tail == [sys.executable, "-m", "savehub.collector", "--sessions-dir", str(tmp_path / "sess"), "login", "icloud", "--account", "@js.yip"]
    env = opts["env"]
    assert env["DISPLAY"] == ":7" and env["SAVEHUB_DB"] == str(tmp_path / "t.db")
    assert "GITHUB_TOKEN" not in env and "SAVEHUB_DASHBOARD_PASSWORD" not in env
    assert opts["start_new_session"] and opts.get("shell") is None
    blob = json.dumps([argv, r]).lower()
    assert "password=" not in blob and "ghp_" not in blob and "should_not_leak" not in json.dumps(env).lower()
    # xfce4-terminal: own process (not a shared server) and -x
    r, calls = _launch(monkeypatch, tmp_path, terminal="xfce4-terminal")
    assert calls[0][0][:5] == ["/usr/bin/xfce4-terminal", "--disable-server", "--title", "SaveHub login: iCloud @js.yip", "-x"]


def test_login_terminal_refusals(monkeypatch, tmp_path):
    from savehub.collector import terminal
    with pytest.raises(CollectorError, match="own browser window"):
        terminal.launch_login_terminal("x", "@a", tmp_path, ":1", popen=lambda *a, **k: pytest.fail("launched"))
    with pytest.raises(ValueError):                                      # shell metacharacters never reach a command line
        _launch(monkeypatch, tmp_path, account="@a;rm -rf ~")
    with pytest.raises(CollectorError, match="invalid display"):
        _launch(monkeypatch, tmp_path, display=":1; xterm")
    with pytest.raises(CollectorError, match="no terminal emulator"):
        _launch(monkeypatch, tmp_path, which=lambda n: None)
    monkeypatch.delenv("DISPLAY", raising=False)
    with pytest.raises(CollectorError, match="no X display"):
        terminal.launch_login_terminal("icloud", "@a", tmp_path, None, popen=lambda *a, **k: pytest.fail("launched"), which=lambda n: "/usr/bin/xterm")


def test_login_terminal_cli(monkeypatch, tmp_path, capsys):
    from savehub.collector import terminal
    seen = {}
    monkeypatch.setattr(terminal, "launch_login_terminal", lambda *a: seen.setdefault("args", a) and {"status": "launched"})
    assert cli.main(["--sessions-dir", str(tmp_path), "login-terminal", "icloud", "--account", "@me", "--display", ":3"]) == 0
    assert seen["args"] == ("icloud", "@me", str(tmp_path), ":3", None)
    monkeypatch.undo()
    assert cli.main(["login-terminal", "x", "--account", "@me", "--display", ":3"]) != 0
    assert "own browser window" in capsys.readouterr().out
