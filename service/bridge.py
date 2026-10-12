"""插件桥接层：插件=手（页面里的读写），服务端=脑（决策/审核/留痕）。

插件上报的行格式与 problem_reader 的解析对齐（表头文本 -> 值的 dict）。
闸门逻辑全复用：开关 / 敏感硬闸 / AI 审核 / 幂等账本。
"""
from datetime import datetime

from shared import audit
from shared.models import ProblemParcel
from service import rules, reviewer
from service.problem_reader import _make_problem_id
from db import ledger


def _row_to_parcel(row: dict) -> ProblemParcel:
    """插件上报的行（{表头: 值}）→ ProblemParcel。"""
    desc = row.get("问题件描述") or ""
    ptype = row.get("问题件类型") or ""
    category = row.get("问题件一级类型") or ""
    created = row.get("创建时间") or ""
    anchor_src = f"{row.get('运单号') or ''}|{ptype}|{created}|{desc[:40]}"
    return ProblemParcel(
        problem_id=_make_problem_id({
            "waybill_no": row.get("运单号"),
            "ptype": ptype,
            "created_at": created,
            "description": desc,
        }),
        waybill_no=row.get("运单号") or (row.get("破损子单号") or None),
        outlet_from=row.get("发布网点"),
        outlet_notify=row.get("通知网点"),
        category=category,
        ptype=ptype,
        description=desc,
        latest_reply=row.get("最新回复内容") or None,
        latest_reply_at=row.get("最新回复时间") or None,
        order_source=row.get("订单来源"),
        status=row.get("快件状态"),
        province=row.get("派件省区"),
        creator=row.get("创建人"),
        created_at=created,
        needs_human=rules.is_sensitive(ptype, desc),
    )


async def ingest_rows(rows: list[dict], page_url: str | None) -> dict:
    """插件上报列表 → 去重建档 → 跑闸门计划。返回统计。"""
    import json as _json
    parcels = [_row_to_parcel(r) for r in rows if isinstance(r, dict)]
    # 去重（按 problem_id）
    seen = set()
    uniq = []
    for p in parcels:
        if p.problem_id not in seen:
            seen.add(p.problem_id)
            uniq.append(p)
    plan_auto = []
    plan_human = []
    for p in uniq:
        if p.latest_reply:
            continue  # 已回复
        text = rules.match_template(p.ptype or "", p.category or "")
        ok, why = rules.validate_reply(text or "")
        if p.needs_human or not ok:
            plan_human.append({"problem_id": p.problem_id, "reason": "sensitive" if p.needs_human else why})
        else:
            plan_auto.append({"problem_id": p.problem_id})
    # 写缓存供 pending_tasks 取未回复件
    from service.bridge_cache import save_parcels
    save_parcels([_json.loads(p.model_dump_json()) for p in uniq])
    audit.append("bridge_ingest_list", {
        "rows": len(rows), "unreplied": len(plan_auto) + len(plan_human),
        "auto_eligible": len(plan_auto), "sensitive": sum(1 for p in uniq if p.needs_human),
    })
    return {
        "ok": True,
        "total": len(uniq),
        "unreplied": len(plan_auto) + len(plan_human),
        "auto_eligible": len(plan_auto),
        "human_pending": len(plan_human),
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
    }


async def pending_tasks(limit: int = 5) -> dict:
    """插件轮询：该回哪条、用什么话术（已过四层闸门）。

    闸门走 gatekeeper.run_gates（模块化管线：开关/敏感/规则/AI审核）。
    数据源按 bridge_mode 装配（extension / cdp）。
    """
    from service.auto_reply import auto_reply_enabled
    from service.gatekeeper import run_gates
    from service.registry import get_source
    from shared import settings as cfg

    if not auto_reply_enabled():
        return {"ok": True, "enabled": False, "tasks": []}

    mode = cfg.get("write.auto_reply.mode", "extension")
    source = get_source(mode)
    parcels = await source.fetch_parcels()
    switches = {"auto_enabled": True}
    tasks = []
    for p in parcels:
        if len(tasks) >= limit:
            break
        gate = await run_gates(p, switches)
        if not gate.passed:
            continue
        tasks.append({
            "problem_id": p.problem_id,
            "waybill_no": p.waybill_no,
            "desc_hint": (p.description or "")[:20],
            "ptype": p.ptype,
            "reply_text": gate.reply_text,
        })
    audit.append("bridge_tasks_pending", {"issued": len(tasks)})
    return {"ok": True, "enabled": True, "tasks": tasks}


async def ingest_result(problem_id: str, reply_text: str, submitted: bool,
                        readback: str, mode: str, error: str | None) -> dict:
    """插件回传结果 → 落账本、审计。"""
    confirmed_by = f"extension:{mode}"
    key = ledger.make_idempotency_key(problem_id, "reply", reply_text, confirmed_by)
    existing = ledger.get_status(key)
    if existing and existing["status"] == "committed":
        return {"ok": True, "note": "already committed (idempotent)"}
    if not existing:
        ledger.record_intent(problem_id, None, "reply", reply_text, key,
                             auto=(mode == "full"), confirmed_by=confirmed_by)
    if submitted:
        ledger.record_result(key, "committed", platform_receipt=(readback or "")[:200])
        audit.append("bridge_reply_committed", {
            "problem_id": problem_id, "mode": mode, "text_len": len(reply_text),
            "readback": (readback or "")[:120],
        }, actor=confirmed_by)
        return {"ok": True, "note": "committed"}
    else:
        ledger.record_result(key, "failed", platform_receipt=(error or "submission failed"))
        audit.append("bridge_reply_failed", {
            "problem_id": problem_id, "mode": mode, "error": (error or "")[:120],
        }, actor=confirmed_by)
        return {"ok": False, "note": error or "failed"}
