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
from shared.models import ParcelList as PParcelList
from shared.config import DB_PATH

_state: dict = {"reader": None, "cache": None, "cache_ts": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _state["reader"] = problem_reader
    # AI 后置分析（每小时批量审已提交回复，只报告不动数据）
    import asyncio

    async def retrospect_poller():
        from service.retrospect import run_retrospect
        while True:
            await asyncio.sleep(3600)
            try:
                await run_retrospect()
            except Exception:
                pass

    _state["retrospect_task"] = asyncio.create_task(retrospect_poller())

    # 远程更新轮询（后台任务，10 分钟一次；服务器不可达静默跳过）
    async def update_poller():
        from service.updater import poll_and_apply
        from shared import audit as _a
        while True:
            await asyncio.sleep(600)
            try:
                # poll_and_apply 是同步的（httpx.get）——放线程池防阻塞事件循环
                r = await asyncio.to_thread(poll_and_apply)
                if r.get("ok") and r.get("needs_restart"):
                    _a.append("update_needs_restart", r, actor="updater")
            except Exception as e:
                _a.append("update_poll_error", {"error": str(e)[:120]}, actor="updater")

    _state["update_task"] = asyncio.create_task(update_poller())
    yield
    for k in ("update_task", "retrospect_task"):
        task = _state.get(k)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


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
        "auto_reply_enabled": auto_reply.get_mode() != "off",
        "mode": auto_reply.get_mode(),
    }


@app.get("/problem-parcels")
async def list_problem_parcels(force: bool = False):
    """拉问题件列表（review HIGH-4：优先插件桥接缓存——插件上报的数据；
    缓存为空/过期时兜底走 CDP reader）。带 5 分钟缓存。"""
    now = datetime.now().timestamp()
    cached = _state.get("cache")
    if cached is not None and not force and (now - (_state.get("cache_ts") or 0)) < 300:
        return cached
    # 优先：插件桥接缓存（extension 部署的实时数据源）
    from service.bridge_cache import get_latest_parcels
    parcels = get_latest_parcels()
    if parcels:
        payload = json.loads(PParcelList(items=parcels, total=len(parcels),
                             scanned_at=datetime.now()).model_dump_json())
        _state["cache"] = payload
        _state["cache_ts"] = now
        return payload
    # 兜底：CDP reader（需调试 Chrome 在 9222）
    try:
        result = await problem_reader.fetch_problem_list()
    except Exception as e:
        raise HTTPException(status_code=502,
            detail="no data: extension not reporting and CDP reader unavailable (login the ZTO page first)")
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
    mode = auto_reply.get_mode()
    return {
        "enabled": mode != "off",
        "mode": mode,
        "note": "敏感类（仲裁/理赔/责任/费用）永不自动，即使开关开着",
    }


class AutoReplyToggle(BaseModel):
    enabled: bool


@app.post("/auto-reply/toggle")
async def toggle_auto_reply(body: AutoReplyToggle):
    """切换自动回复（工作台页面调用）：false=off，true=semi。落 settings.yaml，审计留痕。"""
    mode = "semi" if body.enabled else "off"
    conf = auto_reply.set_mode(mode)
    return {"ok": True, "enabled": conf["enabled"], "mode": conf["mode"]}


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
    """第一步：闸门校验 + 生成幂等键（不提交）。

    人工路径：human_confirmed=True（人工已看预览）。
    敏感件在闸门②被拒（必须人在中通原系统处理）。
    """
    parcel = None
    data = _state.get("cache")
    if data:
        parcel = next((i for i in data["items"] if i["problem_id"] == body.problem_id), None)
    if parcel is None:
        # 工作台/prepare 用缓存；无缓存时从 bridge 缓存兜底
        from service.bridge_cache import get_latest_parcels
        for pp in get_latest_parcels():
            if pp.problem_id == body.problem_id:
                parcel = pp.model_dump()
                break
    if parcel is None:
        raise HTTPException(status_code=404, detail="problem not in latest scan — scan first")
    from service.gatekeeper import run_gates
    gate = await run_gates(parcel, switches={"auto_enabled": False}, human_confirmed=True)
    if not gate.passed:
        raise HTTPException(status_code=403, detail=f"gate:{gate.gate}: {gate.reason}")
    # 绑定人工编辑后的文本（prepare 冻结的就是最终提交文本）
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
    """第二步：凭 prepare 签发的幂等键真实提交。

    闸门在 prepare 已过；commit 只认 prepare 的 key（文本不一致 → key 对不上 → 拒），
    敏感词再查一次（防御纵深）。
    """
    if rules.is_sensitive("", body.reply_text):
        raise HTTPException(status_code=403, detail="sensitive content — blocked")
    # 校验 key 与文本绑定（prepare 冻结的就是这个文本；不一致 = 没走过 prepare）
    expect_key = replier.ledger.make_idempotency_key(
        body.problem_id, "reply", body.reply_text, confirmed_by="human")
    if body.idempotency_key != expect_key:
        raise HTTPException(status_code=403, detail="idempotency key mismatch — must call /reply/prepare first")
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


