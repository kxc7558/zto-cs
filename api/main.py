"""FastAPI 接口层。双监听预留：本机原型先绑 127.0.0.1。

授权分级（§5.1）：只读免签（带 caller）；写操作走 prepare→commit 两阶段。
自动回复开关：write.auto_reply.enabled（工作台可切换，敏感类硬闸不受开关影响）。
"""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from service import problem_reader, rules, auto_reply, replier
from shared import audit
from shared import settings as cfg
from shared.config import DB_PATH

_state: dict = {"reader": None, "cache": None, "cache_ts": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _state["reader"] = problem_reader
    yield


app = FastAPI(title="ZTO 客服智助 · 问题件回复闭环", lifespan=lifespan)

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

@app.get("/")
async def index():
    return FileResponse("api/static/index.html")


# ---------- 只读 ----------

@app.get("/health")
async def health():
    return {
        "ok": True,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "auto_reply_enabled": auto_reply.auto_reply_enabled(),
    }


@app.get("/problem-parcels")
async def list_problem_parcels(force: bool = False):
    """拉「需要我回复的」问题件列表。带 5 分钟缓存（低频、反风控）。"""
    now = datetime.now().timestamp()
    cached = _state.get("cache")
    if cached is not None and not force and (now - (_state.get("cache_ts") or 0)) < 300:
        return cached
    try:
        result = await problem_reader.fetch_problem_list()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"browser read failed: {e}")
    payload = json.loads(result.model_dump_json())
    _state["cache"] = payload
    _state["cache_ts"] = now
    return payload


@app.get("/problem-parcels/{problem_id}")
async def get_problem_parcel(problem_id: str):
    data = _state.get("cache")
    if data is None:
        raise HTTPException(status_code=404, detail="scan first: GET /problem-parcels")
    for item in data["items"]:
        if item["problem_id"] == problem_id:
            return item
    raise HTTPException(status_code=404, detail="not found in latest scan")


@app.get("/audit/log")
async def audit_log(month: str | None = None):
    return audit.verify(month)


# ---------- 自动回复开关 ----------

@app.get("/auto-reply/status")
async def auto_reply_status():
    return {
        "enabled": auto_reply.auto_reply_enabled(),
        "note": "敏感类（仲裁/理赔/责任/费用）永不自动，即使开关开着",
    }


class AutoReplyToggle(BaseModel):
    enabled: bool


@app.post("/auto-reply/toggle")
async def toggle_auto_reply(body: AutoReplyToggle):
    """切换自动回复总开关（工作台页面调用）。落 settings.yaml，审计留痕。"""
    conf = auto_reply.set_auto_reply(body.enabled)
    return {"ok": True, "enabled": body.enabled, "config": conf.get("write")}


@app.post("/auto-reply/run")
async def run_auto_reply_once(max_n: int = 5):
    """手动触发一轮自动回复（总开关开着才跑；限流 max_n 条）。"""
    try:
        return await auto_reply.run_auto_reply(max_n=max_n)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"auto reply failed: {e}")


# ---------- 单条人工回复（两阶段） ----------

class ReplyPrepare(BaseModel):
    problem_id: str
    reply_text: str


@app.post("/reply/prepare")
async def reply_prepare(body: ReplyPrepare):
    """第一步：校验 + 生成幂等键（不提交）。返回预览与 key。"""
    ok, why = rules.validate_reply(body.reply_text)
    if not ok:
        raise HTTPException(status_code=400, detail=f"rule-invalid: {why}")
    parcel = None
    data = _state.get("cache")
    if data:
        parcel = next((i for i in data["items"] if i["problem_id"] == body.problem_id), None)
    if parcel and rules.is_sensitive(parcel.get("ptype") or "", parcel.get("description") or ""):
        raise HTTPException(status_code=403, detail="sensitive type — must be handled manually")
    key = replier.ledger.make_idempotency_key(
        body.problem_id, "reply", body.reply_text, confirmed_by="human")
    return {"ok": True, "idempotency_key": key, "reply_text": body.reply_text,
            "problem_id": body.problem_id}


class ReplyCommit(BaseModel):
    problem_id: str
    waybill_no: str | None = None
    reply_text: str
    idempotency_key: str


@app.post("/reply/commit")
async def reply_commit(body: ReplyCommit):
    """第二步：凭幂等键真实提交 + 回读校验（敏感类在 prepare 已拒；这里再查一次）。"""
    if rules.is_sensitive("", body.reply_text):
        raise HTTPException(status_code=403, detail="sensitive content — blocked")
    try:
        return await replier.commit_reply(
            problem_id=body.problem_id, waybill_no=body.waybill_no,
            reply_text=body.reply_text, confirmed_by="human", auto=False)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"commit failed: {e}")


@app.get("/reply/ledger/{problem_id}")
async def reply_ledger(problem_id: str):
    return replier.ledger.get_by_problem(problem_id)


# ---------- 插件桥接（extension bridge） ----------
# 分工：插件=手（只做页面里的读和写），服务端=脑（决策/审核/留痕）。
# 插件不做任何决策：回什么、能不能回，全由服务端定。

class BridgeIngestList(BaseModel):
    """插件上报的问题件列表（content script 从页面读出的原始行）。"""
    rows: list[dict]
    page_url: str | None = None


@app.post("/bridge/ingest/list")
async def bridge_ingest_list(body: BridgeIngestList):
    """插件上报列表 → 服务端去重建档、跑闸门计划，返回统计。

    数据从插件来（不再走 Playwright），解析逻辑与 problem_reader 对齐。
    """
    from service.bridge import ingest_rows
    result = await ingest_rows(body.rows, body.page_url)
    return result


@app.get("/bridge/tasks/pending")
async def bridge_tasks_pending(limit: int = 5):
    """插件轮询：该回哪条、用什么话术（已过四层闸门：开关/敏感/AI审核/幂等）。"""
    from service.bridge import pending_tasks
    return await pending_tasks(limit=limit)


class BridgeIngestResult(BaseModel):
    """插件回传提交结果。"""
    problem_id: str
    reply_text: str
    submitted: bool
    readback: str | None = None
    mode: str = "semi"          # semi / full
    error: str | None = None


@app.post("/bridge/ingest/result")
async def bridge_ingest_result(body: BridgeIngestResult):
    """插件回传结果 → 服务端落账本、审计、回读校验。"""
    from service.bridge import ingest_result
    return await ingest_result(
        problem_id=body.problem_id, reply_text=body.reply_text,
        submitted=body.submitted, readback=body.readback or "",
        mode=body.mode, error=body.error,
    )


@app.get("/bridge/config")
async def bridge_config():
    """插件拉配置：模式（semi/full）、轮询间隔、敏感词（供插件本地预过滤）。"""
    from shared.config import SENSITIVE_KEYWORDS
    return {
        "mode": cfg.get("write.auto_reply.mode", "semi"),
        "poll_interval_seconds": cfg.get("write.auto_reply.poll_interval_seconds", 60),
        "send_interval_seconds": cfg.get("write.auto_reply.send_interval_seconds", 45),
        "sensitive_keywords": SENSITIVE_KEYWORDS,
        "enabled": auto_reply.auto_reply_enabled(),
    }

