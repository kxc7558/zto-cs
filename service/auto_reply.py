"""自动回复引擎（重构后）：统一走闸门管线，不再有自己的旁路。

review CRITICAL-1 修复：旧版 run_auto_reply 自建计划（只查开关+敏感词+规则），
绕过了 gatekeeper 的 AI 审核层。现在唯一写路径 = gatekeeper.run_gates（四层闸门），
数据源与执行通道经 registry 按配置装配。

模式（三层）：
- off：只生成建议（工作台展示），不写
- semi：闸门通过后下发任务给插件，插件填弹窗、人工点提交
- full：闸门通过后插件自动提交（限速）

敏感类硬闸在 gatekeeper ②（不受任何开关影响）。
"""
from service import gatekeeper
from service.registry import get_source
from shared import audit
from shared import settings as cfg


def get_mode() -> str:
    """off / semi / full（工作台开关 off↔semi↔full 循环）。"""
    enabled = bool(cfg.get("write.auto_reply.enabled", False))
    if not enabled:
        return "off"
    return cfg.get("write.auto_reply.mode", "semi")


def set_mode(mode: str) -> dict:
    """设置模式：off（关闭）/ semi（填好人工确认）/ full（自动提交）。"""
    if mode not in ("off", "semi", "full"):
        raise ValueError(f"invalid mode: {mode}")
    cfg.set_value("write.auto_reply.enabled", mode != "off")
    if mode != "off":
        cfg.set_value("write.auto_reply.mode", mode)
    audit.append("auto_reply_mode_set", {"mode": mode}, actor="human")
    return {"enabled": mode != "off", "mode": mode}


def plan_auto_replies(items: list[dict]) -> dict:
    """对一批问题件给出分类视图（只读展示用，不写）：自动可回 / 需人工。"""
    plan = []
    for it in items:
        if it.get("latest_reply"):
            continue
        from service.rules import is_sensitive
        sensitive = is_sensitive(it.get("ptype") or "", it.get("description") or "")
        plan.append({"problem_id": it.get("problem_id"), "sensitive": sensitive,
                     "verdict": "human" if sensitive else "auto"})
    return {"auto": [p for p in plan if p["verdict"] == "auto"],
            "human": [p for p in plan if p["verdict"] == "human"]}
