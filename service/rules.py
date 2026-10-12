"""确定性回复规则引擎（不涉 AI）：
- 回复模板库（对齐中通自带回复语模板，配置化）
- 敏感类判定（转人工硬闸，不受自动回复开关影响）
- 自动回复放行判定
"""
import re
from pathlib import Path

import yaml

from shared.config import SENSITIVE_KEYWORDS, ROOT

TEMPLATES_PATH = ROOT / "config" / "reply_templates.yaml"


def load_templates() -> dict:
    """模板库：{问题件类型(或一级类型): [回复语,...]}。配置化（中通改口径只改配置）。"""
    if TEMPLATES_PATH.exists():
        return yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8")) or {}
    return {}


def is_sensitive(ptype: str, description: str) -> bool:
    """敏感类硬闸：涉仲裁/理赔/责任/费用的问题件永不自动回复。"""
    text = f"{ptype or ''}{description or ''}"
    return any(k in text for k in SENSITIVE_KEYWORDS)


def match_template(ptype: str, category: str) -> str | None:
    """按问题件类型匹配模板；类型没配则回退一级类型；都没有返回 None（转人工）。"""
    tpl = load_templates()
    for key in (ptype, category):
        if key and key in tpl and tpl[key]:
            return tpl[key][0]
    # 未配置类型的兜底：uncategorized
    return (tpl.get("uncategorized") or [None])[0]


def validate_reply(text: str) -> tuple[bool, str]:
    """确定性校验：非空、长度合规、无敏感情形。模型/模板文本都要过这道闸。"""
    if not text or not text.strip():
        return False, "empty"
    if len(text) > 200:
        return False, "too_long(>200)"
    for k in SENSITIVE_KEYWORDS:
        if k in text:
            return False, f"sensitive_keyword:{k}"
    return True, "ok"
