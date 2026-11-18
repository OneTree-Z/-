"""印尼电力招标 / 政策法规 每日采集脚本
流程：Google News RSS 检索 -> 去重 -> Claude 筛选分类摘要 -> 写入 docs/data.json
"""
import os, json, re, hashlib, urllib.parse
from datetime import datetime, timezone
import feedparser

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data.json")
MODEL = os.getenv("MODEL", "claude-haiku-5-5")   # 便宜够用；想更准可改 claude-sonnet-5-5
MAX_KEEP = 800
BATCH = 10

# ===== 检索词（印尼语 + 英语），可自行增删 =====
QUERIES = [
    "lelang pengadaan PLN listrik",
    "tender PLN pembangkit transmisi gardu induk",
    "pengadaan PLN PLTS PLTB EBT tender",
    "peraturan menteri ESDM ketenagalistrikan",
    "RUPTL PLN kebijakan listrik",
    "regulasi tarif listrik TKDN PLTS",
    "Indonesia power tender PLN",
    "Indonesia electricity regulation policy ESDM",
]
# ===== 官方来源（RSS 地址请自行核实后填入，格式 ("名称", "RSS链接")）=====
EXTRA_FEEDS = []

PROMPT = """你是电力行业海外市场分析助手，关注印尼电力领域的【招标采购】和【政策法规】。
下面是若干条新闻/公告（JSON）。请逐条判断，只输出一个 JSON 数组，不要任何其他文字。
每个元素格式：
{"id":原id,"relevant":true/false,
 "category":"招标"|"政策法规"|"项目动态"|"其他",
 "title_zh":"中文标题","summary_zh":"1-2句中文摘要","summary_en":"1-2 sentence English summary",
 "owner":"业主/发布机构，未知则空字符串","deadline":"截止日期，原文没有则空字符串","amount":"容量或金额，原文没有则空字符串"}
规则：与印尼电力（发电、输配电、储能、新能源、电价、电力政策法规、PLN/ESDM采购）无关的 relevant=false。
只依据所给标题和摘要，不得编造；原文没有的信息一律留空。
"""

def clean(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t or "")).strip()

def load():
    try:
        with open(DATA, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def fetch():
    urls = [("Google News", "https://news.google.com/rss/search?q=" +
             urllib.parse.quote(q + " when:3d") + "&hl=id&gl=ID&ceid=ID:id") for q, in [(q,) for q in QUERIES]]
    urls += EXTRA_FEEDS
    out = []
    for name, url in urls:
        try:
            for e in feedparser.parse(url).entries:
                pub = e.get("published_parsed") or e.get("updated_parsed")
                out.append({
                    "link": e.get("link", ""),
                    "title": clean(e.get("title")),
                    "snippet": clean(e.get("summary"))[:400],
                    "source": (e.get("source") or {}).get("title", name) if isinstance(e.get("source"), dict) else name,
                    "published": datetime(*pub[:6], tzinfo=timezone.utc).isoformat() if pub else "",
                })
        except Exception as ex:
            print("抓取失败:", url, ex)
    return out

def analyze(batch):
    from anthropic import Anthropic
    client = Anthropic()
    payload = [{"id": b["id"], "title": b["title"], "snippet": b["snippet"],
                "source": b["source"], "date": b["published"]} for b in batch]
    msg = client.messages.create(
        model=MODEL, max_tokens=4000, system=PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
    text = "".join(b.text for b in msg.content if b.type == "text")
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return {r["id"]: r for r in json.loads(text)}

def main():
    old = load()
    seen = {o["id"] for o in old} | {re.sub(r"\W+", "", o["title"].lower()) for o in old}
    new = []
    for it in fetch():
        if not it["link"] or not it["title"]:
            continue
        it["id"] = hashlib.md5(it["link"].encode()).hexdigest()[:12]
        tkey = re.sub(r"\W+", "", it["title"].lower())
        if it["id"] in seen or tkey in seen:
            continue
        seen.add(it["id"]); seen.add(tkey)
        new.append(it)
    print("新条目:", len(new))

    use_ai = bool(os.getenv("ANTHROPIC_API_KEY"))
    kept = []
    for i in range(0, len(new), BATCH):
        batch = new[i:i + BATCH]
        res = {}
        if use_ai:
            try:
                res = analyze(batch)
            except Exception as ex:
                print("AI 处理失败，本批按未分类保留:", ex)
        for b in batch:
            r = res.get(b["id"])
            if r and not r.get("relevant", True):
                continue
            r = r or {}
            kept.append({
                "id": b["id"], "title": b["title"], "link": b["link"], "source": b["source"],
                "published": b["published"], "category": r.get("category", "未分类"),
                "title_zh": r.get("title_zh") or b["title"],
                "summary_zh": r.get("summary_zh", ""), "summary_en": r.get("summary_en", ""),
                "owner": r.get("owner", ""), "deadline": r.get("deadline", ""), "amount": r.get("amount", ""),
                "added": datetime.now(timezone.utc).isoformat(),
            })
    allitems = sorted(kept + old, key=lambda x: x.get("published") or x.get("added", ""), reverse=True)[:MAX_KEEP]
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(allitems, f, ensure_ascii=False, indent=1)
    print("入库后总数:", len(allitems))

if __name__ == "__main__":
    main()
