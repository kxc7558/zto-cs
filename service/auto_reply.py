"""自动回复引擎：总开关控制、敏感类硬闸（不受开关影响）、每条留痕。

三层控制（README / §15 设计）：
1. 总开关 write.auto_reply.enabled（工作台页面可切换，落 settings.yaml）
2. 敏感类硬闸：仲裁/理赔/责任/费用类永不自动（代码强制）
3. 每条自动提交都走同一管线（幂等账本 auto=true 标记），审计可查
"""
import asyncio
import random

from shared import audit
from shared import settings as cfg
from service import rules
from service.problem_reader import fetch_problem_list
from service.replier import commit_reply
from db import ledger


def auto_reply_enabled() -> bool:
    return bool(cfg.get("write.auto_reply.enabled", False))


def set_auto_reply(enabled: bool) -> dict:
    val = cfg.set_value("write.auto_reply.enabled", bool(enabled))
    audit.append("auto_reply_toggled", {"enabled": bool(enabled)}, actor="human")
    return val


def plan_auto_replies(items: list[dict]) -> dict:
    """对一批问题件给出自动回复计划（不动浏览器，纯规则）：哪些能自动、哪些转人工。"""
    plan = []
    for it in items:
        if it.get("latest_reply"):
            continue  # 已回复，跳过
        ptype, desc = it.get("ptype") or "", it.get("description") or ""
        sensitive = rules.is_sensitive(ptype, desc)
        text = rules.match_template(ptype, it.get("category") or "")
        ok, why = rules.validate_reply(text or "")
        entry = {
            "problem_id": it.get("problem_id"),
            "waybill_no": it.get("waybill_no"),
            "ptype": ptype,
            "description": desc,
            "sensitive": sensitive,
            "reply_text": text if ok else None,
            "verdict": (
                "human" if sensitive else
                ("human" if not ok else "auto")
            ),
            "reason": (
                "sensitive-type" if sensitive else
                ("rule-invalid:" + why if not ok else "ok")
            ),
        }
        plan.append(entry)
    return {"auto": [p for p in plan if p["verdict"] == "auto"],
            "human": [p for p in plan if p["verdict"] == "human"]}


async def run_auto_reply(max_n: int = 5) -> dict:
    """执行一轮自动回复（总开关开着才跑）：拉列表→计划→逐条提交。

    max_n 限流：单轮最多 N 条（反风控，默认 5）。
    """
    if not auto_reply_enabled():
        return {"ok": False, "note": "auto_reply disabled", "processed": 0}

    listing = await fetch_problem_list()
    items = [i.model_dump() for i in listing.items]
    plan = plan_auto_replies(items)
    targets = plan["auto"][:max_n]

    results = []
    for t in targets:
        r = await commit_reply(
            problem_id=t["problem_id"], waybill_no=t["waybill_no"],
            reply_text=t["reply_text"], confirmed_by="system:auto", auto=True,
            desc_hint=(t.get("description") or "")[:20],
        )
        results.append({"problem_id": t["problem_id"], "ok": r.get("ok"), "note": r.get("note")})
        await asyncio.sleep(random.uniform(*((3, 8))))  # 反风控：条间拟人延迟

    return {
        "ok": True, "processed": len(results),
        "auto_eligible": len(plan["auto"]), "human_pending": len(plan["human"]),
        "results": results,
    }
