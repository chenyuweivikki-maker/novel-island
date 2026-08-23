"""自动建书服务：从会话历史自动开书入库（对话式建库的建书环节）。

从 main.py 拆分（分类管理）：会话 → 判断可建书 → 建书 + 绑定 session + 历史入库 + 防重复。
"""
import json
import re

from ..core.llm_client import chat
from ..core.memory import memory_manager
from ..core.novel_store import novel_store
from .kb import ingest_material
from .title_sync import _extract_explicit_title

# 已自动建库的 session_id（进程内缓存，防重复建书；持久化绑定以 novels.session_id 为准）
_AUTO_BOOKED: set = set()

# 常见题材词（无明确书名时给建书兜底 genre；有明确题材短语时优先用短语）
_GENRE_HINTS = ["都市", "奇幻", "悬疑", "古风", "科幻", "言情", "百合", "武侠", "末世",
                "穿越", "电竞", "校园", "职场", "修仙", "刑侦", "年代", "重生", "娱乐圈"]


def _extract_genre(text: str) -> str:
    """从对话里抽一个题材：优先取「...小说/文/题材/类型」前的短语（如 现代都市百合），
    否则从常见题材词里做子串匹配兜底；都没有返回 ''。"""
    if not text:
        return ""
    m = re.search(r"(?:是一本|是本|题材是|类型是|属于|背景是)\s*([\u4e00-\u9fa5]{2,8}?)(?:小说|文|题材|类型)", text)
    if m:
        g = m.group(1).strip()
        if 2 <= len(g) <= 8:
            return g
    for g in _GENRE_HINTS:
        if g in text:
            return g
    return ""


def _try_create_book_from_title(session_id: str, query: str = "") -> int | None:
    """对话里出现明确书名（《》/「书名是/叫XXX」）且该会话尚未建书 → 立即建书。
    只带「书名 + 题材」（title_auto=0 表示已明确命名），不在此刻把整段历史入库——
    人物/人设等后续对话会通过 _sync_chat_to_kb 增量补进知识库。返回 novel_id；否则 None。"""
    if session_id in _AUTO_BOOKED or novel_store.get_novel_by_session(session_id):
        return None
    title = _extract_explicit_title(query or "")
    if not title:
        return None
    genre = _extract_genre(query or "")
    novel_id = novel_store.create_novel(title, 0, 0, genre, session_id=session_id, title_auto=0)
    _AUTO_BOOKED.add(session_id)
    print(f"[auto_book] session={session_id} 明确书名即建书: 《{title}》({genre}) novel_id={novel_id}")
    return novel_id


def _detect_character_names(text: str) -> list:
    """规则检测角色名：中文 2-4 字人名（排除常见非人名词）"""
    stopwords = {"什么", "怎么", "我们", "你们", "他们", "自己", "一个", "这个", "那个", "不是",
                 "就是", "但是", "因为", "所以", "如果", "还是", "没有", "可以", "告诉", "小说",
                 "题材", "主角", "女主", "男主", "配角", "人设", "灵感", "剧情", "书"}
    found = []
    for m in re.findall(r"[\u4e00-\u9fa5]{2,3}(?:[\u4e00-\u9fa5]{1})?", text):
        name = m.strip()
        if len(name) < 2 or len(name) > 4:
            continue
        if name in stopwords:
            continue
        if any(w in name for w in ("一个", "这个", "那个", "我们", "你们", "他们")):
            continue
        found.append(name)
    seen = set()
    uniq = []
    for n in found:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq[:5]


AUTO_BOOK_PROMPT = """你是「小说岛」的建书助手。作者在对话里已经聊出了一些创作想法，请判断是否已经可以开书，并提取建书信息。

对话历史（作者说的话）：
{history}

判断标准（满足任一即 ready=true）：
- 出现了明确书名（如"书名观南嘉措"）
- 出现了明确角色名（如"主角叫江观南"）
- 有清晰的主角/女主/男主设定描述（如"女主是美妆博主，性格很强硬"——没名字也算）
- 有题材 + 至少一条具体设定（如"都市文，主角是律师"）
- 作者粘贴了大段素材/正文（明显是作品内容）

请输出 JSON：{{
  "ready": true或false,
  "title": "书名（明确书名；没有就空字符串 ''，不要编造）",
  "genre": "题材（如 都市/奇幻/悬疑，没有就空）",
  "characters": ["角色名列表（明确出现的角色名；没有名字就空数组）"]
}}
只输出 JSON。"""


