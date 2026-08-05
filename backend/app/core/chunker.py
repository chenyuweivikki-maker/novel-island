"""文本分块 — 按段落聚合，目标400字/块，15% overlap"""
import re
from dataclasses import dataclass


@dataclass
class Chunk:
    id: int
    text: str
    char_count: int


def clean_text(text: str) -> str:
    """清洗：统一换行、去多余空行、去首尾空白"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, target_size: int = 400, overlap: int = 60) -> list[Chunk]:
    """
    按段落聚合分块：
    1. 先按空行切段落
    2. 逐段累加，超过 target_size 就截断
    3. 下一块带上前一块尾部 overlap 字符作为上下文
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[Chunk] = []
    current = ""
    idx = 0

    for para in paragraphs:
        if len(current) + len(para) > target_size and current:
            chunks.append(Chunk(id=idx, text=current.strip(), char_count=len(current)))
            idx += 1
            # overlap: 前一块尾部
            current = current[-overlap:] + para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append(Chunk(id=idx, text=current.strip(), char_count=len(current)))

    return chunks
