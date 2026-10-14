# ZTO 客服智助 · 问题件回复闭环

> 中通快运一级网点客服智助。范围：**问题件回复闭环**（非资金写），代客下单搁置。
> 设计文档：`d:\AI-Knowledge\10-Projects\中通快运客服智助-CDP方案.md`（§14 实测 / §15 闭环设计）

## 架构定稿（2026-10-13）

摒弃浏览器插件，改**服务端 CDP 直驱**（Playwright 连调试 Chrome 9222）。

闭环链路：
```
数据拉取（自动点查询）
  → 闸门（总开关 + 敏感硬闸 + 规则校验）
  → CDP 填弹窗
  → 工作台「待确认」
  → 人点确认
  → CDP 提交 + 回读（双锚，读不回不算成功）
  → 落账 + 工时
```

## 两个入口

| 入口 | 地址 | 说明 |
|---|---|---|
| 工作台（可写） | `127.0.0.1:8765` | 装机终端；开关 / 待确认 / 确认发送 |
| 只读页（局域网） | `0.0.0.0:8766` | 其他同事查看；无任何写路由 |

两入口都需**访问码**（`config/access_code.txt`，8 位，安装时生成并显示；工作台首屏输入一次）。

## 运行前提

1. 专用调试 Chrome 已启动（CDP 9222，登录态保存在 `%LOCALAPPDATA%\zto-cs\chrome-cdp\zto`）：
   ```
   scripts\启动浏览器.bat
   ```
   首次需在该窗口手动登录（安盾/微信扫码），登录态持久化。
2. **VPN/代理必须关闭**——开着访问中通必 500。
3. Python 3.10+。

## 安装

**客服**：解压管理员给的交付包，双击 `安装.bat`（详见 `安装教程.md`）。

**开发**：
```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```
（连已有调试 Chrome 的 `connect_over_cdp` 不需要 `playwright install chromium`。）

## 一键启动

双击 `scripts\启动服务.bat`：起 8765（本机）+ 8766（局域网），并打开工作台页面。

## 主要接口

| 方法 | 路径 | 鉴权 | 作用 |
|---|---|---|---|
| GET | `/health` | 免签 | 存活探针 |
| GET | `/problem-parcels` | 需访问码 | 拉问题件列表 |
| GET | `/bridge/confirmations` | 需访问码 | 待确认队列 |
| POST | `/bridge/confirm` | 需访问码 | 确认发送（后台 CDP 提交 + 回读） |
| POST | `/auto-reply/toggle` | 需访问码 | 自动回复开关 |
| POST | `/reply/prepare` · `/reply/commit` | 需访问码 | 单条人工回复两阶段 |
| GET | `/audit/log` | 需访问码 | 审计哈希链校验 |

## 交付 / 更新

- **制作交付包**：`python scripts\make_delivery.py <版本>` → `dist/zto-cs-<版本>.zip`
  （白名单打包，自动排除 `.venv/bak/update-server/数据/密钥外敏感物`，含自检）
- **发布更新**：`python scripts\publish.py <版本>`（Ed25519 签名 + 上传家庭服务器；
  客户端验签、只升不降、白名单、凭证不覆盖）

## 硬规则

- 别人的业务数据只读（写路径唯一 = `gatekeeper.run_gates`，人工确认后才提交）
- AI 只做分类/建议，不直接写；敏感类（仲裁/理赔/责任/费用）永不自动
- 凭证/登录态/访问码不进 git、不进日志
- 提交必回读双锚验证；读不回 → 记 `unknown` 转人工，绝不假装成功