def _auto_create_book(session_id: str, current_query: str = "") -> int | None:
    """从会话历史自动建书（LLM 判断已有足够建书信息时调用）。返回 novel_id；失败/已建过返回 None"""
    if session_id in _AUTO_BOOKED:
        return None
    # 该会话已绑定书（含老会话回填）→ 直接复用，不重复建书
    existing = novel_store.get_novel_by_session(session_id)
    if existing:
        _AUTO_BOOKED.add(session_id)
        return existing["id"]
    # 明确书名 → 立即建书（只带书名+题材；人物/人设靠后续对话增量入库）。
    # 不再做「回填最近一本未绑定书」的内容无关绑定：那会让不同书的对话串到同一个项目。
    nid = _try_create_book_from_title(session_id, current_query)
    if nid:
        return nid
    memory = memory_manager.get_memory(None, session_id)
    hist = memory.get_context()
    user_msgs = [m["content"] for m in hist if m.get("role") == "user"]
    # 当前轮还没写入记忆，需并入判断
    if current_query and current_query.strip():
        user_msgs.append(current_query.strip())
    if not user_msgs:
        return None
    joined = "；".join(user_msgs[-10:])
    # 触发条件扩展：单条大段素材（>80字）直接视为作品内容 → 开书入库
    is_material = any(len(m) > 80 for m in user_msgs[-3:])
    title, genre, characters, ready = "", "", [], False
    try:
        out = chat(AUTO_BOOK_PROMPT.format(history=joined[-600:]), "请判断并建书。",
                   temperature=0.2, max_tokens=250, task="extract")
        out = out.strip()
        if out.startswith("```"):
            out = out.strip("`")
            if out.startswith("json"):
                out = out[4:]
        data = json.loads(out)
        ready = bool(data.get("ready"))
        title = (data.get("title") or "").strip()
        genre = (data.get("genre") or "").strip()
        characters = [c.strip() for c in (data.get("characters") or []) if c and c.strip()]
    except Exception as e:
        print(f"[auto_book] 抽取失败: {e}")
    # 触发条件：大段素材 OR LLM 判断 ready；都不满足 → 等作者继续聊
    if not (is_material or ready):
        return None
    # 书名缺省：用主角名或"未命名"兜底（title_auto=1 标记自动兜底，后续对话可总结/覆盖）
    title_auto = 1
    if not title:
        title = characters[0] if characters else "未命名"
    else:
        title_auto = 0  # LLM 明确抽取的书名（作者说过）→ 已明确命名
    novel_id = novel_store.create_novel(title, 0, 0, genre, session_id=session_id, title_auto=title_auto)
    _AUTO_BOOKED.add(session_id)
    # 建书时历史已整体入库 → 标记已同步，防止下一轮重复抽取
    memory_manager.mark_all_synced(session_id)
    print(f"[auto_book] session={session_id} 自动建书: 《{title}》({genre}) novel_id={novel_id}, 角色={characters}, material={is_material}")
    # 把设定文本入库（带角色名提示，提升实体抽取质量）
    try:
        material = joined
        if characters:
            material += "。本故事主要角色：" + "、".join(characters)
        ingest_material(material, novel_id)
    except Exception as e:
        print(f"[auto_book] 首次入库失败: {e}")
    return novel_id


def _home_session_booked(session_id: str) -> bool:
    """该会话是否已自动建书：优先查库（持久化绑定，重启不丢），进程内集合作缓存"""
    return session_id in _AUTO_BOOKED or novel_store.get_novel_by_session(session_id) is not None


def _auto_create_book_from_material(material: str, session_id: str) -> int | None:
    """无项目拖入素材时：直接自动建书（书名取素材首句前几字）并入库素材"""
    if session_id in _AUTO_BOOKED or novel_store.get_novel_by_session(session_id):
        return None
    text = material.strip()
    if not text:
        return None
    # 书名：取素材首句前 8 字（去掉标点）作临时名（title_auto=1：自动兜底，后续对话可总结/覆盖）
    first = re.split(r"[。\n！？!?]", text)[0].strip()
    title = first[:8] if first else "未命名"
    novel_id = novel_store.create_novel(title, 0, 0, "", session_id=session_id, title_auto=1)
    _AUTO_BOOKED.add(session_id)
    # 素材已入库，历史消息视为已同步，防止下一轮重复抽取
    memory_manager.mark_all_synced(session_id)
    print(f"[auto_book] session={session_id} 素材自动建书: 《{title}》 novel_id={novel_id}")
    try:
        ingest_material(text, novel_id)
    except Exception as e:
        print(f"[auto_book] 素材入库失败: {e}")
    return novel_id
