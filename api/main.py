"""FastAPI 接口层。双监听预留：本机原型先绑 127.0.0.1。

授权分级（§5.1）：只读免签（带 caller）；写操作走 prepare→commit 两阶段。
自动回复开关：write.auto_reply.enabled（工作台可切换，敏感类硬闸不受开关影响）。
"""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header

from shared import bridge_auth


def _auth(token: str | None) -> None:
    """桥接鉴权：共享密钥校验（X-Bridge-Token）。"""
    if not bridge_auth.verify(token):
        raise HTTPException(status_code=401, detail="bridge auth failed")
from pydantic import BaseModel

from service import problem_reader, rules, auto_reply, replier
from shared import audit
from shared import settings as cfg
from shared.models import ParcelList as PParcelList
from shared.config import DB_PATH

_state: dict = {"reader": None, "cache": None, "cache_ts": None}


def _is_session_expired(e: Exception) -> bool:
    """会话失效判定（异常类型 + 中文文案双保险）。"""
    from service.problem_reader import SessionExpired
    return isinstance(e, SessionExpired) or "会话失效" in str(e)


def _session_expired_breaker() -> None:
    """会话失效：记一次（触发止损），供端点抛 440。"""
    from service.breaker import record_captcha
    record_captcha()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _state["reader"] = problem_reader
    # 崩溃/重启恢复：把遗留的 committing 行核销为 unknown（否则永久卡死，见 bridge 对账）
    try:
        from db import ledger as _ledger
        stale = _ledger.reconcile_committing()
        for s in stale:
            audit.append("reconcile_committing", {"row_id": s["id"],
                         "problem_id": s["problem_id"]}, actor="startup")
    except Exception:
        pass
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
    # 关停：连同后台提交任务一起取消（否则退出时在途提交被丢下，行卡 committing）
    tasks = [_state.get(k) for k in ("update_task", "retrospect_task")]
    tasks += list(_state.get("bg_tasks", []))
    for task in tasks:
        if task:
            task.cancel()
    for task in tasks:
        if task:
            try:
                await task
            except (asyncio.CancelledError, Exception):
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
async def list_problem_parcels(force: bool = False,
                               x_bridge_token: str | None = Header(default=None)):
    """拉问题件列表（带 5 分钟缓存）。缓存空/过期时走 CDP reader。"""
    _auth(x_bridge_token)
    now = datetime.now().timestamp()
    cached = _state.get("cache")
    if cached is not None and not force and (now - (_state.get("cache_ts") or 0)) < 300:
        return cached
    # 单飞（single-flight）：冷缓存/过期后 N 个并发只打一次真实中通查询（反风控）
    lock = _state.setdefault("fetch_lock", asyncio.Lock())
    async with lock:
        now = datetime.now().timestamp()
        cached = _state.get("cache")
        if cached is not None and not force and (now - (_state.get("cache_ts") or 0)) < 300:
            return cached
        # 主数据源：CDP reader（需调试 Chrome 在 9222）
        try:
            result = await problem_reader.fetch_problem_list()
        except Exception as e:
            if _is_session_expired(e):
                _session_expired_breaker()
                raise HTTPException(status_code=440,
                    detail="中通登录已过期——请双击 启动浏览器.bat 重新登录中通")
            # 兜底（booter）：CDP 不可用时退回 bridge_cache（最近一次成功抓取的快照，TTL 内）
            from service.bridge_cache import get_latest_parcels
            fallback = get_latest_parcels()
            if fallback:
                payload = json.loads(PParcelList(items=fallback, total=len(fallback),
                                     scanned_at=datetime.now()).model_dump_json())
                _state["cache"] = payload
                _state["cache_ts"] = now
                return payload
            raise HTTPException(status_code=502,
                detail="no data: CDP reader unavailable (login the ZTO problemQuery page first)")
        payload = json.loads(result.model_dump_json())
        _state["cache"] = payload
        _state["cache_ts"] = now
        # 落 bridge_cache：供只读端兜底与跨进程取数（否则该兜底分支永远是死代码）
        try:
            from service.bridge_cache import save_parcels
            save_parcels([i.model_dump() for i in result.items])
        except Exception:
            pass
        return payload


