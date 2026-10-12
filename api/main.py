"""FastAPI 接口层（阶段一：全只读）。双监听：只读绑 127.0.0.1（单机原型先只本机）。
写端点（reply/commit 等）按 §15 设计，默认不挂载（write.enabled=false）。
"""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException

from service import problem_reader
from shared import audit
from shared.config import DB_PATH

_state: dict = {"reader": None, "cache": None, "cache_ts": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _state["reader"] = problem_reader
    yield
    # shutdown: nothing persistent to close (reader sessions are per-request)


app = FastAPI(title="ZTO 客服智助 · 问题件回复闭环（阶段一只读）", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "ok": True,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "stage": "read-only",
        "write_enabled": False,
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
