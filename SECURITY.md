# SaveHub security model

SaveHub is a personal tool. The goal is that the things that are equivalent to *being logged in* (browser sessions,
tokens, the vault key) never leave the machine they were created on, never show up in logs, Git, REST or MCP, and that
nothing on a social app gets removed unless you explicitly ask for it.

## Where secrets live

| What | Where | Protection |
|---|---|---|
| Browser sessions (cookies + localStorage, per platform/account) | `data/sessions/<platform>@<handle>.json` on the box | dir `0700`, files `0600` (atomic writes under umask `077`), git-ignored (`data/*` + explicit rules), docker-ignored; **AES-256-GCM encrypted at rest when a vault key is set** |
| Vault master key | `SAVEHUB_VAULT_KEY` env var, or `data/.vault_key` (`0600`, git-ignored; override path with `SAVEHUB_VAULT_KEY_FILE`) | never logged, printed, returned or put in a child's environment |
| Account passwords (optional vault) | DB table `account_secrets` | AES-256-GCM, account id as AAD; only revealed to a logged-in dashboard user |
| MCP bearer token | `SAVEHUB_MCP_TOKEN` or `data/.mcp_token` (`0600`) | constant-time compare; the generated token is **not printed** anymore — read it from the file |
| Dashboard login | `SAVEHUB_DASHBOARD_USER` / `SAVEHUB_DASHBOARD_PASSWORD` env | HTTP Basic, constant-time compare; no cookies are set at all |
| SQLite DB | `data/savehub.db` | created owner-only (umask `077`); `-wal`/`-shm` and key files tightened to `0600` at start |

No passwords for the social apps are ever stored by the collector: you log in yourself in a real browser window on the box
(`python -m savehub.collector login <platform> --account @<handle>`); only the resulting session is kept.

## Session encryption at rest
* When a vault key is configured, every session write is encrypted with AES-256-GCM. The file key is derived from the master
  key per file salt (HKDF-SHA256 for a generated Fernet-style key, PBKDF2-HMAC-SHA256 600k for a passphrase), independent of
  the DB, and the session name `<platform>@<account>` is bound as associated data, so a file copied onto another account
  does not decrypt.
* Reads decrypt in memory and hand Playwright a dict: no plaintext temp file is written.
* Backward compatible: legacy plaintext session files are still read and are re-saved encrypted the first time a collector
  (collect / auto-sync / login) uses them. Read-only status calls (dashboard, MCP `login_status`, `sync_status`) never rewrite files.
* Without the key (or with the wrong one) an encrypted session shows as `locked`; nothing falls back to plaintext.
* Losing the key only loses the sessions (log in again); keep `data/.vault_key` backed up if you also use the password vault.

## Subprocesses
Collector runs (MCP `collect`, auto-sync, "Sync now") are separate processes with an **allow-listed** environment
(`PATH`, `HOME`, locale, display, Playwright paths, `SAVEHUB_DB`, …) minus anything whose name looks secret
(`*TOKEN*`, `*PASSWORD*`, `*SECRET*`, `*KEY*`-style API keys, `*VAULT*`, `*COOKIE*`, `*AUTH*`, credentialed proxy URLs).
`GITHUB_TOKEN`, `SAVEHUB_DASHBOARD_PASSWORD`, `SAVEHUB_MCP_TOKEN` and `SAVEHUB_VAULT_KEY` are never passed. When a child must open
encrypted sessions, the key is written to its **stdin pipe** (`--key-stdin`), not argv or env. Children run in their own process
group, are killed on timeout, and use fixed argv (no shell).

## Library backends (twscrape, instaloader)
* They get the session's storage_state **decrypted in memory** and only the cookies of their own site (`x.com`/`twitter.com`,
  `instagram.com`); no password is ever involved. instaloader keeps the cookie jar in memory (no session/resume files).
  twscrape insists on an SQLite accounts file: it is created `0600` inside a `0700` temp dir and removed in `finally`
  (tests assert nothing is left). Library backends never write the session file back.
* twscrape telemetry is disabled in-process (`TWS_TELEMETRY=0`, `DO_NOT_TRACK=1`); its logs and every backend error message go
  through the scrubber. They run inside the same stripped-environment collector child as the browser path.
