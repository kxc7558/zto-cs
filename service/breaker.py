"""止损（circuit breaker）：设计 §4 的带数字止损阈值写进代码，自动执行。

触发条件（任一即触发）：
- 账号被平台限制/封禁（登录页重定向 + 连续两次 24h 内失效——needs_reauth 频次）
- 单日触发强制验证码 > 3 次（登录页出现频率）
- 收到异地登录/异常告警（预留接口）
- AI 后置抽查 flag > 3 条（话术有问题，自动降级 semi）

触发动作：自动回复切回 semi/off、切只读、落审计、工作台显示。
恢复：人工确认风险解除后手动开（不复启自动化）。
"""
from datetime import datetime, timedelta

from shared import audit
from shared import settings as cfg

STATE_PATH_FLAG = "circuit_breaker.flagged_today"
STATE_PATH_CAPTCHA = "circuit_breaker.captcha_today"
STATE_PATH_DATE = "circuit_breaker.date"
THRESHOLD_FLAG = 3        # AI 抽查 flag 上限（Q4）
THRESHOLD_CAPTCHA = 3     # 单日强制验证码上限（设计 §4）


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _reset_if_new_day() -> None:
    if cfg.get(STATE_PATH_DATE) != _today():
        cfg.set_value(STATE_PATH_DATE, _today())
        cfg.set_value(STATE_PATH_FLAG, 0)
        cfg.set_value(STATE_PATH_CAPTCHA, 0)


def record_ai_flag() -> int:
    """AI 后置抽查 flag +1。返回当日累计。超阈值自动降级。"""
    _reset_if_new_day()
    n = (cfg.get(STATE_PATH_FLAG) or 0) + 1
    cfg.set_value(STATE_PATH_FLAG, n)
    if n >= THRESHOLD_FLAG:
        _trip("ai_flag_threshold",
              f"AI 抽查当日 flag 达 {n} 条（阈值 {THRESHOLD_FLAG}），自动降级 semi")
    return n


def record_captcha() -> int:
    """登录页出现验证码 +1（登录态探活检测到）。返回当日累计。"""
    _reset_if_new_day()
    n = (cfg.get(STATE_PATH_CAPTCHA) or 0) + 1
    cfg.set_value(STATE_PATH_CAPTCHA, n)
    if n >= THRESHOLD_CAPTCHA:
        _trip("captcha_threshold",
              f"单日强制验证码达 {n} 次（阈值 {THRESHOLD_CAPTCHA}），自动切只读")
    return n


def _trip(reason: str, note: str) -> None:
    """触发止损：自动回复停止（enabled=False 即 off，M5 语义对齐）+ 落审计。"""
    cfg.set_value("write.auto_reply.enabled", False)
    cfg.set_value("write.auto_reply.mode", "semi")
    cfg.set_value("circuit_breaker.tripped_at", datetime.now().isoformat(timespec="seconds"))
    audit.append("circuit_breaker_tripped", {"reason": reason, "note": note}, actor="breaker")


def status() -> dict:
    """当前止损状态（工作台显示）。只读：不重置计数（M6：GET 不带副作用）。"""
    if cfg.get(STATE_PATH_DATE) != _today():
        return {"ai_flags_today": 0, "captcha_today": 0,
                "threshold_flag": THRESHOLD_FLAG, "threshold_captcha": THRESHOLD_CAPTCHA,
                "tripped": bool(cfg.get("circuit_breaker.tripped_at"))}
    return {
        "ai_flags_today": cfg.get(STATE_PATH_FLAG) or 0,
        "captcha_today": cfg.get(STATE_PATH_CAPTCHA) or 0,
        "threshold_flag": THRESHOLD_FLAG,
        "threshold_captcha": THRESHOLD_CAPTCHA,
        "tripped": bool(cfg.get("circuit_breaker.tripped_at")),
    }
    return {
        "ai_flags_today": cfg.get(STATE_PATH_FLAG) or 0,
        "captcha_today": cfg.get(STATE_PATH_CAPTCHA) or 0,
        "threshold_flag": THRESHOLD_FLAG,
        "threshold_captcha": THRESHOLD_CAPTCHA,
        "tripped": bool(cfg.get("circuit_breaker.tripped_at")),
    }
