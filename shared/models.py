"""问题件领域模型（Pydantic）。"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ProblemParcel(BaseModel):
    """problemQuery 列表页的一条问题件（字段与 §14.2 实测列对齐）。"""

    problem_id: str                    # 本地锚点（运单号+类型+登记时间 合成）
    waybill_no: Optional[str] = None   # 运单号
    outlet_from: Optional[str] = None  # 发布网点
    outlet_notify: Optional[str] = None  # 通知网点
    category: Optional[str] = None     # 问题件一级类型（通知类/必回类/发件网点差错类）
    ptype: Optional[str] = None        # 问题件类型（催派件/仲裁证据上传/…）
    description: Optional[str] = None  # 问题件描述（AI 理解输入）
    latest_reply: Optional[str] = None  # 最新回复内容
    latest_reply_at: Optional[str] = None  # 最新回复时间
    reply_deadline: Optional[str] = None   # 回复时效（当天需回复/12点前需回复）
    order_source: Optional[str] = None
    status: Optional[str] = None       # 快件状态
    province: Optional[str] = None
    creator: Optional[str] = None
    created_at: Optional[str] = None
    needs_human: bool = False          # 敏感类转人工标记（规则引擎判定）
    reply_progress: Optional[str] = None  # 未回复/当天需回复/已回复


class ParcelList(BaseModel):
    total: int
    scanned_at: datetime
    items: list[ProblemParcel]
