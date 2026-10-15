"""Sample data (all rows flagged is_sample=1; fictional people/handles; example URLs)."""
from datetime import datetime, timedelta, timezone
from . import db

ACCOUNTS = [
    ("x", "Js (main)", "js_sample", "https://x.com/js_sample", "js.sample@example.com", "", "Main X account", "personal,main", "active", "x_oauth2 (not connected)"),
    ("x", "Js Trading", "js_trades_sample", "https://x.com/js_trades_sample", "trade.sample@example.com", "", "Gold/markets notes", "trading", "active", "manual"),
    ("instagram", "Js Eats", "jseats.sample", "https://instagram.com/jseats.sample", "js.sample@example.com", "", "Food account", "food", "active", "export import"),
    ("xiaohongshu", "小叶 Sample", "xhs_sample_88", "https://www.xiaohongshu.com/user/profile/sample", "", "+60 12-000 0000", "小红书主号", "chinese,lifestyle", "active", "paste links"),
    ("tiktok", "js.clips", "js.clips.sample", "https://tiktok.com/@js.clips.sample", "", "", "", "video", "active", "export import"),
    ("whatsapp", "Js phone", "+60120000000", "", "", "+60 12-000 0000", "Personal WhatsApp", "personal", "active", "chat export"),
    ("wechat", "Js 微信", "js_wx_sample", "", "", "+60 12-000 0000", "", "chinese", "dormant", "paste links"),
]
# platform, account_index, title, content, author, handle, url, days_ago
ITEMS = [
    ("x", 0, "Thread: 12 Python tools I use every day", "uv, ruff, httpx, rich, typer, polars… a quick thread on the Python tooling that replaced half my stack.", "Dev Sample", "devsample", "https://x.com/devsample/status/1000000000000000001", 0),
    ("x", 0, "Grok vs other LLMs for coding agents", "Benchmarks of AI coding agents on real repos; prompt caching and agent loops matter more than model size.", "AI Notes Sample", "ainotes_s", "https://x.com/ainotes_s/status/1000000000000000002", 1),
    ("x", 1, "XAUUSD weekly levels", "Gold (XAUUSD) key support 2380, resistance 2450. Watching CPI on Wednesday. Not financial advice.", "Gold Desk Sample", "golddesk_s", "https://x.com/golddesk_s/status/1000000000000000003", 2),
    ("x", 1, "Dividend ETF comparison for Malaysians", "Comparing Bursa-listed and US-listed dividend ETFs, withholding tax and fees for Malaysia investors.", "MY Money Sample", "mymoney_s", "https://x.com/mymoney_s/status/1000000000000000004", 9),
    ("threads", None, "Minimal Notion habit tracker template", "A simple Notion template for habits + weekly review. Productivity without the overwhelm.", "Notion Sample", "notion.sample", "https://www.threads.net/@notion.sample/post/SAMPLE1", 1),
    ("threads", None, "Designers: stop using pure black", "Typography and UI design tip — use #111 instead of #000 for dark mode text and backgrounds.", "UI Sample", "ui.sample", "https://www.threads.net/@ui.sample/post/SAMPLE2", 5),
    ("instagram", 2, "Best nasi lemak in Kuala Lumpur (2026 list)", "10 nasi lemak spots in KL ranked — sambal, rice, ayam goreng. Food guide for Malaysia.", "KL Foodie Sample", "klfoodie.sample", "https://www.instagram.com/p/SAMPLE_IG1/", 0),
    ("instagram", 2, "Penang cafe hopping itinerary", "One-day Penang cafe itinerary: George Town coffee, street art, and dessert stops. Travel reel.", "Penang Sample", "penang.sample", "https://www.instagram.com/p/SAMPLE_IG2/", 3),
    ("instagram", 2, "15-minute home workout, no equipment", "Fitness reel: bodyweight HIIT workout you can do at home.", "Fit Sample", "fit.sample", "https://www.instagram.com/reel/SAMPLE_IG3/", 6),
    ("facebook", None, "IKEA small-apartment storage hacks", "Home decor + DIY storage ideas for small condos, with IKEA furniture links.", "Home Sample Group", None, "https://www.facebook.com/groups/sample/posts/1001", 2),
    ("facebook", None, "Lazada 10.10 tech deals roundup", "Shopping: discounts on earbuds, SSDs and monitors this 10.10 sale.", "Deals MY Sample", None, "https://www.facebook.com/dealsmysample/posts/1002", 6),
    ("messenger", None, "Link from friend: Japan rail pass guide", "Travel guide for JR Pass 2026 prices and itinerary for Tokyo-Kyoto-Osaka.", "Friend Sample", None, "https://example.com/sample/japan-rail-pass-guide", 4),
    ("whatsapp", 5, "Family group: claypot chicken rice recipe", "Recipe shared in family chat — claypot chicken rice, step by step.", "Mum (sample)", None, "https://example.com/sample/claypot-chicken-rice", 1),
    ("whatsapp", 5, "Job posting: Senior frontend developer (KL, hybrid)", "Career: hiring React/TypeScript developer, KL hybrid, salary range listed.", "Ex-colleague (sample)", None, "https://example.com/sample/jobs/frontend-kl", 8),
    ("wechat", 6, "公众号：2026年个人理财入门指南", "理财 投资 入门：基金、股票、黄金配置建议，适合新手。", "理财笔记 Sample", None, "https://mp.weixin.qq.com/s/SAMPLE_WX1", 2),
    ("wechat", 6, "大模型 Agent 开发实践", "人工智能 大模型 Agent 编程实践，工具调用与 MCP 协议介绍。", "AI前线 Sample", None, "https://mp.weixin.qq.com/s/SAMPLE_WX2", 10),
    ("tiktok", 4, "Easy Korean skincare routine", "Skincare: 5-step routine, sunscreen tips, beauty for humid weather.", "Glow Sample", "glow.sample", "https://www.tiktok.com/@glow.sample/video/7000000000000000001", 0),
    ("tiktok", 4, "Funny cat meme compilation", "Humor: cats vs cucumbers, meme compilation lol.", "Cat Memes Sample", "catmemes.sample", "https://www.tiktok.com/@catmemes.sample/video/7000000000000000002", 3),
    ("tiktok", 4, "How I budget my salary (50/30/20)", "Finance tip: budget with 50/30/20, save money and invest.", "Money Sample", "money.sample", "https://www.tiktok.com/@money.sample/video/7000000000000000003", 12),
    ("douyin", None, "抖音：10分钟快手晚餐食谱", "美食 食谱：番茄炒蛋、蒜蓉青菜，10分钟搞定晚餐。", "厨房 Sample", None, "https://v.douyin.com/SAMPLE1/", 1),
    ("douyin", None, "抖音：跑步新手入门", "健身 跑步 入门：配速、呼吸、跑鞋选择。", "跑者 Sample", None, "https://v.douyin.com/SAMPLE2/", 7),
    ("xiaohongshu", 3, "吉隆坡咖啡店探店合集☕", "吉隆坡 咖啡 探店：Bangsar、TTDI 十家宝藏咖啡店推荐。马来西亚 美食。", "小红薯 Sample", None, "http://xhslink.com/SAMPLE1", 0),
    ("xiaohongshu", 3, "秋冬通勤穿搭｜简约风", "穿搭 搭配：秋冬通勤简约风，基础款怎么搭。", "穿搭 Sample", None, "http://xhslink.com/SAMPLE2", 2),
    ("xiaohongshu", 3, "槟城三天两夜旅行攻略", "旅行 攻略：槟城 酒店、美食、景点 路线。", "旅行 Sample", None, "http://xhslink.com/SAMPLE3", 4),
    ("xiaohongshu", 3, "Notion 学习计划模板｜效率", "效率 学习：Notion 模板 做读书计划和习惯打卡。", "效率 Sample", None, "http://xhslink.com/SAMPLE4", 15),
    ("web", None, "Model Context Protocol — official docs", "MCP spec and SDKs: build servers exposing tools to AI clients like Claude and Cursor.", "modelcontextprotocol.io", None, "https://modelcontextprotocol.io/", 1),
    ("web", None, "SQLite FTS5 documentation", "Full-text search extension for SQLite: tokenizers, bm25 ranking, trigram.", "sqlite.org", None, "https://www.sqlite.org/fts5.html", 5),
    ("web", None, "Figma: auto layout masterclass", "Design tutorial covering auto layout, variables and components in Figma.", "Design Sample", None, "https://example.com/sample/figma-auto-layout", 11),
    ("x", 0, "Concert announcement: KL Arena tour dates", "Music: concert tour dates at Bukit Jalil, tickets on sale Friday.", "Music Sample", "music_s", "https://x.com/music_s/status/1000000000000000005", 13),
    ("web", None, "Reading list: 10 books on habits & focus", "Book list: Atomic Habits, Deep Work, and more on productivity and learning.", "Books Sample", None, "https://example.com/sample/reading-list", 20),
]
CONTACTS = [
    ("Aisyah Rahman (sample)", ["+60 12-111 1111"], ["aisyah@example.com"], "Sample Sdn Bhd", "whatsapp", 5),
    ("陈小明 Chen Xiaoming (sample)", ["+60 16-222 2222"], [], "", "wechat", 6),
    ("Kumar Raj (sample)", ["+60 17-333 3333"], ["kumar@example.com"], "Sample Tech", "whatsapp", 5),
    ("Mei Ling (sample)", [], ["meiling@example.com"], "", "instagram", 2),
    ("Daniel Tan (sample)", ["+60 19-444 4444"], ["daniel@example.com"], "Sample Bank", "x", 0),
    ("林美华 Lin Meihua (sample)", ["+60 11-555 5555"], [], "", "xiaohongshu", 3),
]

