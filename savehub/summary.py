"""Digest of recent saves."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from . import db, tagger

def get_summary(days=7, use_ai=True, enabled_only=None):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
    items = db.list_items(since=since, limit=500, enabled_only=enabled_only)["items"]
    by_p = Counter(i["platform"] for i in items)
    tags = Counter(t for i in items for t in (i["tags"] or []))
    authors = Counter(i["author"] for i in items if i.get("author"))
    by_day = Counter((i["saved_at"] or "")[:10] for i in items)
    by_type = Counter(i.get("type") or "post" for i in items)
    digest, by = None, "local"
    if items and use_ai and tagger.llm_config():
        try:
            lines = "\n".join(f"- [{i['platform']}] {i['title']} — {i.get('summary') or ''}" for i in items[:80])
            digest = tagger.chat(f"Here are my saved items from the last {days} days:\n{lines}\n\nWrite a friendly 4-6 sentence digest: main themes, notable items, and 1-2 suggested follow-ups. If items are in Chinese, you may mention them in Chinese.",
                                 system="You write personal weekly digests of saved content.", max_tokens=500)
            by = tagger.llm_config()["name"]
        except Exception as e:
            print("AI digest failed:", e)
    if not digest:
        if not items: digest = f"No items saved in the last {days} days."
        else:
            top_t = ", ".join(t for t, _ in tags.most_common(5)) or "no tags"
            top_p = ", ".join(f"{db.PLATFORMS[p]['name']} ({n})" for p, n in by_p.most_common(3))
            highlights = "; ".join(i["title"][:60] for i in items[:3])
            digest = (f"You saved {len(items)} items in the last {days} days, mostly from {top_p}. "
                      f"Main themes: {top_t}. Most recent: {highlights}. "
                      f"(Template digest — set OPENAI_API_KEY or XAI_API_KEY for an AI-written summary.)")
    return {"days": days, "since": since, "total": len(items), "by_platform": dict(by_p.most_common()),
            "top_tags": tags.most_common(15), "top_authors": authors.most_common(8), "by_day": dict(sorted(by_day.items())),
            "by_type": dict(by_type.most_common()),
            "digest": digest, "digest_by": by, "recent": items[:12]}
