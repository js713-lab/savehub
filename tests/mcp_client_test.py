"""Exercise every MCP tool over stdio (isolated temp DB) and list tools over streamable HTTP (live server on :8766)."""
import asyncio, json, os, sys, tempfile, pathlib
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

ROOT = pathlib.Path(__file__).resolve().parent.parent
def out(r):
    t = r.content[0].text if r.content else ""
    try: return json.loads(t)
    except Exception: return t

async def stdio_test():
    env = {**os.environ, "SAVEHUB_DB": os.path.join(tempfile.mkdtemp(), "mcp.db"), "SAVEHUB_SESSIONS_DIR": tempfile.mkdtemp(),
           "SAVEHUB_ENABLED_PLATFORMS": "x,instagram,xiaohongshu,douyin,tiktok,wechat,whatsapp,icloud,web"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "savehub.mcp_server"], env=env, cwd=str(ROOT))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools); print("STDIO tools:", tools)
            calls = [
                ("list_platforms", {}),
                ("search_items", {"query": "咖啡店"}),
                ("search_items", {"query": "python"}),
                ("list_items", {"sort": "title", "order": "asc", "limit": 3}),
                ("list_items", {"platforms": ["facebook"]}),  # disabled -> filtered out
                ("get_item", {"item_id": 1}),
                ("add_item", {"url": "http://xhslink.com/MCPTEST", "title": "小红书 MCP 测试：马来西亚旅行攻略"}),
                ("add_item", {"url": "https://facebook.com/x/1", "title": "should be refused"}),
                ("tag_items", {"item_ids": [1, 2]}),
                ("get_summary", {"days": 7}),
                ("import_file", {"path": str(ROOT / "samples/tiktok_user_data.json"), "kind": "tiktok_json"}),
                ("import_file", {"text": "https://v.douyin.com/MCPPASTE/ 抖音测试", "kind": "paste"}),
                ("search_contacts", {"query": "Kumar"}),
                ("sync_now", {}),
                ("list_accounts", {"platform": "x"}),
                ("add_account", {"platform": "tiktok", "display_name": "MCP test", "handle": "mcp.test"}),
                ("search_accounts", {"query": "example.com"}),
                ("login_status", {"platform": "x"}),
                ("login_status", {"platform": "x", "account": "@me"}),
                ("collect", {"platform": "x"}),                       # no session -> need_login, returns immediately
                ("collect", {"platform": "instagram", "account": "@me"}),   # implemented, no session -> need_login
                ("collect", {"platform": "wechat"}),                         # stub collector -> not_implemented
                ("login_status", {"platform": "icloud", "account": "@me"}),  # on-box terminal login, no session yet
                ("collect", {"platform": "icloud", "account": "@me"}),       # API collector, no session -> need_login
            ]
            fails = []
            for name, args in calls:
                res = out(await s.call_tool(name, args))
                if name == "list_platforms":
                    brief = {p["id"]: [p["enabled"], p["collector"]["implemented"], p["collector"]["logged_in_accounts"]] for p in res["platforms"]}
                    if not all("collector" in p for p in res["platforms"]) or "collector_runtime" not in res: fails.append(name)
                elif name == "login_status":
                    brief = res
                    if res.get("logged_in") is not False or "fix" not in res: fails.append(name)
                    if args.get("platform") == "icloud" and ("savehub.collector login icloud" not in res["fix"] or res.get("login") != "terminal"): fails.append("login_status icloud")
                elif name == "collect":
                    brief = res
                    if res.get("status") not in ("need_login", "not_implemented"): fails.append(name)
                elif isinstance(res, dict) and "items" in res: brief = {"total": res["total"], "titles": [i["title"][:40] for i in res["items"][:3]], "platforms": sorted({i["platform"] for i in res["items"]})}
                elif isinstance(res, dict) and name == "get_summary": brief = {"total": res["total"], "by_platform": res["by_platform"], "digest": res["digest"][:120]}
                elif isinstance(res, dict) and "accounts" in res: brief = {"total": res["total"], "handles": [a["handle"] for a in res["accounts"]]}
                elif isinstance(res, dict) and "contacts" in res: brief = {"total": res["total"], "names": [c["name"] for c in res["contacts"]]}
                elif isinstance(res, dict) and name == "get_item": brief = {k: res.get(k) for k in ("id", "title", "tags")}
                else: brief = res
                print(f"  {name}({json.dumps(args, ensure_ascii=False)[:70]}) -> {json.dumps(brief, ensure_ascii=False)[:300]}")
            assert {"login_status", "collect"} <= set(tools) and len(tools) == 17, tools
            assert not fails, fails
            print("STDIO: all tool calls OK")

async def http_test():
    try:
        async with streamablehttp_client(os.environ.get("SAVEHUB_MCP_URL", "http://127.0.0.1:8765/mcp"), headers={"Authorization": "Bearer " + os.environ.get("SAVEHUB_MCP_TOKEN", "devtoken")}) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                print("HTTP tools:", len((await s.list_tools()).tools))
                res = out(await s.call_tool("search_items", {"query": "nasi lemak"}))
                print("  HTTP search_items('nasi lemak') ->", res["total"], [i["title"] for i in res["items"]])
    except Exception as e:
        print("HTTP test failed:", repr(e))

asyncio.run(stdio_test()); asyncio.run(http_test())
