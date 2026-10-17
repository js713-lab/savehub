"""Open a terminal window on the box's desktop running a terminal-mode login (iCloud), so it can be handed to the user
the same way as the browser logins (which open their own Chromium window on the desktop).

    python -m savehub.collector login-terminal icloud --account @js.yip [--display :1] [--terminal xterm]

The window runs the ordinary `python -m savehub.collector login icloud --account @...` command; the Apple ID password and
2FA code are typed into THAT terminal (getpass, no echo) and go straight to Apple via pyicloud. Nothing here, in REST or in
MCP ever sees or stores the password: this launcher only builds a fixed argv (no shell string interpolation of user input,
account handle validated by sessions.account_key) and an allow-listed environment without tokens/passwords.
"""
from __future__ import annotations
import os, re, shutil, subprocess, sys
from typing import Callable, Optional
from .base import REGISTRY, CollectorError
from . import sessions, runtime

TERMINALS = ("xterm", "xfce4-terminal", "x-terminal-emulator", "gnome-terminal", "konsole", "lxterminal", "mate-terminal")
_DISPLAY_RE = re.compile(r"^[A-Za-z0-9_.\-/]*:\d+(\.\d+)?$")
# the wrapper keeps the window open after the login so the user can read the result; user input only ever arrives as "$@"
_WRAP = ('printf "SaveHub login: %s\\n(the password and 2FA code are typed here and go only to the provider; nothing is stored but the trusted session)\\n\\n" "$SAVEHUB_LOGIN_LABEL"; '
         '"$@"; rc=$?; printf "\\nFinished (exit %s). Press Enter to close this window." "$rc"; read _')
# env passed to the window: the collector allow-list plus what the login needs to find the DB / sessions / vault key
_EXTRA_ENV = ("SAVEHUB_DB", "SAVEHUB_VAULT_KEY", "SAVEHUB_VAULT_KEY_FILE", "TERM", "COLORTERM")


def find_terminal(preferred: Optional[str] = None, which: Callable = shutil.which) -> Optional[str]:
    names = ([preferred] if preferred else []) + [t for t in TERMINALS if t != preferred]
    for n in names:
        p = which(n)
        if p: return p
    return None


def terminal_argv(term: str, title: str, inner: list) -> list:
    """argv that opens `term` with `title` running `inner` (a list, never a shell string)."""
    kind = os.path.basename(os.path.realpath(term))
    base = os.path.basename(term)
    if "xfce4-terminal" in (kind, base) or kind.startswith("xfce4-terminal"): return [term, "--disable-server", "--title", title, "-x", *inner]   # own process, not a shared terminal server
    if "gnome-terminal" in kind or "gnome-terminal" in base: return [term, "--title", title, "--", *inner]
    if "konsole" in (kind, base): return [term, "-p", f"tabtitle={title}", "-e", *inner]
    if "lxterminal" in (kind, base) or "mate-terminal" in (kind, base): return [term, "-t", title, "-e", " ".join(map(_q, inner))]
    return [term, "-T", title, "-e", *inner]          # xterm and the Debian x-terminal-emulator convention


def _q(s: str) -> str:
    import shlex
    return shlex.quote(s)


def login_env(display: str) -> dict:
    env = runtime.child_env(os.environ.get("SAVEHUB_DB", ""))
    if not os.environ.get("SAVEHUB_DB"): env.pop("SAVEHUB_DB", None)
    env.update({k: os.environ[k] for k in _EXTRA_ENV if os.environ.get(k)})
    env["DISPLAY"] = display
    for k in list(env):
        if runtime.is_secret_name(k) and not k.startswith("SAVEHUB_VAULT_KEY"): env.pop(k)   # e.g. GITHUB_TOKEN, *_PASSWORD
    return env


def launch_login_terminal(platform: str, account: str = "", sessions_dir=None, display: Optional[str] = None,
                          terminal: Optional[str] = None, popen: Callable = subprocess.Popen, which: Callable = shutil.which) -> dict:
    cls = REGISTRY.get(platform)
    if cls is None: raise CollectorError(f"unknown platform '{platform}'; one of {sorted(REGISTRY)}")
    if getattr(cls, "login_mode", "browser") != "terminal":
        raise CollectorError(f"'{platform}' logs in through its own browser window: run `python -m savehub.collector login {platform} "
                             f"--account @<handle>` on the box desktop instead")
    key = sessions.account_key(account)                                  # validates the handle (ValueError -> CLI error)
    display = display or os.environ.get("DISPLAY") or ""
    if not display: raise CollectorError("no X display: pass --display :N (the box desktop the user is looking at) or set DISPLAY")
    if not _DISPLAY_RE.match(display): raise CollectorError(f"invalid display '{display}' (expected e.g. :1)")
    term = find_terminal(terminal, which)
    if not term: raise CollectorError(f"no terminal emulator found (tried {', '.join(TERMINALS)}); install xterm or run the login in any terminal: "
                                      f"python -m savehub.collector login {platform}" + (f" --account @{key}" if key != sessions.DEFAULT_ACCOUNT else ""))
    login = [sys.executable, "-m", "savehub.collector"] + (["--sessions-dir", str(sessions.sessions_dir(sessions_dir))] if sessions_dir else []) + \
            ["login", platform] + (["--account", "@" + key] if key != sessions.DEFAULT_ACCOUNT else [])
    label = f"{getattr(cls, 'name', platform)}" + (f" @{key}" if key != sessions.DEFAULT_ACCOUNT else "")
    inner = ["sh", "-c", _WRAP, "savehub-login", *login]
    env = {**login_env(display), "SAVEHUB_LOGIN_LABEL": label}
    argv = terminal_argv(term, f"SaveHub login: {label}", inner)
    proc = popen(argv, cwd=str(sessions.ROOT), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # nosec B603 - fixed argv list, no shell=True; user input is a validated handle passed as "$@"
                 stderr=subprocess.DEVNULL, start_new_session=True)
    return {"status": "launched", "platform": platform, "account": "@" + key if key != sessions.DEFAULT_ACCOUNT else "default",
            "pid": getattr(proc, "pid", None), "terminal": os.path.basename(term), "display": display,
            "command": " ".join(_q(x) for x in ["python", *login[1:]]),
            "note": "type the Apple ID, password and 2FA code in that window; only the trusted session is kept (encrypted)"}
