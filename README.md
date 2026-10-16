# SaveHub v1 — all your saves, bookmarks, favourites, contacts & accounts in one place

Local-first dashboard + MCP server. Python (FastAPI) + SQLite FTS5 (trigram tokenizer, so English **and 中文** substring search both work) + a single-page vanilla-JS UI.

## Run (one command)
```bash
./run.sh                      # first run creates .venv + installs requirements, seeds sample data
# → dashboard  http://127.0.0.1:8765/
# → MCP (streamable HTTP, same port)  http://127.0.0.1:8765/mcp   (Authorization: Bearer $SAVEHUB_MCP_TOKEN)
```
`./run.sh` starts `python -m savehub.server`: dashboard, REST API **and** MCP on **one port**. If `SAVEHUB_MCP_TOKEN` is unset, a token is generated, printed to the log and saved to `data/.mcp_token`.

Other entry points:
```bash
.venv/bin/python -m savehub.mcp_server                       # MCP over stdio (for Claude Desktop / Cursor local config)
.venv/bin/python -m savehub.mcp_server --http --port 8766    # standalone MCP HTTP server (optional)
.venv/bin/python -m savehub.app                              # dashboard only (no /mcp)
```

## Deploy to Render (free tier)
1. Push this folder to a Git repo (GitHub/GitLab/Bitbucket). It already contains `Dockerfile` and `render.yaml`.
2. In Render: **New → Blueprint**, choose the repo. Render reads `render.yaml` and creates a free Docker web service `savehub`.
3. During setup Render asks for the `sync: false` vars. All are **optional**, so you can leave them blank: `OPENAI_API_KEY` or `XAI_API_KEY` (AI tagging), `X_CLIENT_ID`, `NOTION_TOKEN`, `NOTION_DATABASE_ID`. `SAVEHUB_MCP_TOKEN` is generated automatically.
4. After deploy:
   - Dashboard: `https://<service>.onrender.com/` (login `savehub` / value of `SAVEHUB_DASHBOARD_PASSWORD` from the Environment tab)
   - Health: `https://<service>.onrender.com/healthz`
   - MCP: `https://<service>.onrender.com/mcp` with header `Authorization: Bearer <SAVEHUB_MCP_TOKEN>`. To get the value: Render dashboard → service → Environment → reveal `SAVEHUB_MCP_TOKEN`.
   - For X OAuth, add the callback `https://<service>.onrender.com/api/x/callback` in your X app. It is derived automatically from `RENDER_EXTERNAL_HOSTNAME`.

```json
{ "mcpServers": { "savehub": { "url": "https://<service>.onrender.com/mcp", "headers": { "Authorization": "Bearer <SAVEHUB_MCP_TOKEN>" } } } }
```

How it works: one uvicorn process binds `0.0.0.0:$PORT`. `/mcp` goes to the MCP streamable-HTTP app (bearer auth, stateless mode), and everything else goes to the dashboard/API. MCP DNS-rebinding protection allows localhost, `$RENDER_EXTERNAL_HOSTNAME` (set by Render) and any hosts in `SAVEHUB_ALLOWED_HOSTS` (comma list, e.g. a custom domain).

**Free-tier caveats (please read):**
- **No persistent disk on Free.** Render docs: *"Paid services can preserve local filesystem changes by attaching a persistent disk, but Free web services cannot."* SQLite therefore lives on the ephemeral filesystem, and **the library resets to the sample data on every deploy, restart or idle spin-down**. Export regularly (Settings → Drive folder export or Notion sync), or upgrade to a paid plan and uncomment the `disk:` block in `render.yaml` (mount `/workspace/savehub/data`).
- **Cold starts:** the service sleeps after 15 min without traffic, and the first request takes about 1 min. MCP clients may time out on that first call, so just retry.
- Settings saved in the UI (Notion token, X tokens) live in the same ephemeral DB. Prefer env vars on Render.
- Dashboard login: `render.yaml` generates `SAVEHUB_DASHBOARD_PASSWORD`. The browser asks for HTTP Basic credentials: user `savehub` (or `SAVEHUB_DASHBOARD_USER`), password = that env value. `/healthz` is open, and `/mcp` uses its own bearer token. If you remove the password var, the dashboard is public.

