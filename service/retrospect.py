"""AI 后置分析（retrospect）：事后批量审已提交的回复，只报告不动数据。

用户拍板：AI 不能前置（阻塞提交路径），做后置分析。
- 提交路径：闸门全确定性（开关+敏感+规则+人工确认），毫秒级
- 本模块：定时批量拉审计里「已提交的回复」，AI 逐条审「现在回头看合适吗」，
  输出异常清单+理由，工作台显示「AI 抽查：N 条建议复查」
- **只报告，不动任何数据**——补救由人工决定
- AI 挂了不影响任何业务（不在提交路径上）
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from shared import audit
from shared.config import AUDIT_DIR
from service.wiki import context_for_review

RETROSPECT_PATH = AUDIT_DIR / "retrospect.json"


def _committed_replies(limit: int = 20) -> list[dict]:
    """从审计哈希链拉最近的「已提交回复」记录。"""
    d = max((p for p in AUDIT_DIR.iterdir()
             if p.is_dir() and len(p.name) == 7 and p.name[4] == "-"),
            default=AUDIT_DIR)
    f = d / "audit.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines()[-200:]:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event") in ("reply_committed", "bridge_reply_committed"):
            out.append({
                "ts": rec.get("ts"),
                "problem_id": rec.get("payload", {}).get("problem_id"),
                "mode": rec.get("payload", {}).get("mode"),
                "text_len": rec.get("payload", {}).get("text_len"),
            })
    return out[-limit:]


async def run_retrospect(limit: int = 20) -> dict:
    """一轮后置分析：批量审最近提交的回复，输出异常清单。

    AI 调用失败 → 记录分析失败（不影响任何业务），下次再跑。
    """
    replies = _committed_replies(limit)
    if not replies:
        return {"ok": True, "reviewed": 0, "flagged": [], "note": "no committed replies"}

    from service.reviewer import AiReviewer
    flagged = []
    for r in replies:
        # 从账本拉回复原文与问题件上下文
        from db import ledger
        rows = ledger.get_by_problem(r.get("problem_id") or "")
        committed = next((x for x in rows if x["status"] == "committed"), None)
        if not committed:
            continue
        reply_text = committed.get("reply_text", "")
        # 问题件上下文从最近审计/缓存拿不到的话，用知识库按类型兜底
        prompt = (
            "你是物流网点回复质检员。事后抽查这条已提交的回复，判断是否合适。只输出 JSON。\n\n"
            f"【该类型话术知识】\n{context_for_review(r.get('ptype') or '')[:500]}\n\n"
            f"已提交回复：{reply_text[:200]}\n\n"
            "判断：回复是否与问题类型匹配、有无不当承诺、有无遗漏关键信息。\n"
            '只输出：{"verdict":"ok" 或 "flag","reason":"一句话理由"}'
        )
        try:
            from service.reviewer import _call_local_model, _parse_verdict
            content = await _call_local_model(prompt)
            v = _parse_verdict(content)
            # 后置分析输出 ok/flag（与闸门的 pass/human 区分）
            verdict = "ok" if v.get("verdict") in ("pass", "ok") else "flag"
            entry = {"problem_id": r.get("problem_id"), "ts": r.get("ts"),
                     "verdict": verdict, "reason": v.get("reason", "")}
        except Exception as e:
            entry = {"problem_id": r.get("problem_id"), "ts": r.get("ts"),
                     "verdict": "skipped", "reason": f"analysis-failed:{type(e).__name__}"}
        flagged.append(entry)

    flag_count = len([e for e in flagged if e["verdict"] == "flag"])
    # Q4：flag 进止损计数（超 3 条自动降级 semi）
    from service.breaker import record_ai_flag
    for _ in range(flag_count):
        record_ai_flag()
    result = {"reviewed": len(flagged),
              "flagged": [e for e in flagged if e["verdict"] == "flag"],
              "skipped": [e for e in flagged if e["verdict"] == "skipped"],
              "ts": datetime.now().isoformat(timespec="seconds")}
    # 结果落盘（工作台读）
    RETROSPECT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    audit.append("retrospect_run", {"reviewed": result["reviewed"],
                                    "flagged": len(result["flagged"])}, actor="ai-retrospect")
    return {"ok": True, **result}


def latest_retrospect() -> dict:
    """读最近一次后置分析结果（工作台展示）。"""
    if not RETROSPECT_PATH.exists():
        return {"reviewed": 0, "flagged": [], "note": "尚未运行"}
    try:
        return json.loads(RETROSPECT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"reviewed": 0, "flagged": [], "note": "结果损坏"}