@app.get("/problem-parcels/{problem_id}")
async def get_problem_parcel(problem_id: str,
                             x_bridge_token: str | None = Header(default=None)):
    _auth(x_bridge_token)
    data = _state.get("cache")
    if data is None:
        raise HTTPException(status_code=404, detail="scan first: GET /problem-parcels")
    for item in data["items"]:
        if item["problem_id"] == problem_id:
            return item
    raise HTTPException(status_code=404, detail="not found in latest scan")


@app.get("/audit/log")
async def audit_log(month: str | None = None,
                    x_bridge_token: str | None = Header(default=None)):
    _auth(x_bridge_token)
    return audit.verify(month)


# ---------- 自动回复开关 ----------

@app.get("/auto-reply/status")
async def auto_reply_status(x_bridge_token: str | None = Header(default=None)):
    _auth(x_bridge_token)
    mode = auto_reply.get_mode()
    return {
        "enabled": mode != "off",
        "mode": mode,
        "note": "敏感类（仲裁/理赔/责任/费用）永不自动，即使开关开着",
    }


class AutoReplyToggle(BaseModel):
    enabled: bool


@app.post("/auto-reply/toggle")
async def toggle_auto_reply(body: AutoReplyToggle,
                            x_bridge_token: str | None = Header(default=None)):
    """切换自动回复（工作台页面调用）：false=off，true=semi。落 settings.yaml，审计留痕。"""
    _auth(x_bridge_token)
    mode = "semi" if body.enabled else "off"
    conf = auto_reply.set_mode(mode)
    return {"ok": True, "enabled": conf["enabled"], "mode": conf["mode"]}


@app.post("/auto-reply/run")
async def run_auto_reply_once(max_n: int = 5,
                              x_bridge_token: str | None = Header(default=None)):
    """手动触发一轮自动回复（总开关开着才跑；限流 max_n 条）。"""
    _auth(x_bridge_token)
    try:
        return await asyncio.wait_for(auto_reply.run_auto_reply(max_n=max_n), timeout=600)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="auto reply timed out")
    except Exception as e:
        if _is_session_expired(e):
            _session_expired_breaker()
            raise HTTPException(status_code=440,
                detail="中通登录已过期——请双击 启动浏览器.bat 重新登录中通")
        raise HTTPException(status_code=502, detail=f"auto reply failed: {e}")


# ---------- 单条人工回复（两阶段） ----------

class ReplyPrepare(BaseModel):
    problem_id: str
    reply_text: str


@app.post("/reply/prepare")
async def reply_prepare(body: ReplyPrepare,
                        x_bridge_token: str | None = Header(default=None)):
    """第一步：闸门校验 + 生成幂等键（不提交）。

    人工路径：human_confirmed=True（人工已看预览）。
    敏感件在闸门②被拒（必须人在中通原系统处理）。
    """
    _auth(x_bridge_token)
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
async def reply_commit(body: ReplyCommit,
                       x_bridge_token: str | None = Header(default=None)):
    """第二步：凭 prepare 签发的幂等键真实提交。

    闸门在 prepare 已过；commit 只认 prepare 的 key（文本不一致 → key 对不上 → 拒），
    敏感词 + 规则（空/长度）再查一遍（防御纵深：人工可能改过文本）。
    """
    _auth(x_bridge_token)
    if rules.is_sensitive("", body.reply_text):
        raise HTTPException(status_code=403, detail="sensitive content — blocked")
    ok, why = rules.validate_reply(body.reply_text)
    if not ok:
        raise HTTPException(status_code=403, detail=f"rule:{why}")
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
async def reply_ledger(problem_id: str,
                       x_bridge_token: str | None = Header(default=None)):
    _auth(x_bridge_token)
    return replier.ledger.get_by_problem(problem_id)


# ---------- 桥接（CDP 直驱确认流） ----------
# 分工：服务端=脑（决策/审核/留痕）；执行=CDP（填弹窗/提交/回读）。
# 任何一步都不由模型自行决定：回什么、能不能回，全由闸门定。


@app.get("/bridge/confirmations")
async def bridge_confirmations(x_bridge_token: str | None = Header(default=None)):
    """待确认队列（工作台展示：已填好、等人工确认的回复）。"""
    _auth(x_bridge_token)
    from service.bridge import pending_confirmations
    return {"items": pending_confirmations()}


class BridgeConfirm(BaseModel):
    problem_id: str
    row_id: str | None = None   # 精确到具体账本行（同 problem_id 可能多行）


