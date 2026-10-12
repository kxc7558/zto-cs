"""闸门管线：一条回复从"拟生成"到"放行执行"的全部检查。

模块化设计：管线只认 ProblemParcel + 回复文本，不认数据源/执行通道。
四层闸门（缺一不放行）：
  ① 总开关（write.enabled + auto_reply.enabled）
  ② 敏感类硬闸（仲裁/理赔/责任/费用——代码强制，不受开关影响）
  ③ 模板匹配 + 确定性规则校验（必填/长度/敏感词）
  ④ AI 审核（本机模型，fail-safe 转人工）

任何一层不过 → 返回不放行 + 理由 + 停在哪层。执行通道拿到的是"已过全部闸门"的回复。

**所有写路径必须经过本管线**——包括人工单条回复（/reply/prepare+commit）。
人工路径与自动路径的差异只在①（人工确认代替总开关）与④（人工已审，AI 审核可跳过）。
"""
from dataclasses import dataclass

from service import rules, reviewer
from shared import audit
from shared.models import ProblemParcel


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reply_text: str | None
    reason: str
    gate: str  # 停在哪一层


async def run_gates(parcel: ProblemParcel, switches: dict,
                    reviewer_impl=None, human_confirmed: bool = False) -> GateResult:
    """跑全部闸门。

    switches: {auto_enabled: bool}
    human_confirmed: True 表示人工已看预览点过确认（①由人代替；④人工已审可跳过）。
    reviewer_impl 可注入（测试用）。
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

    # ④ AI 审核（人工确认路径跳过——人已审；自动路径 fail-safe：故障 → human）
    if not human_confirmed:
        impl = reviewer_impl or reviewer.AiReviewer()
        review = await impl.review_reply(
            ptype=parcel.ptype or "", description=parcel.description or "",
            reply_text=text, outlet_from=parcel.outlet_from or "", outlet_notify=parcel.outlet_notify or "",
        )
        if review.get("verdict") != "pass":
            return GateResult(False, None, f"ai-review:{review.get('reason', 'human')}", "ai_review")

    audit.append("gates_passed", {"problem_id": parcel.problem_id, "ptype": parcel.ptype,
                                  "human": human_confirmed})
    return GateResult(True, text, "ok", "all")
