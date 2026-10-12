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
SENSITIVE_KEYWORDS = ["仲裁", "理赔", "罚款", "责任", "费用", "申诉"]  # 转人工关键词

# 远程更新（家庭服务器）：装的时候可改成局域网地址或 Tailscale 地址
UPDATE_SERVER_URL = "http://100.104.230.47:8900"  # homeserver (Tailscale)

# AI 审核模型（本机，机密数据不出边界）
# LLM_BASE_URL：OpenAI 格式网关（如 key-pool）；留空则用 LOCAL_RELAY_URL（Anthropic 格式中转站）
LLM_BASE_URL = ""            # 例：http://127.0.0.1:8787/v1
LLM_MODEL = "claude-sonnet-5"
LOCAL_RELAY_URL = "http://127.0.0.1:15721"  # 本机中转站（Anthropic 格式）

# 数据抽屉（硬偏好：分类抽屉，避免堆积）
AUDIT_DIR = ROOT / "audit"           # 审计按 YYYY-MM 分屉
LOG_DIR = ROOT / "logs"              # 日志按月轮转
DB_PATH = ROOT / "db" / "zto-cs.db"  # 幂等账本 + 问题件账本（阶段一只用问题件）

for d in (AUDIT_DIR, LOG_DIR, DB_PATH.parent):
    d.mkdir(parents=True, exist_ok=True)
