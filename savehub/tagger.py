"""Auto-tagging: OpenAI-compatible LLM if configured, else local keyword + TF-IDF tagger."""
import json, math, os, re
from collections import Counter
import httpx

TOPICS = {
    "food": ["food","recipe","restaurant","cafe","coffee","nasi","laksa","eat","dinner","brunch","美食","咖啡","餐厅","好吃","探店","食谱","火锅","奶茶"],
    "travel": ["travel","trip","flight","hotel","itinerary","beach","japan","bali","penang","langkawi","旅行","旅游","攻略","酒店","机票","景点"],
    "tech": ["tech","software","app","code","coding","python","javascript","api","developer","github","programming","linux","技术","编程","程序"],
    "ai": ["ai","llm","gpt","grok","claude","machine learning","neural","prompt","agent","人工智能","模型","大模型"],
    "finance": ["finance","invest","stock","crypto","bitcoin","etf","dividend","budget","money","gold","xauusd","trading","理财","投资","股票","基金","黄金"],
    "fitness": ["fitness","workout","gym","run","running","yoga","protein","health","健身","跑步","减脂","瑜伽"],
    "fashion": ["fashion","outfit","style","ootd","wear","dress","sneaker","穿搭","衣服","搭配"],
    "beauty": ["skincare","makeup","beauty","serum","sunscreen","护肤","化妆","美妆","防晒"],
    "productivity": ["productivity","notion","habit","workflow","todo","focus","template","效率","习惯","时间管理"],
    "design": ["design","ui","ux","figma","typography","logo","设计","配色"],
    "career": ["career","job","interview","resume","hiring","salary","求职","面试","简历","职场"],
    "music": ["music","song","playlist","album","concert","spotify","音乐","歌"],
    "home": ["home","diy","interior","decor","furniture","ikea","家居","装修","收纳"],
    "parenting": ["parenting","kids","baby","toddler","育儿","宝宝"],
    "learning": ["learn","course","tutorial","guide","book","reading","study","教程","学习","读书","书单"],
    "news": ["news","breaking","election","policy","government","新闻"],
    "humor": ["funny","meme","lol","joke","搞笑","段子"],
    "shopping": ["deal","sale","discount","shopee","lazada","buy","review","好物","推荐","种草","优惠"],
    "malaysia": ["malaysia","kuala lumpur","kl","penang","johor","selangor","马来西亚","吉隆坡","槟城"],
}
STOP = set("""a an the and or of to in on for with at by from is are was were be this that it its as you your i we our they my me
http https www com amp just like get got new how what why when who will can not but so if about into out up more most very rt via""".split())

def _text(item):
    return " ".join(str(item.get(k) or "") for k in ("title", "content", "author", "url"))

def _summary_local(item):
    t = (item.get("content") or item.get("title") or item.get("url") or "").strip()
    t = re.sub(r"https?://\S+", "", t).strip() or (item.get("title") or item.get("url") or "")
    m = re.split(r"(?<=[.!?。！？])\s*", t, maxsplit=1)
    s = m[0] if m and len(m[0]) > 15 else t
    return (s[:157] + "…") if len(s) > 160 else s

_DF = None
def _doc_freq():
    """Corpus document-frequency from the library for TF-IDF (cached per process)."""
    global _DF
    if _DF is None:
        try:
            from . import db
            docs = [r[0] or "" for r in db.conn().execute("SELECT coalesce(title,'')||' '||coalesce(content,'') FROM items LIMIT 5000")]
        except Exception:
            docs = []
        df = Counter()
        for d in docs: df.update(set(_tokens(d)))
        _DF = (df, max(len(docs), 1))
    return _DF

def _tokens(s):
    s = re.sub(r"https?://\S+", " ", s.lower())
    toks = [w for w in re.findall(r"[a-z][a-z0-9+#\-]{2,}", s) if w not in STOP]
    # CJK: use bigrams
    for run in re.findall(r"[\u4e00-\u9fff]+", s):
        toks += [run[i:i+2] for i in range(len(run) - 1)] if len(run) > 1 else [run]
    return toks

def local_tags(item, k=5):
    text = _text(item).lower()
    tags = []
    for topic, kws in TOPICS.items():
        if any((re.search(r"\b" + re.escape(kw) + r"\b", text) if kw.isascii() else kw in text) for kw in kws):
            tags.append(topic)
    df, n = _doc_freq()
    tf = Counter(_tokens(_text({"title": item.get("title"), "content": item.get("content")})))
    scored = sorted(((c * math.log((n + 1) / (df.get(w, 0) + 1)) + 0.01 * len(w), w) for w, c in tf.items()), reverse=True)
    for _, w in scored:
        if len(tags) >= k: break
        if w not in tags and not w.isdigit(): tags.append(w)
    return tags[:k]

# ---------------- LLM ----------------
def llm_config():
    from . import db
    s = db.get_settings()["ai"]
    prov = s.get("provider", "auto")
    if prov == "local": return None
    oa, xa = os.environ.get("OPENAI_API_KEY"), os.environ.get("XAI_API_KEY")
    if prov in ("auto", "openai") and oa:
        return {"key": oa, "base": s.get("base_url") or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                "model": s.get("model") or os.environ.get("SAVEHUB_AI_MODEL", "gpt-4o-mini"), "name": "openai"}
    if prov in ("auto", "xai") and xa:
        return {"key": xa, "base": s.get("base_url") or "https://api.x.ai/v1",
                "model": s.get("model") or os.environ.get("SAVEHUB_AI_MODEL", "grok-3-mini"), "name": "xai"}
    return None

def chat(prompt, system="You are a concise assistant.", json_mode=False, max_tokens=400):
    cfg = llm_config()
    if not cfg: return None
    body = {"model": cfg["model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0.2}
    if json_mode: body["response_format"] = {"type": "json_object"}
    r = httpx.post(cfg["base"].rstrip("/") + "/chat/completions", json=body, headers={"Authorization": f"Bearer {cfg['key']}"}, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def tag_item(item):
    if llm_config():
        try:
            out = chat("Item saved from " + str(item.get("platform")) + ":\n" + _text(item)[:3000] +
                       '\n\nReturn JSON {"tags": [3-6 short lowercase topic tags, English], "summary": "one sentence, <=25 words, same language as the item"}',
                       system="You tag and summarize saved social-media items.", json_mode=True)
            j = json.loads(out)
            return {"tags": [str(t).lower()[:30] for t in j.get("tags", [])][:6], "summary": str(j.get("summary", ""))[:300], "tagger": llm_config()["name"]}
        except Exception as e:  # fall back silently but record it
            print("LLM tagging failed, using local:", e)
    return {"tags": local_tags(item), "summary": _summary_local(item), "tagger": "local"}

def retag(ids=None, force=False):
    from . import db
    c = db.conn()
    q = "SELECT * FROM items" + (f" WHERE id IN ({','.join('?'*len(ids))})" if ids else ("" if force else " WHERE tags='[]' OR tags IS NULL"))  # nosec B608 - only '?' placeholders are interpolated
    n = 0
    for r in c.execute(q, ids or []).fetchall():
        t = tag_item(dict(r)); db.update_item(r["id"], t); n += 1
    return n

def provider_status():
    cfg = llm_config()
    return {"active": cfg["name"] if cfg else "local", "model": cfg["model"] if cfg else "keyword+tfidf",
            "openai_key": bool(os.environ.get("OPENAI_API_KEY")), "xai_key": bool(os.environ.get("XAI_API_KEY"))}
