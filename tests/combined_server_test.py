"""Start the combined single-port server (PORT=8900, temp DB, simulated Render host) and verify
dashboard + REST + MCP coexist on one port."""
import asyncio, json, os, subprocess, sys, tempfile, time, pathlib
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = pathlib.Path(__file__).resolve().parent.parent
PORT, TOKEN, RHOST = 8900, "test-token-123", "savehub-test.onrender.com"
BASE = f"http://127.0.0.1:{PORT}"
env = {**os.environ, "PORT": str(PORT), "SAVEHUB_MCP_TOKEN": TOKEN, "RENDER_EXTERNAL_HOSTNAME": RHOST,
       "SAVEHUB_DB": os.path.join(tempfile.mkdtemp(), "fresh.db")}  # fresh, non-existent DB => tests auto-create + seed
proc = subprocess.Popen([sys.executable, "-m", "savehub.server"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
results = []
def check(name, ok, detail=""):
    results.append(ok); print(("PASS" if ok else "FAIL"), name, detail)

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
def parse(r):
    t = r.text
    if "text/event-stream" in r.headers.get("content-type", ""):
        t = next(l[5:].strip() for l in t.splitlines() if l.startswith("data:"))
    return json.loads(t)

try:
    for _ in range(60):
        try:
            if httpx.get(BASE + "/healthz", timeout=1).status_code == 200: break
        except Exception: time.sleep(0.25)
    r = httpx.get(BASE + "/healthz"); check("GET /healthz", r.status_code == 200, f"{r.status_code} {r.text}")
    r = httpx.get(BASE + "/"); check("GET / dashboard html", r.status_code == 200 and "<title>SaveHub" in r.text, str(r.status_code))
    r = httpx.get(BASE + "/static/app.js"); check("GET /static/app.js", r.status_code == 200, str(r.status_code))
    j = httpx.get(BASE + "/api/health").json(); check("fresh DB auto-created + seeded", j["stats"]["items"] == 30 and j["stats"]["accounts"] == 7, json.dumps(j["stats"])[:80])
    r = httpx.get(BASE + "/api/items", params={"q": "咖啡店"}); check("REST search on same port", r.json()["total"] == 1)

    r = httpx.post(BASE + "/mcp", json=INIT, headers=HDR); check("MCP without token -> 401", r.status_code == 401, str(r.status_code))
    r = httpx.post(BASE + "/mcp", json=INIT, headers={**HDR, "Authorization": "Bearer wrong"}); check("MCP wrong token -> 401", r.status_code == 401, str(r.status_code))
    auth = {**HDR, "Authorization": f"Bearer {TOKEN}"}
    for host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}", RHOST):
        r = httpx.post(BASE + "/mcp", json=INIT, headers={**auth, "Host": host})
        ok = r.status_code == 200 and parse(r)["result"]["serverInfo"]["name"] == "savehub"
        check(f"MCP initialize (Host: {host})", ok, f"{r.status_code} protocol={parse(r)['result'].get('protocolVersion') if r.status_code == 200 else r.text[:80]}")
        sid = r.headers.get("mcp-session-id")
        h2 = {**auth, "Host": host, **({"mcp-session-id": sid} if sid else {}), "mcp-protocol-version": "2025-03-26"}
        httpx.post(BASE + "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h2)
        r = httpx.post(BASE + "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=h2)
        names = sorted(t["name"] for t in parse(r)["result"]["tools"]) if r.status_code == 200 else []
        check(f"MCP tools/list (Host: {host})", len(names) == 13, f"{len(names)} tools")
    r = httpx.post(BASE + "/mcp", json=INIT, headers={**auth, "Host": "evil.example.com"}); check("MCP foreign Host rejected (DNS-rebinding protection)", r.status_code == 421, str(r.status_code))
    r = httpx.post(BASE + "/mcp", json=INIT, headers={**auth, "Origin": "https://evil.example.com"}); check("MCP foreign Origin rejected", r.status_code == 403, str(r.status_code))

    async def sdk():
        async with streamablehttp_client(BASE + "/mcp", headers={"Authorization": f"Bearer {TOKEN}"}) as (rd, wr, _):
            async with ClientSession(rd, wr) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
                res = json.loads((await s.call_tool("search_items", {"query": "nasi lemak"})).content[0].text)
                return len(tools), res["total"]
    n, total = asyncio.run(sdk()); check("MCP SDK client: initialize+list_tools+search_items", n == 13 and total == 1, f"{n} tools, search total={total}")
    r = httpx.get(BASE + "/healthz"); check("dashboard still healthy after MCP traffic", r.status_code == 200)
    # dashboard basic auth (second server instance with SAVEHUB_DASHBOARD_PASSWORD)
    st = httpx.get(BASE + "/api/vault/status").json()
    check("vault disabled when no dashboard login (server 1)", not st["enabled"], st["reason"][:70])
    from cryptography.fernet import Fernet
    VKEY = Fernet.generate_key().decode(); SECRET = "Combined-Test-Secret-密码-9!"
    p2 = subprocess.Popen([sys.executable, "-m", "savehub.server"], cwd=ROOT, env={**env, "PORT": "8901", "SAVEHUB_DASHBOARD_PASSWORD": "pw123", "SAVEHUB_VAULT_KEY": VKEY, "SAVEHUB_DB": os.path.join(tempfile.mkdtemp(), "vault.db")}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        B2 = "http://127.0.0.1:8901"
        for _ in range(60):
            try:
                if httpx.get(B2 + "/healthz", timeout=1).status_code == 200: break
            except Exception: time.sleep(0.25)
        check("auth mode: /healthz open", httpx.get(B2 + "/healthz").status_code == 200)
        check("auth mode: dashboard without login -> 401", httpx.get(B2 + "/").status_code == 401)
        check("auth mode: dashboard with login -> 200", httpx.get(B2 + "/", auth=("savehub", "pw123")).status_code == 200)
        r = httpx.post(B2 + "/mcp", json=INIT, headers={**HDR, "Authorization": f"Bearer {TOKEN}"}); check("auth mode: /mcp still uses bearer token", r.status_code == 200, str(r.status_code))
        A2 = ("savehub", "pw123")
        st = httpx.get(B2 + "/api/vault/status", auth=A2).json(); check("vault enabled with key + login", st["enabled"] and VKEY not in json.dumps(st), st.get("reason", ""))
        aid = httpx.post(B2 + "/api/accounts", json={"platform": "x", "handle": "vault_combined"}, auth=A2).json()["id"]
        r = httpx.put(f"{B2}/api/accounts/{aid}/secret", json={"password": SECRET}, auth=A2); check("vault set password", r.status_code == 200, str(r.status_code))
        check("vault reveal without login -> 401", httpx.post(f"{B2}/api/accounts/{aid}/secret/reveal", json={}).status_code == 401)
        r = httpx.post(f"{B2}/api/accounts/{aid}/secret/reveal", json={}, auth=A2); check("vault reveal with login -> plaintext", r.status_code == 200 and r.json()["password"] == SECRET)
        async def mcp_leak():
            async with streamablehttp_client(B2 + "/mcp", headers={"Authorization": f"Bearer {TOKEN}"}) as (rd, wr, _):
                async with ClientSession(rd, wr) as s:
                    await s.initialize()
                    outs = [ (await s.call_tool(n, a)).content[0].text for n, a in (("list_accounts", {}), ("search_accounts", {"query": "vault_combined"}), ("list_accounts", {"platform": "x"})) ]
                    schemas = json.dumps([t.inputSchema for t in (await s.list_tools()).tools])
                    return outs, schemas
        outs, schemas = asyncio.run(mcp_leak())
        check("MCP over HTTP never returns the secret/ciphertext", all(SECRET not in o and "v1:" not in o and "has_password" not in o for o in outs) and "vault_combined" in outs[1])
        check("MCP tool schemas accept no password", "password" not in schemas.lower())
    finally:
        p2.terminate()
finally:
    proc.terminate()
    try: out = proc.communicate(timeout=10)[0]
    except Exception: proc.kill(); out = ""
print("--- server log (head) ---\n" + "\n".join(out.splitlines()[:6]))
print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
