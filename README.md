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
| Accounts: many accounts per platform; add/edit/delete; search/sort/filter; CSV import/export; items & contacts link via `account_id` | ✅ tested. **Passwords are never stored** (API rejects password/secret fields; CSV password columns are dropped) |
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

None of the non-X apps offer a public API for personal saved items; SaveHub does not scrape them.

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
Tools: `search_items, list_items, get_item, add_item, tag_items, get_summary, list_platforms, import_file, search_contacts, sync_now, list_accounts, add_account, search_accounts`.

## Tests
```bash
.venv/bin/python -m pytest -q tests/test_all.py      # API, search, sort, tagging, summary, every importer, accounts, drive-folder sync, X auth URL (temp DB)
.venv/bin/python tests/mcp_client_test.py            # every MCP tool over stdio (temp DB) + HTTP at $SAVEHUB_MCP_URL (default :8765/mcp)
.venv/bin/python tests/combined_server_test.py       # boots combined server on PORT=8900 with a fresh DB: /healthz, dashboard, REST, MCP auth/hosts/tools
.venv/bin/python tests/screenshots.py                # headless screenshots → screenshots/
```

## Sample data
30 items, 7 accounts, 6 contacts — all fictional and flagged `is_sample` (shown with a **SAMPLE** badge). “Clear sample data” in the banner/Settings deletes only those rows. Example import files live in `samples/`.

## Layout
`savehub/db.py` storage+FTS · `tagger.py` AI/local tagging · `importers.py` · `summary.py` · `sync.py` (Notion/Drive) · `xoauth.py` · `app.py` REST API · `mcp_server.py` · `server.py` (combined single-port) · `seed.py` · `static/` UI