## Features
| Area | Status |
|---|---|
| Library: full-text search (EN/中文), sort by date saved / platform / author / title / relevance, platform chips, tag chips, account filter, card + table views, dark/light | ✅ tested |
| Auto-tagging (tags + 1-line summary per item): OpenAI-compatible LLM if `OPENAI_API_KEY` or `XAI_API_KEY` set, else local keyword + TF-IDF tagger | ✅ local tagger tested · ⚠️ LLM path implemented, **not tested** (no key on build box) |
| Summary/digest: counts by platform, top tags & authors, recent saves, written digest (AI if key, template otherwise) | ✅ template tested · ⚠️ AI digest untested |
| Accounts: many accounts per platform; add/edit/delete; search/sort/filter; CSV import/export; items & contacts link via `account_id` | ✅ tested. Plaintext passwords are never stored (generic account API rejects password/secret fields; CSV password columns are dropped) — see the encrypted vault below |
| Accounts UX: guided **Add account** (pick app from tiles → form; `@` auto-prefix, profile URL auto-built for X/Threads/Instagram/Facebook/Messenger/TikTok/WhatsApp, inline validation, duplicate check, Enter/Esc, **Save & add another** with running count + undo), **Paste handles** bulk add with live preview (`POST /api/accounts/preview`, `POST /api/accounts/bulk`; lines like `@foo`, `ig @foo Name`, `foo, Name`, profile links), grouped-by-platform cards or table, platform/status segments with counts, search (`/`), sort, inline quick edit, delete with confirmation (+ undo when nothing is linked), toasts, empty state, mobile bottom-sheet, `GET /api/accounts/stats` | ✅ tested (`tests/accounts_ui_test.py`, headless Chromium, 30 checks) |
| Contacts: vCard (.vcf) + CSV (Google/Outlook style) import, searchable, linkable to account | ✅ tested |
| Importers (see below) | ✅ tested with the synthetic files in `samples/` — real exports vary; adjust if a field is missed |
| X live API: OAuth 2.0 PKCE → `GET /2/users/:id/bookmarks` (paginated, refresh tokens) | ⚠️ implemented, auth-URL generation tested; **token exchange & fetch untested** (needs your Client ID + an X API tier with bookmarks) |
| Notion sync (integration token + database id) | ⚠️ implemented, **untested** |
| Google Drive: `folder` mode writes JSON/CSV/Markdown into a folder (point it at a *Google Drive for desktop* folder) | ✅ folder export tested |
| Google Drive `service_account` mode | ⚠️ stub-quality, **untested**; needs `pip install google-api-python-client google-auth` |
| MCP server (official `mcp` Python SDK, FastMCP), stdio + streamable HTTP at `/mcp` on the dashboard port with bearer token | ✅ all tools exercised via `tests/mcp_client_test.py` |

### Connection method per platform (honest)
| Platform | Method |
|---|---|
| X | **Live API** (OAuth 2.0 PKCE, bookmarks endpoint) — your own developer app |
| Instagram / Facebook / Threads | Import “Download your information” **JSON** export (generic walker handles saved_posts / saves_v2 shapes) |
| Messenger | Import messages JSON from the same export; shared links → items |
| WhatsApp | “Export chat” `.txt`; links → items (author = sender) |
| TikTok | Data export `user_data.json` (Favorite Videos + Like List) |
| WeChat / 抖音 / 小红书 | No export of favourites — paste share text/links (share-text boilerplate stripped), or CSV / JSON / HTML link list |
| Web | Add link, paste links, browser bookmarks HTML (Netscape format) |

None of the non-X apps offer a public API for personal saved items. The server never scrapes them; the optional, **local-only** browser collector below can (X implemented, others stubbed).

