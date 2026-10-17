"""Test isolation: never pick up a real vault key (env or data/.vault_key) - tests that need one set a throwaway key."""
import os, tempfile

os.environ.pop("SAVEHUB_VAULT_KEY", None)
os.environ["SAVEHUB_VAULT_KEY_FILE"] = os.path.join(tempfile.mkdtemp(prefix="savehub-nokey-"), "absent.key")
