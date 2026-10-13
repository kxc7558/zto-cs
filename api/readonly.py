"""只读入口（局域网）：3 名客服查看用。绑 0.0.0.0:8766，只有只读路由。

Q6 决议：写接口只在 127.0.0.1:8765（装机终端）；本入口不含任何写路由。
访问控制：只读 token（与桥接密钥同一把）。
"""
import json
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header

from shared import audit, bridge_auth
from service.retrospect import latest_retrospect
from service.breaker import status as breaker_status
from api.main import lifespan, _state


ro_app = FastAPI(title="问题件助手（只读）", lifespan=lifespan)


def _auth(token: str | None) -> None:
    if not bridge_auth.verify(token):
        raise HTTPException(status_code=401, detail="read auth failed")


@ro_app.get("/health")
async def health():
    return {"ok": True, "readonly": True}


@ro_app.get("/problem-parcels")
async def list_parcels(force: bool = False, x_read_token: str | None = Header(default=None)):
    _auth(x_read_token)
    data = _state.get("cache")
    if data is None:
        raise HTTPException(status_code=502, detail="no data yet — 等数据采集（约1分钟）")
    return data


@ro_app.get("/retrospect/latest")
async def retrospect():
    _read = None  # 只读端点免 token 细项（列表本身无敏感写操作）
    return latest_retrospect()


@ro_app.get("/breaker/status")
async def breaker():
    return breaker_status()


@ro_app.get("/audit/log")
async def audit_log(month: str | None = None):
    return audit.verify(month)