## Env vars
| Var | Purpose |
|---|---|
| `SAVEHUB_PORT` (8765), `SAVEHUB_HOST` (127.0.0.1) | web server |
| `SAVEHUB_DB` | SQLite path (default `data/savehub.db`) |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | OpenAI or any OpenAI-compatible API |
| `XAI_API_KEY` | xAI (base `https://api.x.ai/v1`) |
| `SAVEHUB_AI_MODEL` | model override (defaults `gpt-4o-mini` / `grok-3-mini`) — provider also selectable in Settings (auto/openai/xai/local) |
| `X_CLIENT_ID` | X OAuth client id (or set in Settings; optional client secret for confidential apps). Callback: `http://localhost:8765/api/x/callback` |
| `NOTION_TOKEN`, `NOTION_DATABASE_ID` | Notion sync (also in Settings; still must be toggled on) |
| `SAVEHUB_ENABLED_PLATFORMS` | comma list limiting which platforms MCP tools (and app) can read/write, e.g. `x,instagram,xiaohongshu` |
| `PORT` | deployment mode: bind `0.0.0.0:$PORT` (set by Render) |
| `SAVEHUB_MCP_TOKEN` | bearer token for `/mcp` (auto-generated to `data/.mcp_token` if unset) |
| `SAVEHUB_ALLOWED_HOSTS` | extra allowed Host headers for MCP (custom domains); `RENDER_EXTERNAL_HOSTNAME` is added automatically; `*` disables the check |
| `SAVEHUB_MCP_STATELESS` (1) | MCP stateless HTTP mode (robust across restarts); `0` = sessions |
| `SAVEHUB_PUBLIC_URL` | public base URL (for X OAuth callback) if not on Render |
| `SAVEHUB_DASHBOARD_PASSWORD`, `SAVEHUB_DASHBOARD_USER` (savehub) | optional HTTP Basic auth for the dashboard + REST API (combined server); unset = no login |
| `SAVEHUB_SEED` (1) | `0` = don't seed sample data on an empty DB |
| `SAVEHUB_MCP_PORT` (8766) | port for the standalone MCP HTTP server only |

Settings (including Notion token / X client secret) are stored in plain text in the local SQLite `settings` table — keep `data/` private.

## MCP client config (Claude Desktop / Cursor)
```json
{
  "mcpServers": {
    "savehub": {
      "command": "/workspace/savehub/.venv/bin/python",
      "args": ["-m", "savehub.mcp_server"],
      "cwd": "/workspace/savehub",
      "env": {
        "PYTHONPATH": "/workspace/savehub",
        "SAVEHUB_DB": "/workspace/savehub/data/savehub.db",
        "SAVEHUB_ENABLED_PLATFORMS": "x,threads,instagram,facebook,messenger,whatsapp,wechat,tiktok,douyin,xiaohongshu,web"
      }
    }
  }
}
```
HTTP variant (clients that support streamable HTTP):
```json
{ "mcpServers": { "savehub": { "url": "http://127.0.0.1:8765/mcp", "headers": { "Authorization": "Bearer yourtoken" } } } }
```
Tools: `search_items, list_items, get_item, add_item, tag_items, get_summary, list_platforms, import_file, search_contacts, sync_now, list_accounts, add_account, search_accounts, login_status, collect` (the last two drive the browser collector, see below).

## Tests
```bash
.venv/bin/python -m pytest -q tests/test_all.py      # API, search, sort, tagging, summary, every importer, accounts, drive-folder sync, X auth URL, bulk account paste (temp DB)
.venv/bin/python tests/accounts_ui_test.py           # Accounts UI end-to-end + screenshots (needs server on :8900)
.venv/bin/python tests/mcp_client_test.py            # every MCP tool over stdio (temp DB) + HTTP at $SAVEHUB_MCP_URL (default :8765/mcp)
.venv/bin/python tests/combined_server_test.py       # boots combined server on PORT=8900 with a fresh DB: /healthz, dashboard, REST, MCP auth/hosts/tools
.venv/bin/python tests/screenshots.py                # headless screenshots → screenshots/
.venv/bin/python -m pytest -q tests/collector_test.py   # browser collector: X parser on HTML fixtures, scroll/de-dupe, login detection, push to a temp SaveHub, unsave safety
```

## Sample data
30 items, 7 accounts, 6 contacts — all fictional and flagged `is_sample` (shown with a **SAMPLE** badge). “Clear sample data” in the banner/Settings deletes only those rows. Example import files live in `samples/`.

