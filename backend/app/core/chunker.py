"""
文本分块 — 里程碑9升级：Markdown章节感知

里程碑1：纯文本按段落聚合（400字/块）
里程碑9：先按 Markdown 标题（#/##/###）切成章节，章节内再按段落聚合。
         这样结构化内容（人设/大纲/正文）完整保留，检索才有语义。

设计：
  1. 检测文本里的 Markdown 标题（## 人设资料 等）
  2. 有标题 → 按标题分章，每章内段落聚合
  3. 无标题 → 回退纯段落聚合（兼容纯文本）
"""
import re
from dataclasses import dataclass, field


@dataclass
class Chunk:
    id: int
    text: str
    char_count: int
    section: str = ""  # 所属章节标题（里程碑9新增）


def clean_text(text: str) -> str:
    """清洗：统一换行、去多余空行、去首尾空白"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Markdown 标题正则（## 或 ### 开头）
_TITLE_RE = re.compile(r"^(#{1,3})\s+(.+)$")


def _split_by_sections(text: str) -> list[tuple[str, str]]:
    """按 Markdown 标题切成 (章节名, 内容) 列表"""
    sections = []
    current_title = ""
    current_content = []

    for line in text.split("\n"):
        m = _TITLE_RE.match(line.strip())
        if m:
            # 遇到新标题，先保存上一章
            if current_content:
                sections.append((current_title, "\n".join(current_content)))
            current_title = m.group(2).strip()
            current_content = []
        else:
            current_content.append(line)

    # 保存最后一章
    if current_content:
        sections.append((current_title, "\n".join(current_content)))

    return sections


def _chunk_paragraphs(text: str, target_size: int, overlap: int, section: str) -> list[Chunk]:
    """在单个章节内按段落聚合分块（原逻辑）"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[Chunk] = []
    current = ""
    idx = 0

    for para in paragraphs:
        if len(current) + len(para) > target_size and current:
            chunks.append(Chunk(id=idx, text=current.strip(), char_count=len(current), section=section))
            idx += 1
            current = current[-overlap:] + para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append(Chunk(id=idx, text=current.strip(), char_count=len(current), section=section))

    return chunks


def chunk_text(text: str, target_size: int = 400, overlap: int = 60) -> list[Chunk]:
    """
    分块：按 Markdown 章节感知，章节内段落聚合。

    里程碑9：检测到 Markdown 标题 → 按章节分块；否则回退纯段落聚合。
    返回的 Chunk 带 section 字段（所属章节）。
    """
    sections = _split_by_sections(text)

    # 检查是否真的有标题结构（避免把纯文本第一行当标题）
    has_titles = any(t for t, _ in sections if t)

    if not has_titles:
        # 纯文本：回退原逻辑
        return _chunk_paragraphs(text, target_size, overlap, "")

    # 有标题：按章节分块，全局统一 id
    all_chunks: list[Chunk] = []
    next_id = 0
    for section_title, content in sections:
        if not content.strip():
            continue
        sec_chunks = _chunk_paragraphs(content, target_size, overlap, section_title)
        for c in sec_chunks:
            c.id = next_id
            next_id += 1
            all_chunks.append(c)

    return all_chunks
