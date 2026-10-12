"""共享配置与常量。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CDP_WS_URL = "http://127.0.0.1:9222"
PROBLEM_QUERY_URL = (
    "https://datahub-portlet.zto56.com/single/problemQuery.html?v=1.0&action=switch"
)
# 摸结构时实测到的鉴权入口；userAcct/siteId 来自网点账号，运行时从 problemQuery 页取
WS_HOST = "https://ws.zto56.com"

# 反风控基线（§15 / 设计 §4）
SCAN_INTERVAL_SECONDS = 300          # 低频轮询：5 分钟
REQUEST_DELAY_SECONDS = (2, 6)       # 请求间随机延迟区间
SENitive_KEYWORDS = ["仲裁", "理赔", "罚款", "责任", "费用", "申诉"]  # 转人工关键词

# 数据抽屉（硬偏好：分类抽屉，避免堆积）
AUDIT_DIR = ROOT / "audit"           # 审计按 YYYY-MM 分屉
LOG_DIR = ROOT / "logs"              # 日志按月轮转
DB_PATH = ROOT / "db" / "zto-cs.db"  # 幂等账本 + 问题件账本（阶段一只用问题件）

for d in (AUDIT_DIR, LOG_DIR, DB_PATH.parent):
    d.mkdir(parents=True, exist_ok=True)