@app.post("/bridge/confirm")
async def bridge_confirm(body: BridgeConfirm, x_bridge_token: str | None = Header(default=None)):
    """工作台确认发送：认领具体行 → committing，后台 CDP 提交（前端轮询账本状态）。

    认领是同步原子操作（快）：认领成功才派后台任务，并把真实结果回传；
    认领失败（已被别人认领/无该行/多行需指定 row_id）如实回 {ok:false}，不再一律 200 假成功。
    """
    _auth(x_bridge_token)
    from service.bridge import claim_row, dispatch_row
    import asyncio
    claim = claim_row(body.problem_id, confirmed_by="human:workbench", row_id=body.row_id)
    if not claim.get("ok"):
        return {"ok": False, "note": claim.get("note")}
    # CDP 提交是长任务（可能 30s+）——后台执行，HTTP 立即返回（前端轮询账本状态）
    task = asyncio.create_task(dispatch_row(claim))
    _state.setdefault("bg_tasks", []).append(task)  # 持引用防 GC

    def _done(t):
        # 取回异常（否则 "Task exception was never retrieved"）；移除引用
        if t.cancelled():
            pass
        elif t.exception():
            audit.append("bridge_confirm_task_error",
                         {"error": str(t.exception())[:160]}, actor="human:workbench")
        if t in _state.get("bg_tasks", []):
            _state["bg_tasks"].remove(t)

    task.add_done_callback(_done)
    return {"ok": True, "row_id": claim["rid"],
            "note": "submitting in background — 刷新列表查看结果"}


@app.get("/bridge/history")
async def bridge_history(x_bridge_token: str | None = Header(default=None)):
    """留存数据：全部写账本记录（工作台查询校对用）。"""
    _auth(x_bridge_token)
    import sqlite3
    from shared.config import DB_PATH
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT * FROM write_ledger ORDER BY intent_ts DESC LIMIT 200").fetchall()
    c.close()
    return {"items": [dict(r) for r in rows]}


@app.get("/bridge/config")
async def bridge_config(x_bridge_token: str | None = Header(default=None)):
    """返回运行配置：模式（semi/full）、轮询间隔、敏感词。"""
    _auth(x_bridge_token)
    from shared.config import SENSITIVE_KEYWORDS
    mode = auto_reply.get_mode()
    return {
        "mode": mode,                       # off / semi / full（唯一真源，避免与下面重复键互相覆盖）
        "source_mode": cfg.get("write.source_mode", "cdp"),
        "poll_interval_seconds": cfg.get("write.auto_reply.poll_interval_seconds", 60),
        "send_interval_seconds": cfg.get("write.auto_reply.send_interval_seconds", 45),
        "sensitive_keywords": SENSITIVE_KEYWORDS,
        "enabled": mode != "off",
    }


@app.post("/retrospect/run")
async def retrospect_run(x_bridge_token: str | None = Header(default=None)):
    """手动触发一轮 AI 后置分析（工作台调用）。"""
    _auth(x_bridge_token)
    from service.retrospect import run_retrospect
    try:
        return await run_retrospect()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"retrospect failed: {e}")


@app.get("/retrospect/latest")
async def retrospect_latest(x_bridge_token: str | None = Header(default=None)):
    """读最近一次后置分析结果（工作台展示）。"""
    _auth(x_bridge_token)
    from service.retrospect import latest_retrospect
    return latest_retrospect()


@app.get("/metrics/hours")
async def metrics_hours(x_bridge_token: str | None = Header(default=None)):
    """工时基线汇总（Q9：为减员承诺定量）。"""
    _auth(x_bridge_token)
    from db.metrics import summary
    return summary()


@app.get("/breaker/status")
async def breaker_status(x_bridge_token: str | None = Header(default=None)):
    """止损状态（工作台显示：今日 AI flag 数/验证码次数/是否触发过）。"""
    _auth(x_bridge_token)
    from service.breaker import status
    return status()


@app.get("/bridge/fingerprint")
async def bridge_fingerprint(x_bridge_token: str | None = Header(default=None)):
    """访问码指纹（不是访问码本身，供人工核对两边配的是同一把）。"""
    _auth(x_bridge_token)
    return {"fingerprint": bridge_auth.key_fingerprint()}

