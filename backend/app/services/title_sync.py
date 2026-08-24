"""对话同步服务：对话提到书名 → 更新作品书名；对话新内容 → 增量入库知识库。

从 main.py 拆分（分类管理）：首页会话每轮对话完成后的「自动整理」逻辑都在这。
"""
import json
import re

from ..core.llm_client import chat
from ..core.memory import memory_manager
from ..core.novel_store import novel_store
from .kb import ingest_material

# ===== 对话书名同步：对话提到书名 → 更新创作/我的作品的书名 =====
# 明确书名表达：《书名》 或 「书名/名字/名称 是/叫/定为/就叫/打算叫/想叫/命名为/起名/取名 XXX」
EXPLICIT_TITLE_PATTERNS = [
    r"《([^《》\n]{1,20})》",
    r"(?:书名|名字|名称)(?:是|为|叫|定为|暂定|就叫|打算叫|想叫|命名为|起名|取名|定名|叫作|叫做)\s*[《「]?\s*([^，。！？、\s《》「」\n]{2,20})",
]
TITLE_STOPWORDS = {"什么", "名字", "书名", "这个", "那个", "新书", "小说"}


def _extract_explicit_title(text: str) -> str:
    """规则提取对话里明确提到的书名（书名号《》 或「书名是/叫 XXX」表达）；没有返回 ''"""
    if not text:
        return ""
    for pat in EXPLICIT_TITLE_PATTERNS:
        for m in re.finditer(pat, text):
            t = m.group(1).strip().strip("《》「」 “”‘’\"")
            # 去掉口语/问句尾巴（吧/啊/呀/呢/哦/了/嘛/呗 等）
            t = re.sub(r"[吧啊呀呢哦了嘛呗啦哇]{1,2}$", "", t).strip()
            if not t or len(t) < 2 or len(t) > 20:
                continue
            if t in TITLE_STOPWORDS or any(w in t for w in TITLE_STOPWORDS):
                continue
            return t
    return ""


BOOK_TITLE_SUMMARY_PROMPT = """你是「小说岛」的建书助手。作者在对话里聊了创作想法，但还没给出明确书名。请根据对话内容拟一个贴切的书名。

对话（作者说的话）：
{text}

规则：
- 若对话里已出现明确书名（作者说「书名就叫XXX」或用了书名号《》），直接返回它
- 否则根据题材/主角/剧情拟一个 2-8 字的自然书名（贴合氛围，不要带「未命名」字样）
- 对话内容太少（只有打招呼/闲聊，没有创作信息）→ 返回 {"title": ""}，表示暂不拟题

只输出 JSON：{"title": "书名"}，不要其他内容。"""


def _llm_summarize_title(text: str) -> str:
    """LLM 从对话内容总结一个书名；内容不足/失败返回 ''（表示不更新）"""
    if not text or len(text.strip()) < 6:
        return ""
    try:
        out = chat(BOOK_TITLE_SUMMARY_PROMPT.replace("{text}", text[-600:]), "请拟书名。",
                   temperature=0.6, max_tokens=60, task="extract").strip()
        if out.startswith("```"):
            out = out.strip("`")
            if out.startswith("json"):
                out = out[4:]
        data = json.loads(out)
        t = (data.get("title") or "").strip()
        if not t or t in ("未命名", "新对话"):
            return ""
        return t
    except Exception as e:
        print(f"[sync_title] LLM 总结书名失败: {e}")
        return ""


def _sync_chat_to_kb(session_id: str) -> None:
    """首页对话内容增量入库（人设/关系/剧情自动填充创作页）：
    把该首页会话里尚未入库的用户消息合并进知识库（build 状态机抽取人物/关系/事件），
    这样「人设卡片」「人物关系图」会自动根据对话内容长出来。幂等（synced 标记防重复）。
    """
    try:
        novel = novel_store.get_novel_by_session(session_id)
        if not novel:
            return
        rows = memory_manager.get_unsynced_user_messages(session_id)
        if not rows:
            return
        # 只入库有信息量的消息（太短 = 打招呼/闲聊，不抽）
        meaningful = [r for r in rows if len((r["content"] or "").strip()) >= 8]
        if not meaningful:
            memory_manager.mark_messages_synced(session_id, [r["id"] for r in rows])
            return
        # 最近最多 3 条合并入库（控制成本，避免一次抽太多）
        batch = meaningful[-3:]
        text = "\n\n".join(r["content"] for r in batch)
        ingest_material(text, novel["id"])
        # 对话中作者提到大纲内容 → 自动捕获进大纲（首页会话绑定书后同样生效）
        _maybe_capture_outline(novel["id"], text)
        memory_manager.mark_messages_synced(session_id, [r["id"] for r in rows])
        print(f"[kb_sync] session={session_id} 对话增量入库 {len(batch)} 条 → 《{novel['title']}》")
    except Exception as e:
        print(f"[kb_sync] 失败: {e}")


