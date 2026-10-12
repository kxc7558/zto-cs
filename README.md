# ZTO 客服智助 · 问题件回复闭环（最小原型）

> 中通快运一级网点客服智助。当前范围：**问题件回复闭环**（非资金写），代客下单搁置。
> 设计文档：`d:\AI-Knowledge\10-Projects\中通快运客服智助-CDP方案.md`（§14 实测 / §15 闭环设计）

## 当前阶段：阶段一（只读）

**只做**：自动拉「需要我回复的」问题件列表 → 摆给人工看。**零写操作。**

## 运行前提

1. 专用调试 Chrome 已启动（CDP 端口 9222，登录态在 `C:\Users\32492\chrome-cdp\zto`）：
   ```
   scripts\启动浏览器.bat
   ```
   首次需在该窗口手动登录（安盾/微信扫码），登录态持久化。
2. **VPN 必须关闭**——开着访问中通必 500。
3. Python 3.13 + 依赖（见下方安装）。

## 安装

```bash
python -m venv .venv
.venv\Scripts\pip install playwright httpx
.venv\Scripts\playwright install chromium
```

## 一键启动

双击 `scripts\启动服务.bat`：起 FastAPI（127.0.0.1:8765）+ 自动打开工作台页面。

## 接口（阶段一，全只读）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 存活探针（进程+浏览器+登录态） |
| GET | `/problem-parcels` | 拉「需要我回复的」问题件列表（实时从 problemQuery 页读取） |
| GET | `/problem-parcels/{id}` | 单条详情 |
| GET | `/audit/log` | 审计记录（哈希链） |

## 写操作（未启用）

`reply/prepare`、`reply/commit`、`corrections/*` 按 §15 设计实现，但**默认关闭**（config/settings.yaml 里 `write.enabled: false`）。开启前置：§15.8 待核清单实测完成 + 二级网点授权书 + 人签字授权。

## 硬规则

- 别人的业务数据只读（未开写闸门前，程序绝不提交任何回复）
- AI 只做分类/建议，不直接写
- 凭证/登录态不进 git、不进日志、不明文
- 写操作（若开）：人工确认 + 一次性令牌 + 留痕 + 可回滚
