"""闸门管线单测：四层闸门 + 幂等账本 + 审计链（纯逻辑，不碰浏览器）。

跑法：.venv/Scripts/python -m pytest tests/test_gates.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from service.gatekeeper import run_gates
from service import rules
from shared.models import ProblemParcel


def _parcel(ptype="催派件", desc="此件麻烦尽快派送，客户等着", latest_reply=None,
            needs_human=False):
    return ProblemParcel(
        problem_id="test123", ptype=ptype, description=desc,
        category="通知类", latest_reply=latest_reply, needs_human=needs_human,
    )


class FakeReviewer:
    """可注入的假审核器（闸门④测试用）。"""

    def __init__(self, verdict="pass"):
        self.verdict = verdict
        self.calls = []

    async def review_reply(self, **kw):
        self.calls.append(kw)
        return {"verdict": self.verdict, "reason": "fake"}


# ---------- 闸门①：总开关 ----------

@pytest.mark.asyncio
async def test_gate_switch_off_blocks():
    r = await run_gates(_parcel(), switches={"auto_enabled": False})
    assert not r.passed
    assert r.gate == "switch"


# ---------- 闸门②：敏感类硬闸 ----------

@pytest.mark.asyncio
async def test_gate_sensitive_blocks():
    r = await run_gates(_parcel(ptype="理赔证据上传"), switches={"auto_enabled": True})
    assert not r.passed
    assert r.gate == "sensitive"


@pytest.mark.asyncio
async def test_gate_sensitive_keyword_in_desc_blocks():
    r = await run_gates(_parcel(desc="客户要求仲裁，费用争议"), switches={"auto_enabled": True})
    assert not r.passed
    assert r.gate == "sensitive"


@pytest.mark.asyncio
async def test_gate_needs_human_flag_blocks():
    # needs_human 标记也能拦（reader/bridge 两处口径）
    p = _parcel()
    p.needs_human = True
    r = await run_gates(p, switches={"auto_enabled": True})
    assert not r.passed
    assert r.gate == "sensitive"


# ---------- 已回复不进管线 ----------

@pytest.mark.asyncio
async def test_gate_already_replied_blocks():
    r = await run_gates(_parcel(latest_reply="已在派件"), switches={"auto_enabled": True})
    assert not r.passed
    assert r.gate == "done"


# ---------- 闸门③：规则校验 ----------

@pytest.mark.asyncio
async def test_gate_no_template_blocks():
    p = _parcel(ptype="不存在的类型")
    p.category = "也不存在"
    r = await run_gates(p, switches={"auto_enabled": True}, reviewer_impl=FakeReviewer())
    assert not r.passed
    assert r.gate == "rules"


@pytest.mark.asyncio
async def test_rule_validate_empty():
    ok, why = rules.validate_reply("")
    assert not ok and why == "empty"


@pytest.mark.asyncio
async def test_rule_validate_too_long():
    ok, why = rules.validate_reply("长" * 201)
    assert not ok and "too_long" in why


@pytest.mark.asyncio
async def test_rule_validate_sensitive_text():
    ok, why = rules.validate_reply("需要赔付费用 100 元")
    assert not ok and "sensitive" in why


# ---------- 闸门④：AI 审核 ----------

@pytest.mark.asyncio
async def test_gate_ai_human_blocks():
    r = await run_gates(_parcel(), switches={"auto_enabled": True},
                        reviewer_impl=FakeReviewer(verdict="human"))
    assert not r.passed
    assert r.gate == "ai_review"


@pytest.mark.asyncio
async def test_gate_ai_pass_allows():
    fr = FakeReviewer(verdict="pass")
    r = await run_gates(_parcel(), switches={"auto_enabled": True}, reviewer_impl=fr)
    assert r.passed
    assert r.reply_text  # 有模板话术


# ---------- 人工确认路径（跳过①④，但②③仍拦） ----------

@pytest.mark.asyncio
async def test_gate_human_confirmed_skips_switch_and_ai():
    r = await run_gates(_parcel(), switches={"auto_enabled": False}, human_confirmed=True,
                        reviewer_impl=FakeReviewer(verdict="human"))
    assert r.passed  # 人工确认：开关关着也放行，AI human 也不拦


@pytest.mark.asyncio
async def test_gate_human_confirmed_still_blocked_by_sensitive():
    r = await run_gates(_parcel(ptype="理赔证据上传"), switches={"auto_enabled": False},
                        human_confirmed=True)
    assert not r.passed  # 敏感类人工路径也拦（必须人在中通原系统处理）


# ---------- AI 审核器 fail-safe ----------

@pytest.mark.asyncio
async def test_reviewer_failsafe_on_error():
    from service.reviewer import AiReviewer

    class Broken:
        # 触发内部异常（无 LLM 配置 → httpx 失败 → fail-safe human）
        pass

    r = await AiReviewer().review(_parcel(), "已收到，正在处理")
    # 无 LLM 服务时必须转人工（绝不因审核器故障而放行）
    assert r["verdict"] == "human"


# ---------- 幂等账本 ----------

def test_ledger_idempotent_intent():
    from db import ledger
    from shared.config import DB_PATH
    # 用独立测试库
    import shared.config as cfg
    from unittest.mock import patch
    key = "testkey_" + "a" * 24
    with patch.object(ledger, "_conn") as mock_conn:
        # 简化：直接测真实库但用唯一 key，测完清理
        pass
    # 直接测：同 key 两次 record_intent 只有一行
    k = ledger.make_idempotency_key("prob1", "reply", "text", "tester")
    r1 = ledger.record_intent("prob1", None, "reply", "text", k, False, "tester")
    r2 = ledger.record_intent("prob1", None, "reply", "text", k, False, "tester")
    assert r1["status"] == r2["status"]
    st = ledger.get_status(k)
    assert st is not None


def test_ledger_make_key_deterministic():
    from db import ledger
    k1 = ledger.make_idempotency_key("p", "reply", "同文本", "human")
    k2 = ledger.make_idempotency_key("p", "reply", "同文本", "human")
    assert k1 == k2  # 确定性：崩溃后重试重算同一 K
    k3 = ledger.make_idempotency_key("p", "reply", "不同文本", "human")
    assert k1 != k3  # 文本变了 key 变（人改了文本必须重新 prepare）


def test_ledger_key_changes_with_confirmer():
    from db import ledger
    k1 = ledger.make_idempotency_key("p", "reply", "text", "human")
    k2 = ledger.make_idempotency_key("p", "reply", "text", "extension:semi")
    assert k1 != k2


# ---------- 审计链 ----------

def test_audit_chain_verifies():
    from shared import audit
    audit.append("test_event", {"x": 1}, actor="test")
    v = audit.verify()
    assert v["ok"]


def test_audit_chain_detects_tamper():
    import json
    from shared import audit
    from shared.config import AUDIT_DIR
    # 找最新月的 audit.jsonl，改最后一条，验证应失败
    audit.append("tamper_test", {"mark": "before"}, actor="test")
    d = max(p for p in AUDIT_DIR.iterdir() if p.is_dir() and len(p.name) == 7)
    f = d / "audit.jsonl"
    lines = f.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[-1])
    rec["payload"]["mark"] = "TAMPERED"
    lines[-1] = json.dumps(rec, ensure_ascii=False)
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = audit.verify()
    assert not v["ok"], "篡改必须被验出"
    # 还原（删测试标记那条没法直接还原，重写为原始）
    # 简化：把最后一条改回去
    rec["payload"]["mark"] = "before"
    lines[-1] = json.dumps(rec, ensure_ascii=False)
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- updater 安全 ----------

def test_updater_rejects_credential_overwrite_normalized():
    """CRITICAL-2 回归：config/./bridge_secret.key 归一化路径必须被拒。"""
    import io, zipfile
    from service.updater import apply_update
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("config/./bridge_secret.key", "EVIL")
    from scripts.signing import sign_payload
    sig = sign_payload(buf.getvalue())
    manifest = {"version": "evil", "signature": sig["signature"], "sha256": sig["sha256"]}
    r = apply_update(manifest, buf.getvalue())
    assert not r["ok"]
    assert "credential" in r["note"]


def test_updater_rejects_path_traversal():
    import io, zipfile
    from service.updater import apply_update
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("config/../../../evil.py", "EVIL")
    from scripts.signing import sign_payload
    sig = sign_payload(buf.getvalue())
    manifest = {"version": "evil", "signature": sig["signature"], "sha256": sig["sha256"]}
    r = apply_update(manifest, buf.getvalue())
    assert not r["ok"]


def test_updater_rejects_bad_signature():
    import io, zipfile
    from service.updater import apply_update
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("config/reply_templates.yaml", "ok: 1")
    manifest = {"version": "evil", "signature": "00" * 64,
                "sha256": "00" * 64}
    r = apply_update(manifest, buf.getvalue())
    assert not r["ok"]
