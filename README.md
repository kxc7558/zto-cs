# zto-cs · 问题件回复助手

[![release](https://img.shields.io/github/v/release/kxc7558/zto-cs)](https://github.com/kxc7558/zto-cs/releases)
![python](https://img.shields.io/badge/python-3.10%2B-blue)

> 中通快运一级网点客服的**问题件回复闭环**：拉取 → 闸门 → 填单 → 人工确认 → 提交 + 回读 → 落账。
>
> 服务端 CDP 直驱（Playwright 连调试 Chrome），不依赖浏览器插件；写操作两阶段、
> 提交后双锚回读、全程审计哈希链。

## 解决什么问题

网点客服每天要处理几十上百条问题件：逐条查件号、判断该回什么、在网页弹窗里把回复填进去、
再确认到底提交成功没有。真正费时的不是"怎么回"，而是**重复的点选、核对和事后对账**。

这套工具把「查—判—填—核」串成一条闭环，但**最后一步永远由人点确认**。

## 闭环

```
数据拉取（自动点查询）
  → 闸门（总开关 + 敏感硬闸 + 规则校验）
  → CDP 填弹窗
  → 工作台「待确认」
  → 人点确认
  → CDP 提交 + 回读（双锚，读不回不算成功）
  → 落账 + 工时统计
```

## 设计取舍

| 决定 | 为什么 |
|---|---|
| 服务端 CDP 直驱，不做浏览器插件 | 插件随前端改版就失效；CDP 走真实用户会话，登录态可持久化 |
| 写操作 `prepare → commit` 两阶段 | 准备和提交分离，中间必然经过人的确认，AI 拿不到"直接写"的路径 |
| 提交后**双锚回读** | 读不回就记 `unknown` 转人工，**绝不假装成功** |
| 敏感类硬闸 | 仲裁 / 理赔 / 责任 / 费用类永不自动回复，开关也绕不过 |
| 审计哈希链 | 每条写操作可回溯、可校验是否被篡改 |
| 更新包 Ed25519 签名 | 客户端验签、只升不降、白名单校验，凭证不覆盖 |

## 快速开始

前提：Windows、Python 3.10+、一个专用调试 Chrome。

```bash
python -m venv .venv
.venv\Scripts\pip install -r config/requirements.txt
```

1. `scripts\启动浏览器.bat` —— 起调试 Chrome（CDP 9222）。首次需在该窗口手动登录（安盾 / 微信扫码），登录态持久化。
2. `scripts\启动服务.bat` —— 起 8765（本机工作台）+ 8766（局域网只读），并打开工作台。
3. 首屏输入访问码（`config/access_code.txt`，安装时生成）。

> 连已有调试 Chrome 的 `connect_over_cdp` 不需要 `playwright install chromium`。

## 接口

| 方法 | 路径 | 鉴权 | 作用 |
|---|---|---|---|
| GET | `/health` | 免签 | 存活探针 |
| GET | `/problem-parcels` | 访问码 | 拉问题件列表 |
| GET | `/bridge/confirmations` | 访问码 | 待确认队列 |
| POST | `/bridge/confirm` | 访问码 | 确认发送（后台 CDP 提交 + 回读） |
| POST | `/auto-reply/toggle` | 访问码 | 自动回复总开关 |
| POST | `/reply/prepare` · `/reply/commit` | 访问码 | 单条人工回复两阶段 |
| GET | `/audit/log` | 访问码 | 审计哈希链校验 |

**两个入口**：工作台（`127.0.0.1:8765`，可写）与只读页（局域网，无任何写路由）。

## 目录

| 路径 | 内容 |
|---|---|
| `api/` | FastAPI 接口层（鉴权、两阶段写、自动回复开关） |
| `service/` | 业务：问题件读取、规则、闸门 `gatekeeper`、CDP 驱动、回复、熔断、更新 |
| `shared/` | 配置、模型、审计哈希链、桥接鉴权、更新公钥 |
| `db/` | 台账与指标 |
| `scripts/` | 启动、抓包、打包交付、签名发布 |
| `tests/` | 闸门单测 + 台账 / 确认 / 审计压力脚本 |

## 交付与升级

- **做交付包**：`python scripts\make_delivery.py <版本>` —— 白名单打包，自动排除 `.venv` / `bak` / 数据 / 密钥等敏感物，并跑自检。
- **发更新**：`python scripts\publish.py <版本>` —— Ed25519 签名 + 上传更新服务器；客户端验签、只升不降。

## 边界

- **别人的业务数据只读**。唯一的写路径是 `gatekeeper.run_gates`，且只在该路径的末端、人工确认之后。
- AI 只做分类与建议，不直接写。
- 凭证 / 登录态 / 访问码不进 git、不进日志。
- 提交必回读；读不回 → 转人工，不重试成"成功"。

## License

MIT
