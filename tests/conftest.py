"""Test isolation:
- never pick up a real vault key (env or data/.vault_key) - tests that need one set a throwaway key;
- never touch the real library: SAVEHUB_DB is pointed at a throwaway DB *before* any test module imports savehub, so a
  module that forgets to isolate itself (or runs after another module already imported savehub.db) can't fall back to
  data/savehub.db. A session-end check fails the run if the real DB was written anyway."""
import os, pathlib, tempfile
import pytest

os.environ.pop("SAVEHUB_VAULT_KEY", None)
os.environ["SAVEHUB_VAULT_KEY_FILE"] = os.path.join(tempfile.mkdtemp(prefix="savehub-nokey-"), "absent.key")
os.environ["SAVEHUB_DB"] = os.path.join(tempfile.mkdtemp(prefix="savehub-testdb-"), "default.db")

REAL_DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
_REAL = [REAL_DATA / n for n in ("savehub.db", "savehub.db-wal", ".vault_key", ".mcp_token")]


def _snap():
    return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in _REAL if p.exists()}


@pytest.fixture(scope="session", autouse=True)
def _real_library_untouched():
    from savehub import db
    assert REAL_DATA not in pathlib.Path(db.DB_PATH).resolve().parents, f"tests must not use the real DB ({db.DB_PATH})"
    before = _snap()
    yield
    # the live auto-sync loop may legitimately write the real DB while tests run, so only the files tests could
    # create from scratch are compared strictly; the DB itself is checked for rows written by tests (see test_all)
    after = _snap()
    for k in (str(REAL_DATA / ".vault_key"), str(REAL_DATA / ".mcp_token")):
        assert before.get(k) == after.get(k), f"a test modified {k}"
