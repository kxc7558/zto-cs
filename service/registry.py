"""数据源与执行通道的注册与装配（工厂）。

模块化设计：按配置 bridge_mode 选择实现，加新实现 = 注册一个类，管线零改动。
  - extension：插件上报/插件填弹窗（当前，风控最低）
  - cdp：Playwright 读取/提交（备胎，已实现但需专用调试 Chrome）
"""
from service.ports import ProblemSource, ReplyExecutor


class ExtensionSource:
    """插件上报的数据源：列表来自 bridge_cache（content script 定期上报）。"""

    async def fetch_parcels(self):
        from service.bridge_cache import get_latest_parcels
        return get_latest_parcels()


class CdpSource:
    """Playwright 读取的数据源（备胎）：直连调试 Chrome 读 problemQuery 页。"""

    async def fetch_parcels(self):
        from service.problem_reader import fetch_problem_list
        listing = await fetch_problem_list()
        return listing.items


class ExtensionExecutor:
    """插件执行的通道：回复由 content script 在页面里填弹窗提交。

    服务端只记账（结果由 /bridge/ingest/result 回传）。
    """

    async def execute(self, parcel, reply_text: str, mode: str) -> dict:
        # 插件桥接形态下，执行发生在浏览器里；这里不直接执行。
        # 任务下发见 /bridge/tasks/pending，结果回传见 ingest_result。
        raise NotImplementedError("extension executor runs in-browser via bridge endpoints")


class CdpExecutor:
    """Playwright 提交的通道（备胎）。"""

    async def execute(self, parcel, reply_text: str, mode: str) -> dict:
        from service.replier import commit_reply
        return await commit_reply(
            problem_id=parcel.problem_id, waybill_no=parcel.waybill_no,
            reply_text=reply_text, confirmed_by="cdp", auto=(mode == "full"),
            desc_hint=(parcel.description or "")[:20],
        )


def get_source(mode: str) -> ProblemSource:
    return ExtensionSource() if mode == "extension" else CdpSource()


def get_executor(mode: str) -> ReplyExecutor:
    return ExtensionExecutor() if mode == "extension" else CdpExecutor()