# 鉴权：共享密钥（X-Bridge-Token），本机 127.0.0.1 + 密钥双保险
from shared import bridge_auth
from fastapi import Header


def _auth(token: str | None) -> None:
    if not bridge_auth.verify(token):
        raise HTTPException(status_code=401, detail="bridge auth failed")


@app.post("/bridge/ingest/list")
async def bridge_ingest_list(body: BridgeIngestList, x_bridge_token: str | None = Header(default=None)):
    """插件上报列表 → 服务端去重建档、跑闸门计划，返回统计。"""
    _auth(x_bridge_token)
    from service.bridge import ingest_rows
    result = await ingest_rows(body.rows, body.page_url)
    return result


@app.get("/bridge/tasks/pending")
async def bridge_tasks_pending(limit: int = 5, x_bridge_token: str | None = Header(default=None)):
    """插件轮询：该回哪条、用什么话术（已过四层闸门）。下发时落意图账本。"""
    _auth(x_bridge_token)
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
async def bridge_ingest_result(body: BridgeIngestResult, x_bridge_token: str | None = Header(default=None)):
    """插件回传结果 → 服务端落账本、审计、回读校验。"""
    _auth(x_bridge_token)
    from service.bridge import ingest_result
    return await ingest_result(
        problem_id=body.problem_id, reply_text=body.reply_text,
        submitted=body.submitted, readback=body.readback or "",
        mode=body.mode, error=body.error,
    )


@app.get("/bridge/config")
async def bridge_config(x_bridge_token: str | None = Header(default=None)):
    """插件拉配置：模式（semi/full）、轮询间隔、敏感词（供插件本地预过滤）。"""
    _auth(x_bridge_token)
    from shared.config import SENSITIVE_KEYWORDS
    return {
        "mode": cfg.get("write.auto_reply.mode", "semi"),
        "source_mode": cfg.get("write.source_mode", "extension"),
        "poll_interval_seconds": cfg.get("write.auto_reply.poll_interval_seconds", 60),
        "send_interval_seconds": cfg.get("write.auto_reply.send_interval_seconds", 45),
        "sensitive_keywords": SENSITIVE_KEYWORDS,
        "enabled": auto_reply.get_mode() != "off",
        "mode": auto_reply.get_mode(),
    }


@app.post("/retrospect/run")
async def retrospect_run():
    """手动触发一轮 AI 后置分析（工作台调用）。"""
    from service.retrospect import run_retrospect
    try:
        return await run_retrospect()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"retrospect failed: {e}")


@app.get("/retrospect/latest")
async def retrospect_latest():
    """读最近一次后置分析结果（工作台展示）。"""
    from service.retrospect import latest_retrospect
    return latest_retrospect()


@app.get("/metrics/hours")
async def metrics_hours():
    """工时基线汇总（Q9：为减员承诺定量）。"""
    from db.metrics import summary
    return summary()


@app.get("/breaker/status")
async def breaker_status():
    """止损状态（工作台显示：今日 AI flag 数/验证码次数/是否触发过）。"""
    from service.breaker import status
    return status()


@app.get("/bridge/fingerprint")
async def bridge_fingerprint():
    """插件配置页展示的密钥指纹（不是密钥本身，供人工核对两边配的是同一把）。"""
    return {"fingerprint": bridge_auth.key_fingerprint()}

