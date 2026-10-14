"""只读入口（局域网）：3 名客服查看用。绑 0.0.0.0:8766，只有只读路由。

Q6 决议：写接口只在 127.0.0.1:8765（装机终端）；本入口不含任何写路由。
review 修复：
- H1：独立进程读不到主 app 内存缓存 → 数据直接从 bridge_cache 文件读（跨进程）
- H2：所有端点加鉴权（只读 token = 访问码）
- M8：不复用主 app lifespan（不起第二个更新轮询——8765 进程已有一个）
"""
import json
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header

from shared import audit, bridge_auth
from service.retrospect import latest_retrospect
from service.breaker import status as breaker_status
from db.metrics import summary as metrics_summary


ro_app = FastAPI(title="问题件助手（只读）")


def _auth(token: str | None) -> None:
    if not bridge_auth.verify(token):
        raise HTTPException(status_code=401, detail="read auth failed")


@ro_app.get("/health")
async def health():
    return {"ok": True, "readonly": True}


@ro_app.get("/problem-parcels")
async def list_parcels(x_read_token: str | None = Header(default=None)):
    _auth(x_read_token)
    # 数据源：CDP 直驱（服务端 Playwright 拉 problemQuery 页）
    from service.registry import fetch_current_parcels
    parcels = await fetch_current_parcels()
    if not parcels:
        raise HTTPException(status_code=502, detail="暂无数据（请确认中通页面已登录并点过查询）")
    items = [json.loads(p.model_dump_json()) for p in parcels]
    return {"total": len(items), "items": items, "scanned_at": datetime.now().isoformat(timespec="seconds")}


@ro_app.get("/auto-reply/status")
async def auto_reply_status(x_read_token: str | None = Header(default=None)):
    _auth(x_read_token)
    from service.auto_reply import get_mode
    mode = get_mode()
    return {"enabled": mode != "off", "mode": mode,
            "note": "敏感类永不自动"}


@ro_app.get("/retrospect/latest")
async def retrospect(x_read_token: str | None = Header(default=None)):
    _auth(x_read_token)
    return latest_retrospect()


@ro_app.get("/breaker/status")
async def breaker(x_read_token: str | None = Header(default=None)):
    _auth(x_read_token)
    return breaker_status()


@ro_app.get("/metrics/hours")
async def metrics(x_read_token: str | None = Header(default=None)):
    _auth(x_read_token)
    return metrics_summary()


@ro_app.get("/audit/log")
async def audit_log(month: str | None = None, x_read_token: str | None = Header(default=None)):
    _auth(x_read_token)
    return audit.verify(month)
