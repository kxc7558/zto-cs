"""审计哈希链（防篡改）。每条记录带前一条的哈希；按月分屉、自动轮转。"""
import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path

from shared.config import AUDIT_DIR

_lock = threading.Lock()


def _month_dir(now: datetime) -> Path:
    d = AUDIT_DIR / now.strftime("%Y-%m")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _last_hash(month_dir: Path) -> str:
    """读本月链尾哈希。链尾校验值另存 sentinel 文件（防整链替换后无人察觉）。"""
    sentinel = month_dir / ".chain_tail"
    if sentinel.exists():
        return sentinel.read_text(encoding="utf-8").strip()
    return "GENESIS"


def append(event: str, payload: dict, actor: str = "system") -> dict:
    """追加一条审计记录：谁、何时、干了什么、带前一条哈希。"""
    now = datetime.now()
    d = _month_dir(now)
    prev = _last_hash(d)
    record = {
        "ts": now.isoformat(timespec="seconds"),
        "actor": actor,
        "event": event,
        "payload": payload,
        "prev_hash": prev,
    }
    body = json.dumps(record, ensure_ascii=False, sort_keys=True)
    record["hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    line = json.dumps(record, ensure_ascii=False)
    (d / "audit.jsonl").open("a", encoding="utf-8").write(line + "\n")
    (d / ".chain_tail").write_text(record["hash"], encoding="utf-8")
    return record


def verify(month: str | None = None) -> dict:
    """验证哈希链完整性：改一条，后面全对不上。

    默认月 = 按目录名（YYYY-MM 格式）取最新，不按字典序（避免 2 月 > 10 月 的字典序错位）。
    """
    if month:
        d = AUDIT_DIR / month
    else:
        candidates = [p for p in AUDIT_DIR.iterdir() if p.is_dir() and len(p.name) == 7 and p.name[4] == "-"]
        d = max(candidates, key=lambda p: p.name, default=AUDIT_DIR)
    f = d / "audit.jsonl"
    if not f.exists():
        return {"ok": True, "records": 0, "note": "no records"}
    prev = "GENESIS"
    n = 0
    for line in f.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        body = {k: v for k, v in rec.items() if k != "hash"}
        expect = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if rec["prev_hash"] != prev or rec["hash"] != expect:
            return {"ok": False, "records": n, "broken_at": n, "month": d.name}
        prev = rec["hash"]
        n += 1
    # 链尾 sentinel 交叉校验（review LOW：截断/删月要能验出）
    sentinel = d / ".chain_tail"
    if sentinel.exists() and sentinel.read_text(encoding="utf-8").strip() != prev:
        return {"ok": False, "records": n, "month": d.name, "note": "chain truncated vs sentinel"}
    return {"ok": True, "records": n, "month": d.name}