## Layout
`savehub/db.py` storage+FTS · `tagger.py` AI/local tagging · `importers.py` · `summary.py` · `sync.py` (Notion/Drive) · `xoauth.py` · `app.py` REST API · `mcp_server.py` · `server.py` (combined single-port) · `seed.py` · `static/` UI · `collector/` local browser collector (not used by the server)

## 🔐 Encrypted password vault (optional)
Each account can optionally hold its login password, **encrypted at rest**.
- **Crypto:** AES-256-GCM (`cryptography`), random 96-bit nonce per secret, account id bound as associated data. Key is derived from `SAVEHUB_VAULT_KEY` only (Fernet-style key → HKDF-SHA256; any other 16+ char passphrase → PBKDF2-HMAC-SHA256, 600k iterations; random per-DB salt). A key-check value detects a wrong/rotated key (refused, never garbage).
- **Storage:** table `account_secrets(account_id, cipher)` holds only `v1:<base64(nonce|ciphertext|tag)>`. The key is never stored, logged or returned.
- **Enabled only when** `SAVEHUB_VAULT_KEY` **and** `SAVEHUB_DASHBOARD_PASSWORD` are set. Otherwise the UI explains it is off and nothing is stored (no plaintext fallback, ever).
- **API (dashboard login required, JSON + same-origin only, `Cache-Control: no-store`):** `GET /api/vault/status`, `PUT /api/accounts/{id}/secret {"password"}`, `POST /api/accounts/{id}/secret/reveal`, `DELETE /api/accounts/{id}/secret`. Account lists only expose `has_password`.
- **Not exposed via MCP** (no tool accepts or returns passwords) and **never included in CSV export**.
- ⚠️ **Render free tier has no persistent disk**: the DB — including saved passwords — is wiped on every restart/redeploy/idle spin-down. Attach a persistent disk (paid) and set `SAVEHUB_PERSISTENT_DISK=1`. Losing `SAVEHUB_VAULT_KEY` makes saved passwords unrecoverable.


## 🕸️ Browser collector (local / self-hosted only)
`savehub.collector` opens the app in a real Chromium (Playwright) **as you**, using a saved login session, scrolls your
**saved / bookmarked** items, and pushes them into SaveHub. It can optionally un-save them on the app afterwards.

**Why local-only:** Render's free tier has no browser and no persistent disk, so the collector does not run there. Run it on
any machine with Chromium (this box, your laptop). By default it **pushes to a SaveHub instance over the REST API**, so it works
against the live Render site as well as a local server. `--sink sqlite --db path` writes straight into a local SQLite DB instead.
⚠️ On Render free, pushed items are lost when the instance restarts or redeploys (ephemeral DB). Export them, or point the collector at a SaveHub that keeps its data.

| Platform | Collector |
|---|---|
| X (Twitter) | ✅ `x.com/i/bookmarks`: author, @handle, text (emoji kept), permalink, post time, media flag/first image, repost/quote context; de-duped by tweet id; optional un-bookmark |
| Instagram, Threads, Facebook, TikTok, 抖音, 小红书, WeChat, WhatsApp, Messenger | ⏳ stubs (`savehub/collector/platforms/stubs.py`) that raise a clear *not implemented* error. Each has TODO notes on its saved-items URL and selectors. `python -m savehub.collector platforms` lists them |

**Install** (kept out of `requirements.txt` so the Render image stays slim):
```bash
.venv/bin/pip install -r requirements-collector.txt && .venv/bin/playwright install chromium
```
If the bundled Chromium is missing, it falls back to a system Chrome/Chromium (or `$SAVEHUB_CHROME`).

**1. Log in once** (opens a visible browser; needs a desktop/`DISPLAY`):
```bash
.venv/bin/python -m savehub.collector login x --account @main_handle   # log in by hand (2FA is fine); saved automatically once signed in
.venv/bin/python -m savehub.collector login x --account @alt_handle    # a second X account, logged in at the same time
```
**Multiple accounts per platform:** each login is stored per (platform, account) as `data/sessions/<platform>@<handle>.json`
(e.g. `x@main_handle.json`, `x@alt_handle.json`, `instagram@food.json`). Without `--account` the legacy single file
`data/sessions/<platform>.json` is used (account `default`, items not linked to an account row).
The session (cookies + localStorage, Playwright `storage_state`) is written to `data/sessions/` (mode 0600, git-ignored;
change the folder with `--sessions-dir` or `SAVEHUB_SESSIONS_DIR`). **No password is stored.** The file works like a logged-in
browser, so keep it private. Each run saves refreshed cookies back to it. `python -m savehub.collector sessions` lists saved sessions
(metadata only).

