"""数据源注册与装配（工厂）。

架构定稿（2026-10-13）：摒弃插件，改为服务端 CDP 直驱（Playwright 连调试 Chrome）。
数据源统一为 cdp（problemQuery 页读取）。写路径唯一 = gatekeeper.run_gates，
执行 = 服务端 cdp_driver（填弹窗/提交/回读）。

历史（已删除）：插件上报（extension / bridge_cache）与 CdpExecutor/ExtensionExecutor。
"""
from service.ports import ProblemSource


class CdpSource:
    """Playwright 读取的数据源：直连调试 Chrome 读 problemQuery 页。"""

    async def fetch_parcels(self):
        from service.problem_reader import fetch_problem_list
        listing = await fetch_problem_list()
        return listing.items


def get_source(mode: str = "cdp") -> ProblemSource:
    """当前唯一数据源 = CDP 直驱（mode 保留以便未来扩展）。"""
    return CdpSource()


async def fetch_current_parcels() -> list:
    """拉当前问题件（公共入口：主站/只读/闸门管线都用它）。"""
    from shared import settings as cfg
    mode = cfg.get("write.source_mode", "cdp")
    source = get_source(mode)
    return await source.fetch_parcels()
