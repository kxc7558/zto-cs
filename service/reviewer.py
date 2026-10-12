"""AI 审核器（本机模型）：提交前审一遍"这条回复对这个问题件合不合适"。

AI 只审不写：输出 放行 / 转人工 + 理由。最终写仍走受控提交（replier）。
机密业务数据不出边界：模型跑本机（key-pool 本地网关），不经过第三方。

模型调用失败时 fail-safe：一律转人工（绝不因审核器故障而自动放行）。
"""
import json

import httpx

from shared import audit


async def review_reply(ptype: str, description: str, reply_text: str,
                       outlet_from: str = "", outlet_notify: str = "") -> dict:
    """审一条自动回复。返回 {verdict: pass|human, reason: str}。

    fail-safe：任何异常 → 转人工。
    模型边界：只走本机中转（127.0.0.1），机密业务数据不出边界。
    """
    try:
        prompt = _build_prompt(ptype, description, reply_text, outlet_from, outlet_notify)
        content = await _call_local_model(prompt)
        verdict = _parse_verdict(content)
        audit.append("ai_review", {
            "ptype": ptype, "verdict": verdict["verdict"], "reason": verdict["reason"][:80],
        }, actor="ai-reviewer")
        return verdict
    except Exception as e:
        audit.append("ai_review_error", {"error": str(e)[:120]}, actor="ai-reviewer")
        return {"verdict": "human", "reason": f"reviewer-unavailable:{type(e).__name__}"}


async def _call_local_model(prompt: str) -> str:
    """调本机模型：支持 OpenAI 格式（LLM_BASE_URL）与 Anthropic 格式（本机中转站）。"""
    from shared.config import LLM_BASE_URL, LLM_MODEL

    if LLM_BASE_URL:
        # OpenAI 格式（key-pool 网关等）
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{LLM_BASE_URL}/chat/completions",
                json={"model": LLM_MODEL,
                      "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0, "max_tokens": 200})
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    # Anthropic 格式（本机中转站 15721，机密数据不出边界）
    # max_tokens 2000：思考模型（deepseek 等）的思考 token 也计入，200 会全吃掉致 content 空
    from shared.config import LOCAL_RELAY_URL
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{LOCAL_RELAY_URL}/v1/messages",
            json={"model": LLM_MODEL, "max_tokens": 2000,
                  "messages": [{"role": "user", "content": prompt}]},
            headers={"anthropic-version": "2023-06-01"})
        resp.raise_for_status()
        content = resp.json().get("content", [])
        if not content:
            raise ValueError("empty content (thinking model ate max_tokens?)")
        return content[0]["text"]


def _build_prompt(ptype: str, description: str, reply_text: str,
                  outlet_from: str, outlet_notify: str) -> str:
    return f"""你是物流网点的回复审核员。判断下面这条自动回复是否合适，只输出 JSON。

问题件类型：{ptype}
问题描述：{description[:200]}
发布网点：{outlet_from}　通知网点：{outlet_notify}
拟自动回复：{reply_text[:200]}

判断标准（任一条不满足就转人工）：
1. 回复与问题类型匹配（催派件回复派送时效、信息修改回复知悉等）
2. 回复没有承诺具体时间/金额/责任归属（这些必须人工确认）
3. 回复没有敏感内容（仲裁/理赔/罚款/费用/责任判定类必须人工）
4. 问题描述复杂（多行说明、含诉求细节）时转人工，只有简单常规场景才自动

只输出：{{"verdict":"pass" 或 "human","reason":"一句话理由"}}"""


def _parse_verdict(content: str) -> dict:
    """解析模型输出，容错。解析失败 → 转人工。"""
    try:
        # 提取 JSON（模型可能带 ```json 包裹）
        s = content.strip()
        if s.startswith("```"):
            s = s.split("```")[1].lstrip("json").strip()
        data = json.loads(s)
        v = data.get("verdict", "")
        if v == "pass":
            return {"verdict": "pass", "reason": data.get("reason", "ok")}
        return {"verdict": "human", "reason": data.get("reason", "ai-said-human")}
    except Exception:
        return {"verdict": "human", "reason": "unparseable-review-output"}


class AiReviewer:
    """ports.AiReviewer 的实现。

    同时提供两种签名：review(parcel, reply_text)（Protocol）
    和 review_reply(**kw)（gatekeeper 调用形态），同一实现。
    """

    async def review(self, parcel, reply_text: str) -> dict:
        return await self.review_reply(
            ptype=parcel.ptype or "", description=parcel.description or "",
            reply_text=reply_text, outlet_from=parcel.outlet_from or "",
            outlet_notify=parcel.outlet_notify or "",
        )

    async def review_reply(self, ptype: str, description: str, reply_text: str,
                           outlet_from: str = "", outlet_notify: str = "") -> dict:
        return await review_reply(
            ptype=ptype, description=description, reply_text=reply_text,
            outlet_from=outlet_from, outlet_notify=outlet_notify,
        )