**2. Collect:**
```bash
export SAVEHUB_URL=https://<service>.onrender.com      # default http://127.0.0.1:8765
export SAVEHUB_DASHBOARD_PASSWORD=...                  # dashboard login (user: SAVEHUB_DASHBOARD_USER, default savehub)
.venv/bin/python -m savehub.collector collect x --no-push --max-items 20   # scrape only, print items
.venv/bin/python -m savehub.collector collect x --max-items 200 --account @myhandle
```
Options: `--max-items` (default 200, hard cap per run), `--scroll-delay` (default 1.8 s + jitter, polite scrolling),
`--headful`, `--account @handle` (collect from that logged-in account's session, tag items with it and link them to its SaveHub
account row, which is created with `connected_via=collector` if the dashboard has none) / `--account-id N` (link only), `--config run.json` (any `RunConfig`
field, e.g. `{"max_items": 100, "unsave": true}`; CLI flags win), `--log-json` (structured logs on stderr). Items are stored as
`platform=x`, `source=collector:x:<handle>` (`collector:x` for the default session), `account_id=<account row>`,
`external_id=<tweet id>`, post time, media flags and `source_account` in `raw`. Filter by account with `account_id`
(dashboard account filter, `GET /api/items?account_id=N`, MCP `list_items(account_id=N)`). An item saved by two of your
accounts is stored once (de-dupe on platform+URL) and stays attributed to the account that collected it first. Re-runs only add new items
(SaveHub de-dupes on platform+URL). The JSON result goes to stdout.

Exit codes: `0` ok · `1` error · **`2` need login** (no session or it expired; the output names the session file and the `login` command to run) · `3` platform not implemented · `4` some pushes failed.

**3. Un-save safety (OFF by default):**
- No flag: nothing on the app is touched.
- `--unsave`: **dry run**. It logs and lists each item it *would* un-bookmark and never opens or clicks them.
- `--unsave --unsave-confirm`: actually un-bookmarks. `--unsave-confirm` alone is refused, and `--no-push` with `--unsave` is refused.
- An item is un-saved only after SaveHub confirms it (POST, then a read-back of the same platform+URL). Failed pushes are never un-saved.
- At most `--max-unsave` (default 50) real un-saves per run, `unsave_delay` 2.5 s apart. Each one is verified (the bookmark toggle flips, or the Share menu no longer offers “Remove from Bookmarks”).

**4. From your AI client (MCP):**
- `login_status(platform, account=None)`: per-account session status (`saved` / `expired` / `missing` / `invalid`) and the linked
  account row. Read-only; it never logs in. If nothing is saved it returns the box command to run (`login <platform> --account @<handle>`).
- `collect(platform, account=None, max_items=None, unsave=False)`: runs the collector (as a subprocess using the SQLite sink, so
  items go into the MCP server's own DB) for that account, or for **every logged-in account** of the platform when `account` is
  omitted. `unsave` is off by default; only `unsave=true` un-saves on the real app (same confirmed-only rules, max 50 per run).
  It returns an error right away when there is no session (`need_login`), the collector is a stub (`not_implemented`), or no
  browser/Playwright is available (`browser_unavailable`, e.g. on Render). Runs are killed after `SAVEHUB_COLLECT_TIMEOUT`
  seconds (default 900) per account. The subprocess gets no password, token or key env vars.
- `list_platforms()` adds `collector` (supported, implemented, per-account sessions, logged_in_accounts) per platform and a
  top-level `collector_runtime` (is a browser available here).
No MCP tool takes or returns passwords, cookies or session contents.

**Fragility:** this scrapes the web UI. When X changes its markup, selectors break. They all live in `SEL` in
`savehub/collector/platforms/x.py`, and `tests/fixtures/x_bookmarks.html` mirrors the expected DOM. Automated use may hit
rate limits, captchas or account checks. Keep runs small and infrequent, and use your own account only, at your own risk under the app's terms.