* They are read-only: un-save always runs on the Playwright path. Versions are pinned in `requirements-collector.txt` and
  covered by `pip-audit`.

## Un-save safety
* Auto-sync **never** un-saves: its child command has no un-save flags (enforced at runtime) and the CLI refuses
  `--incremental` together with `--unsave`. The REST "Sync now" endpoint rejects any extra field (e.g. `unsave`).
* Manual collects un-save only with `--unsave --unsave-confirm` (CLI) or `collect(unsave=true)` (MCP), only items confirmed stored
  in SaveHub (POST + read-back), at most 50 per run, each verified on the app.

## REST / dashboard
* With `SAVEHUB_DASHBOARD_PASSWORD` set, every request except `/healthz` needs HTTP Basic auth (combined server dispatcher and
  `python -m savehub.app` alike). Sensitive endpoints (vault, auto-sync) verify it again themselves.
* Without a dashboard password (local use), the auto-sync endpoints (`GET/PUT /api/autosync`, `POST /api/autosync/run`) only answer
  a loopback client with a loopback `Host` header (defeats DNS rebinding, and makes them unusable on a password-less public deploy).
* CSRF: every state-changing `/api/*` request with a cross-site `Origin`/`Sec-Fetch-Site` (or `Origin: null`) is refused (403);
  the vault and auto-sync writes additionally require `Content-Type: application/json`.
* "Sync now" is rate-limited (one per account and one global request per 60 s → `429` + `Retry-After`); platform must be an
  implemented collector and the handle must match `^[a-z0-9][a-z0-9._-]{0,63}$` (no path traversal; platform ids `^[a-z0-9_]{1,32}$`).
* Headers on every response: `Content-Security-Policy` (`default-src 'self'`, no framing, no plugins, `connect-src 'self'`;
  `script-src` still allows `'unsafe-inline'` because the dashboard uses inline handlers), `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, COOP/CORP, `Cache-Control: no-store`
  on `/api/*`, HSTS behind HTTPS. Links built from imported data only allow `http(s)` URLs (no `javascript:`).
* Session files, cookies, session paths and keys are never returned by any endpoint.

## MCP
* HTTP transport: every request needs `Authorization: Bearer <token>` (all tools, including `collect`, `set_autosync`,
  `sync_status`); DNS-rebinding protection limits Host/Origin. stdio is local by nature.
* MCP **can**: search/list/add items and accounts, import exports, read session *status* (`login_status`, `sync_status`), run
  collects (`collect`, needs an existing session and a browser on that server), turn auto-sync on/off and set its interval.
* MCP **cannot**: log in, read or return cookies/session files/keys/passwords, un-save without an explicit `unsave=true`, make
  auto-sync un-save, or read SaveHub's own secrets via `import_file` (session dir, DB, key/token files, hidden paths such as
  `~/.ssh` or `.git`, `/proc`, `/sys`, `/dev`, `/etc` and symlinks into them are refused; files capped at 50 MB).

## Logs
All collector log lines, uvicorn/app logs (filter installed at server start), stored auto-sync errors and child stderr tails
pass through a scrubber that removes the values of secret-looking env vars (`GITHUB_TOKEN`, `SAVEHUB_*_PASSWORD`,
`SAVEHUB_VAULT_KEY`, `*_API_KEY`, …), the in-memory vault key, bearer/basic credentials, GitHub tokens, `Cookie`/`Set-Cookie`/
`Authorization` headers, cookie `"value"` fields and credentials in URLs. Session file contents are never logged.

## Pushing to GitHub
The remote URL stays tokenless (`https://github.com/js713-lab/savehub.git`); pushes use a one-shot credential helper that reads
`GITHUB_TOKEN` from the environment for that single command, so the token is never written to `.git/config`, the shell history
or logs.

## Checks
`bandit -r savehub` (no high/medium findings; remaining lows are reviewed `try/except` fallbacks, non-crypto `random` for scroll
jitter and false-positive "hardcoded password" strings) and `pip-audit` (no known vulnerabilities) are run before releases.
Reporting a problem: open a private security advisory on the GitHub repo.
