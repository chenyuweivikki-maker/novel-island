"""后端全功能自检：真实模型调用 + 全 API 冒烟（E2E）"""
import json
import sys
import time
import urllib.request

BASE = "http://localhost:8000"
PASS, FAIL = 0, 0


def api(method, path, body=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except Exception as e:
        return None, {"error": str(e)}


def check(name, ok, detail=""):
    global PASS, FAIL
    mark = "✅" if ok else "❌"
    print(f"{mark} {name}" + (f" — {detail}" if detail and not ok else ""))
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ── 1. 基础 ──
st, d = api("GET", "/api/health")
check("health", st == 200 and d.get("status") == "ok")

# ── 2. 作品 CRUD ──
st, d = api("GET", "/api/novels")
check("列表 novels", st == 200 and isinstance(d.get("novels"), list), str(d)[:120])
st, d = api("POST", "/api/novel", {"title": "自检书", "expected_words": 100000, "chapter_words": 3000})
check("创建 novel", st == 200 and "novel_id" in d, str(d)[:120])
nid = d.get("novel_id")

# ── 3. 建库（真实 LLM 抽取） ──
sample = ("主角林晚，25岁，法医，性格冷静寡言，童年目睹母亲去世留下创伤。"
          "配角周沉，刑警队长，和林晚是搭档，两人互相看不顺眼又彼此信任。"
          "故事设定在临江市，连续发生三起密室杀人案，每个现场都有一幅红莲画像。"
          "林晚在解剖时发现死者胃里有纸条，写着她的名字。")
st, d = api("POST", "/api/kb/build", {"text": sample, "novel_id": nid})
check("建库 build（LLM 抽取）", st == 200 and d.get("success") and d["stats"]["chunks"] > 0, str(d)[:150])

# ── 4. 基本对话（真实模型） ──
st, d = api("POST", "/api/kb/ask", {"query": "林晚的性格是怎样的？", "novel_id": nid})
ans = d.get("answer", "")
check("事实问答 fact_qa", st == 200 and len(ans) > 10 and "冷静" in ans, ans[:120])

st, d = api("POST", "/api/kb/ask", {"query": "周沉和林晚之间是什么关系？", "novel_id": nid})
check("关系问答（图谱/混合检索）", st == 200 and len(d.get("answer", "")) > 20, d.get("answer", "")[:120])

st, d = api("POST", "/api/kb/ask", {"query": "我卡文了，密室案之后主角该怎么发展？给我三个方向", "novel_id": nid})
check("灵感拓展 inspiration", st == 200 and len(d.get("answer", "")) > 30, d.get("answer", "")[:120])

st, d = api("POST", "/api/kb/ask", {"query": "被读者骂了，好难过，写不下去了", "novel_id": nid})
ans = d.get("answer", "")
check("情感陪伴 companion", st == 200 and len(ans) > 20, ans[:120])

st, d = api("POST", "/api/kb/ask", {"query": "新写的情节：林晚其实是凶手，她杀了所有人", "novel_id": nid})
check("逻辑批判 logic", st == 200, d.get("answer", "")[:120])

# ── 5. 图谱 / 时间线 ──
st, d = api("GET", f"/api/graph?novel_id={nid}")
check("图谱 graph", st == 200 and len(d.get("entities", [])) >= 2, f"entities={len(d.get('entities', []))}")
st, d = api("GET", f"/api/timeline?novel_id={nid}")
check("时间线 timeline", st == 200, f"total={d.get('total')}")

# ── 6. 章节保存（增量入库 + 章纲） ──
st, d = api("POST", "/api/chapter", {"novel_id": nid, "title": "第一章 红莲", "content": "深夜的临江市下了场暴雨。林晚站在解剖台前，刀尖停在死者左胸，那里纹着一朵盛开的红莲。周沉靠在门边抽烟，烟雾里他的声音有点哑：'第三起了。'林晚没抬头，只说：'胃里有东西。'"})
check("保存章节（章纲+增量入库）", st == 200 and d.get("chapter_id"), str(d)[:150])
st, d = api("GET", f"/api/novel/{nid}/chapter_outlines")
check("章纲列表", st == 200 and len(d.get("outlines", [])) >= 1)
st, d = api("GET", f"/api/novel/{nid}/chapters")
check("章节列表", st == 200 and len(d.get("chapters", [])) >= 1)

# ── 7. 大纲 / 背景资料 ──
st, d = api("POST", f"/api/novel/{nid}/outline", {"content": "卷一：三起密室案，红莲画像的真相"})
check("保存大纲", st == 200)
st, d = api("GET", f"/api/novel/{nid}/outline")
check("读取大纲", st == 200 and "红莲" in d.get("content", ""))
st, d = api("POST", f"/api/novel/{nid}/backgrounds", {"category": "世界观", "title": "临江市", "content": "常年下雨的南方城市"})
bg_id = d.get("id")
st, d = api("GET", f"/api/novel/{nid}/backgrounds")
check("背景资料列表", st == 200 and d.get("total", 0) >= 1)

# ── 8. 灵感库全套 ──
st, d = api("POST", "/api/inspirations", {"novel_id": 0, "content": "主角会梦见案发现场，其实那是前世记忆"})
check("灵感上传+AI分类", st == 200 and d.get("category"), str(d)[:100])
insp_id = d.get("id")
st, d = api("GET", "/api/inspirations?novel_id=0")
check("灵感列表", st == 200)
st, d = api("PATCH", f"/api/inspiration/{insp_id}/category", {"insp_id": insp_id, "category": "剧情"})
check("灵感改分类", st == 200)
st, d = api("GET", "/api/inspiration/categories?novel_id=0")
check("分类列表", st == 200 and len(d.get("categories", [])) >= 5)
st, d = api("GET", "/api/inspirations/export?novel_id=0")
check("灵感导出", st == 200 and len(d.get("text", "")) > 0)

# ── 9. 润色（真实模型） ──
st, d = api("POST", "/api/polish", {"text": "他走进房间，看到桌上的信。", "style": "细腻一点"})
check("润色 polish（LLM）", st == 200 and d.get("polished") and len(d["polished"]) > len("他走进房间，看到桌上的信。"), str(d)[:120])

# ── 10. 成本 ──
st, d = api("GET", "/api/cost")
check("成本记录 cost", st == 200 and "total_cost" in d)

# ── 11. 清理自检数据 ──
st, d = api("DELETE", f"/api/background/{bg_id}")
check("删除背景资料", st == 200)

print(f"\n===== 自检结果: {PASS} 通过 / {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
