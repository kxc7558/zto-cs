"""话术知识库检索（LLM-wiki 模式）：按问题类型从 reply_wiki.md 取对应小节注入 prompt。

设计口径：话术量小、结构化，LLM-wiki（结构化知识按需检索）比 RAG 向量检索更对路。
- 闸门用模板库（rules.py 的 yaml，确定版）
- AI 审核/建议用本模块检索的"知识面"（口径 + 真实话术 + AI 提示）
"""
import re
from pathlib import Path

from shared.config import ROOT

WIKI_PATH = ROOT / "config" / "reply_wiki.md"

_SENSITIVE_SECTIONS = {"仲裁证据上传", "理赔证据上传"}


def _parse_sections(md: str) -> dict[str, str]:
    """按 '## 标题' 切小节。返回 {标题: 小节全文}。"""
    sections = {}
    current = None
    buf: list[str] = []
    for line in md.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1)
            buf = []
        elif current:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    return sections


def load_wiki() -> dict[str, str]:
    if not WIKI_PATH.exists():
        return {}
    return _parse_sections(WIKI_PATH.read_text(encoding="utf-8"))


def lookup(ptype: str, category: str = "") -> str | None:
    """按问题类型（或一级类型）取知识小节。

    标题可能带后缀（如「理赔证据上传 ⚠️ 敏感」），做前缀匹配。
    """
    sections = load_wiki()
    for key in (ptype, category):
        if not key:
            continue
        if key in sections:
            return sections[key]
        # 前缀匹配（标题带 ⚠️ 等后缀）
        for title, body in sections.items():
            if title.startswith(key):
                return body
    return None


def is_sensitive_type(ptype: str) -> bool:
    """类型是否敏感（知识库里标了 ⚠️ 敏感 的小节）。"""
    sections = load_wiki()
    for key, body in sections.items():
        if key == ptype and "敏感" in body.split("\n")[0]:
            return True
    return ptype in _SENSITIVE_SECTIONS


def context_for_review(ptype: str, category: str = "") -> str:
    """给 AI 审核的知识注入：该类型的口径与参考话术（截断防 prompt 过长）。"""
    body = lookup(ptype, category)
    if not body:
        return "（知识库无该类型条目，按通用标准判断，倾向转人工）"
    # 截断（知识小节一般 <800 字，防注入过长）
    return body[:800]