def _sync_project_chat_to_kb(novel_id: int, session_id: str) -> None:
    """创作页对话内容增量入库（与首页路径一致，作用域固定为当前小说项目）：
    把该项目会话里尚未入库的用户消息提取进该项目知识库（人设/关系/事件自动填充）。
    """
    try:
        scope = str(novel_id)
        rows = memory_manager.get_unsynced_user_messages(session_id, scope=scope)
        if not rows:
            return
        meaningful = [r for r in rows if len((r["content"] or "").strip()) >= 8]
        if not meaningful:
            memory_manager.mark_messages_synced(session_id, [r["id"] for r in rows], scope=scope)
            return
        # 最近最多 3 条合并入库（控制成本，避免一次抽太多）
        batch = meaningful[-3:]
        text = "\n\n".join(r["content"] for r in batch)
        ingest_material(text, novel_id)
        memory_manager.mark_messages_synced(session_id, [r["id"] for r in rows], scope=scope)
        print(f"[kb_sync] project={novel_id} 创作页对话增量入库 {len(batch)} 条")
    except Exception as e:
        print(f"[kb_sync] 项目同步失败: {e}")


# ===== 对话→大纲自动捕获（作者提到大纲内容就入库）=====
OUTLINE_CAPTURE_PROMPT = """你是「小说岛」的大纲助手。下面是这本书当前的大纲，以及作者在对话里刚说的话。

当前大纲：
{current}

作者的话：
{message}

请判断作者的话是否提供了大纲信息（梗概/主题/主线/冲突/结局）。如果某块有新信息，就给出更新后的该块内容；没提到的块不要给；都没有就返回 {{}}。

只输出 JSON，例如：{{"logline":"一句话梗概","theme":"主题"}}（只含作者说到且有信息的块）。"""

# 大纲信号词：命中才尝试捕获，控制 LLM 调用成本
_OUTLINE_SIGNALS = ["梗概", "一句话", "故事", "讲的是", "讲一个", "主题", "立意", "主线", "分卷",
                    "冲突", "转折", "结局", "结尾", "大纲", "上卷", "中卷", "下卷", "想写", "这个故事", "这本书讲"]
_OUTLINE_KEYS = ("logline", "theme", "plot", "conflict", "ending")


def _parse_outline_partial(text: str) -> dict:
    """解析 LLM 输出，只保留非空且属于大纲五块的字段"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    try:
        d = json.loads(t)
        if isinstance(d, dict):
            return {k: str(v).strip() for k, v in d.items()
                    if k in _OUTLINE_KEYS and str(v or "").strip()}
    except Exception:
        pass
    return {}


def _maybe_capture_outline(novel_id: int, message: str) -> None:
    """对话中作者提到大纲内容 → 自动捕获进大纲（保存在 novels.outline 的 JSON 里）。"""
    if not novel_id or not message or len(message.strip()) < 6:
        return
    if not any(kw in message for kw in _OUTLINE_SIGNALS):
        return
    # 取当前大纲（兼容旧纯文本）
    raw = novel_store.get_novel_outline(novel_id)
    try:
        cur = json.loads(raw) if raw else {}
        if not isinstance(cur, dict):
            cur = {"plot": raw}
    except Exception:
        cur = {"plot": raw} if raw else {}
    try:
        out = chat(OUTLINE_CAPTURE_PROMPT.format(current=json.dumps(cur, ensure_ascii=False), message=message),
                   "请判断并更新大纲。", temperature=0.2, max_tokens=200, task="extract")
    except Exception as e:
        print(f"[outline_capture] LLM 失败: {e}")
        return
    update = _parse_outline_partial(out)
    if not update:
        return
    changed = False
    for k, v in update.items():
        if v and cur.get(k) != v:
            cur[k] = v
            changed = True
    if changed:
        novel_store.update_novel_outline(novel_id, json.dumps(cur, ensure_ascii=False))
        print(f"[outline_capture] 对话捕获大纲 novel={novel_id} 更新块: {list(update.keys())}")


def _maybe_sync_book_title(session_id: str, query: str = "") -> None:
    """首页会话书名同步（每轮对话完成后调用，幂等）：
    1. 对话里明确提到书名（《》/「书名是XXX」）→ 直接更新创作/我的作品的书名（作者说了算）
    2. 没提到 → 仅当当前书名是自动兜底（未命名/主角名/素材临时名，title_auto=1）时，
       LLM 从对话内容总结一个书名更新
    已明确命名的书（title_auto=0）不会被总结覆盖，只有作者再次明确说出新书名才会更新。
    """
    try:
        novel = novel_store.get_novel_by_session(session_id)
        if not novel:
            return
        # 对话增量入库：人设/关系/剧情自动填充创作页（与书名同步无关，每轮都执行）
        _sync_chat_to_kb(session_id)
        mem = memory_manager.get_memory(None, session_id)
        texts = [m["content"] for m in mem.get_context() if m.get("role") == "user"]
        if query and query.strip() and (not texts or texts[-1] != query.strip()):
            texts.append(query.strip())
        text = "；".join(texts[-6:])
        title = _extract_explicit_title(text)
        if title and title != novel["title"]:
            novel_store.update_novel_title(novel["id"], title, title_auto=0)
            print(f"[sync_title] session={session_id} 对话明确书名 → 更新《{novel['title']}》→《{title}》")
        elif not novel["title_auto"]:
            return  # 已有正式书名且本轮没提新书名 → 不动
        else:
            t = _llm_summarize_title(text)
            if t and t != novel["title"]:
                novel_store.update_novel_title(novel["id"], t, title_auto=0)
                print(f"[sync_title] session={session_id} 自动总结书名 → 《{t}》（原：《{novel['title']}》）")
    except Exception as e:
        print(f"[sync_title] 失败: {e}")
