"""数据源端口（Protocol）：谁提供问题件列表，谁执行回复。

模块化设计（便于拓展）：
- 数据源：ExtensionSource（插件上报，当前）/ CdpSource（Playwright 读取，备胎）
  → 将来官方 API 开放，加 ApiSource 即可，闸门管线与执行通道零改动。
- 执行通道：ExtensionExecutor（插件填弹窗，当前）/ CdpExecutor（Playwright 提交，备胎）
  → 换通道不动闸门。

闸门管线（gatekeeper.py）与端口解耦：管子只认 ProblemParcel 和 WriteIntent。
"""
from typing import Protocol

from shared.models import ProblemParcel


class ProblemSource(Protocol):
    """问题件数据源：提供列表。"""

    async def fetch_parcels(self) -> list[ProblemParcel]:
        """拉当前问题件列表（含最新回复状态）。"""
        ...


class ReplyExecutor(Protocol):
    """回复执行通道：把一条已过闸门的回复写进中通系统。"""

    async def execute(self, parcel: ProblemParcel, reply_text: str, mode: str) -> dict:
        """执行回复。返回 {submitted: bool, readback: str, error: str|None}。

        实现 must：执行后回读校验（界面提示成功不算数）。
        """
        ...


class AiReviewer(Protocol):
    """AI 审核器：提交前审一遍。"""

    async def review(self, parcel: ProblemParcel, reply_text: str) -> dict:
        """返回 {verdict: pass|human, reason: str}。fail-safe：故障 → human。"""
        ...
