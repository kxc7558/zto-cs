"""浏览器会话管理：连接已登录的调试 Chrome（CDP 9222）。掉线重连、单账号串行。"""
import asyncio
import random

from playwright.async_api import async_playwright

from shared.config import CDP_WS_URL


class BrowserSession:
    """连接已有调试 Chrome（带登录态）。掉线重连。"""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()  # 同账号串行（硬规则）

    async def _connect(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.connect_over_cdp(CDP_WS_URL)
        ctx = self._browser.contexts[0] if self._browser.contexts else None
        if ctx is None:
            raise RuntimeError("no browser context on CDP target")
        self._context = ctx
        # 找 problemQuery tab（可能开着多个，取最后一个——最新打开的）
        target = None
        for pg in ctx.pages:
            if "problemQuery" in pg.url:
                target = pg
        if target is not None:
            self._page = target
        else:
            pages = ctx.pages
            self._page = pages[-1] if pages else await ctx.new_page()

    async def page(self):
        async with self._lock:
            if self._page is None or self._browser.is_connected() is False:
                await self._connect()
            return self._page

    async def goto(self, url: str):
        p = await self.page()
        await asyncio.sleep(random.uniform(2, 6))  # 反风控：请求间随机延迟
        await p.goto(url, wait_until="domcontentloaded", timeout=60_000)
        return p

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
