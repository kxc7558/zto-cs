"""插件桥接缓存：最近一次插件上报的问题件列表（供 pending_tasks 取未回复件）。"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from shared.config import ROOT

CACHE_PATH = ROOT / "db" / "bridge_cache.json"
TTL = timedelta(minutes=10)


def save_parcels(items: list[dict]) -> None:
    CACHE_PATH.write_text(
        json.dumps({"ts": datetime.now().isoformat(), "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )


def get_latest_parcels() -> list:
    """取最近上报的 parcels（TTL 内）。过期返回空。"""
    from shared.models import ProblemParcel
    if not CACHE_PATH.exists():
        return []
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data["ts"])
        if datetime.now() - ts > TTL:
            return []
        return [ProblemParcel(**i) for i in data["items"]]
    except Exception:
        return []
