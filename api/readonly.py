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
from fastapi.responses import FileResponse

from shared import audit, bridge_auth
from shared.config import ROOT
from service.retrospect import latest_retrospect
from service.breaker import status as breaker_status
from db.metrics import summary as metrics_summary


ro_app = FastAPI(title="问题件助手（只读）")

_INDEX_HTML = ROOT / "api" / "static" / "index.html"


def _auth(token: str | None) -> None:
    if not bridge_auth.verify(token):
        raise HTTPException(status_code=401, detail="read auth failed")


@ro_app.get("/")
async def index():
    """只读页入口（前端据 /health.readonly 自行隐藏写开关）。"""
    return FileResponse(_INDEX_HTML)


@ro_app.get("/health")
async def health():
    return {"ok": True, "readonly": True}


@ro_app.get("/problem-parcels")
async def list_parcels(x_read_token: str | None = Header(default=None)):
    _auth(x_read_token)
    # 数据源：CDP 直驱（服务端 Playwright 拉 problemQuery 页）
    from service.registry import fetch_current_parcels
    try:
        parcels = await fetch_current_parcels()
    except Exception as e:
        from service.problem_reader import SessionExpired
        if isinstance(e, SessionExpired) or "会话失效" in str(e):
            # 触发 breaker（工作台显示"中通登录已过期"）
            from service.breaker import record_captcha
            record_captcha()
            raise HTTPException(status_code=440,
                detail="中通登录已过期——请双击 启动浏览器.bat 重新登录中通")
        # CDP 不可达（浏览器没启动）→ 可操作提示，而非不透明 500
        msg = str(e)
        low = msg.lower()
        if ("econnrefused" in low or "connect" in low or "9222" in low
                or "playwright" in low or "browser" in low):
            raise HTTPException(status_code=503,
                detail="浏览器未启动——请双击 启动浏览器.bat（或确认调试 Chrome 已开在 9222）")
        raise HTTPException(status_code=502, detail=f"数据源不可用：{msg[:120]}")
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