def seed(force=False):
    c = db.conn()
    if not force and (c.execute("SELECT count(*) FROM settings WHERE key='seeded'").fetchone()[0]):
        return {"skipped": True}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    acc_ids = []
    for p, name, h, url, em, ph, notes, tags, st, via in ACCOUNTS:
        aid, _ = db.add_account({"platform": p, "display_name": name, "handle": h, "profile_url": url, "linked_email": em, "linked_phone": ph, "notes": notes,
                                 "tags": tags, "status": st, "connected_via": via, "is_sample": 1, "created_at": (now - timedelta(days=200)).isoformat()})
        acc_ids.append(aid)
    n = 0
    for p, ai, title, content, author, handle, url, days in ITEMS:
        _, cr = db.add_item({"platform": p, "account_id": acc_ids[ai] if ai is not None else None, "title": title, "content": content, "author": author,
                             "author_handle": handle, "url": url, "saved_at": (now - timedelta(days=days, hours=n % 9)).isoformat(), "source": "sample", "is_sample": 1})
        n += cr
    for name, ph, em, org, p, ai in CONTACTS:
        db.add_contact({"name": name, "phones": ph, "emails": em, "org": org, "platform": p, "account_id": acc_ids[ai], "is_sample": 1, "source": "sample"})
    from . import tagger
    tagger._DF = None  # recompute TF-IDF stats on the full corpus, then retag samples
    if not tagger.llm_config():
        ids = [r[0] for r in c.execute("SELECT id FROM items WHERE is_sample=1")]
        tagger.retag(ids)
    db.set_setting("seeded", True)
    return {"items": n, "accounts": len(acc_ids), "contacts": len(CONTACTS)}
