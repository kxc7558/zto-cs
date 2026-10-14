"""工时基线采集（Q9）：问题件从出现到被回复的时长。

为减员承诺定量：统计每个问题件的「首次出现 → 已提交」时长。
数据源：问题件账本（bridge_cache 首见时间）+ 写账本（committed 时间）。
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from shared.config import ROOT

CACHE_PATH = ROOT / "db" / "bridge_cache.json"
DB_PATH = ROOT / "db" / "zto-cs.db"
METRICS_PATH = ROOT / "db" / "metrics.json"

# 首见时间登记（problem_id -> datetime）——SQLite 简单表
_SCHEMA = "CREATE TABLE IF NOT EXISTS first_seen (problem_id TEXT PRIMARY KEY, ts TEXT, ptype TEXT)"


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def record_first_seen(problem_id: str, ptype: str = "") -> None:
    """问题件首次出现时登记（幂等：已存在则跳过）。"""
    now = datetime.now().isoformat(timespec="seconds")
    c = _conn()
    c.execute("INSERT OR IGNORE INTO first_seen (problem_id, ts, ptype) VALUES (?,?,?)",
              (problem_id, now, ptype))
    c.commit()
    c.close()


def record_committed(problem_id: str) -> None:
    """回复提交时算时长，落 metrics.json。"""
    c = _conn()
    row = c.execute("SELECT ts, ptype FROM first_seen WHERE problem_id=?", (problem_id,)).fetchone()
    c.close()
    if not row:
        return
    t0 = datetime.fromisoformat(row["ts"])
    minutes = round((datetime.now() - t0).total_seconds() / 60, 1)
    # 追加到 metrics
    data = []
    if METRICS_PATH.exists():
        try:
            data = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = []
    data.append({"problem_id": problem_id, "ptype": row["ptype"],
                 "minutes": minutes, "ts": datetime.now().isoformat(timespec="seconds")})
    # M7：原子写（临时文件 + rename，防并发写坏）
    tmp = METRICS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data[-500:], ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(METRICS_PATH)


def summary() -> dict:
    """工时基线汇总：均值/中位数/条数（按类型分）。"""
    if not METRICS_PATH.exists():
        return {"samples": 0, "note": "暂无数据（等真实回复积累）"}
    data = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    if not data:
        return {"samples": 0}
    minutes = sorted(d["minutes"] for d in data)
    n = len(minutes)
    by_type = {}
    for d in data:
        by_type.setdefault(d.get("ptype", "?"), []).append(d["minutes"])
    return {
        "samples": n,
        "median_minutes": minutes[n // 2],
        "mean_minutes": round(sum(minutes) / n, 1),
        "by_type": {k: {"count": len(v), "median": sorted(v)[len(v)//2]} for k, v in by_type.items()},
    }
