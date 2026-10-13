"""数据源注册与装配（工厂）。

模块化设计（review 后简化）：执行统一在插件侧（extension），
服务端只做决策与记账。数据源按配置选择：
  - extension：插件上报（bridge_cache，当前，风控最低）
  - cdp：Playwright 读取（备胎，需专用调试 Chrome）

旧版 CdpExecutor/ExtensionExecutor（直接调 replier.commit_reply 绕闸门）
已删除（review CRITICAL-1）：写路径唯一 = gatekeeper.run_gates，执行在插件侧。
"""
from service.ports import ProblemSource


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


def get_source(mode: str) -> ProblemSource:
    return ExtensionSource() if mode == "extension" else CdpSource()


async def fetch_current_parcels() -> list:
    """按配置 source_mode 拉当前问题件（公共入口：主站/只读/闸门管线都用它）。"""
    from shared import settings as cfg
    mode = cfg.get("write.source_mode", "extension")
    source = get_source(mode)
    return await source.fetch_parcels()
