"""闸门管线：一条回复从"拟生成"到"放行执行"的全部检查。

**AI 已后置（用户拍板：AI 不能前置阻塞提交路径）**——闸门全确定性：
  ① 总开关（write.enabled + auto_reply.enabled）
  ② 敏感类硬闸（仲裁/理赔/责任/费用——代码强制，不受开关影响）
  ③ 模板匹配 + 确定性规则校验（必填/长度/敏感词）

任何一层不过 → 不放行 + 理由 + 停在哪层。全部毫秒级，无 LLM 调用。
AI 审核挪到**事后批量抽查**（service/retrospect.py）：只报告不动数据。

所有写路径必须经过本管线——包括人工单条回复。
人工路径与自动路径的差异只在①（人工确认代替总开关）。
"""
from dataclasses import dataclass

from service import rules
from shared import audit
from shared.models import ProblemParcel


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reply_text: str | None
    reason: str
    gate: str  # 停在哪一层


async def run_gates(parcel: ProblemParcel, switches: dict,
                    human_confirmed: bool = False) -> GateResult:
    """跑全部闸门（全确定性，无 AI）。

    switches: {auto_enabled: bool}
    human_confirmed: True 表示人工已看预览点过确认（①由人代替）。
    """
    # ① 总开关（人工确认可代替）
    if not human_confirmed and not switches.get("auto_enabled"):
        return GateResult(False, None, "auto reply disabled", "switch")

    # ② 敏感类硬闸（不受开关影响，人工路径同样拦——敏感件必须人在中通原系统处理）
    if parcel.needs_human or rules.is_sensitive(parcel.ptype or "", parcel.description or ""):
        return GateResult(False, None, "sensitive type", "sensitive")

    # 已回复的不进管线
    if parcel.latest_reply:
        return GateResult(False, None, "already replied", "done")

    # ③ 模板匹配 + 规则校验
    text = rules.match_template(parcel.ptype or "", parcel.category or "")
    ok, why = rules.validate_reply(text or "")
    if not ok:
        return GateResult(False, None, f"rule-invalid:{why}", "rules")

    audit.append("gates_passed", {"problem_id": parcel.problem_id, "ptype": parcel.ptype,
                                  "human": human_confirmed})
    return GateResult(True, text, "ok", "all")
