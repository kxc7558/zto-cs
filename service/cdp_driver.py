"""CDP 直驱：服务端直接在调试 Chrome 里执行页面动作。

- read_and_fill（工作台确认时调用）：填弹窗（不提交）→ 停在工作台等确认
- fill_and_submit（确认后调用）：填 → 点提交 → 回读（同一连接）
- 定位：vxe-table 固定列——快速回复链接与主数据行是平行数组（同索引同一行），
  行匹配用运单号优先（唯一），描述前缀兜底（撞行风险低但存在）。
"""
import asyncio
import random

from playwright.async_api import async_playwright

from shared.config import CDP_WS_URL

_JS_LOCATE = """
  (anchors) => {
    // anchors: [主锚, 副锚]——两个都在行文本里才匹配（防同类型多行撞锚）
    const [hint, hint2] = anchors;
    const tables = Array.from(document.querySelectorAll('table'));
    let ht = null;
    for (let i = 0; i < tables.length; i++) {
      const ths = Array.from(tables[i].querySelectorAll('thead th')).map(th => th.innerText.trim());
      if (ths.some(h => h.includes('问题件类型'))) { ht = i; break; }
    }
    if (ht === null) return -1;
    let dataIdx = 0;
    let match = -1, count = 0;
    for (let k = ht + 1; k < tables.length; k++) {
      for (const tr of tables[k].querySelectorAll('tbody tr')) {
        const ok = hint && tr.innerText.includes(hint) && (!hint2 || tr.innerText.includes(hint2));
        if (ok) { match = dataIdx; count++; }
        dataIdx++;
      }
    }
    if (count > 1) return -2;
    return match;
  }
"""

_JS_FILL = """
  (text) => {
    const pops = Array.from(document.querySelectorAll('.el-popover')).filter(p =>
      p.querySelector('.problem-upload--quick-reply') && getComputedStyle(p).display !== 'none');
    if (!pops.length) return false;
    const ta = pops[0].querySelector('textarea');
    if (!ta) return false;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(ta, text);
    ta.dispatchEvent(new Event('input', {bubbles: true}));
    return true;
  }
"""

_JS_SUBMIT = """
  () => {
    const pops = Array.from(document.querySelectorAll('.el-popover')).filter(p =>
      p.querySelector('.problem-upload--quick-reply') && getComputedStyle(p).display !== 'none');
    if (!pops.length) return false;
    const btn = Array.from(pops[0].querySelectorAll('button')).find(b => b.innerText.trim() === '提交');
    if (!btn) return false;
    btn.click();
    return true;
  }
"""

_JS_READBACK = """
  (anchors) => {
    const [hint, hint2, text] = anchors;
    const tables = Array.from(document.querySelectorAll('table'));
    let ht = null;
    for (let i = 0; i < tables.length; i++) {
      const ths = Array.from(tables[i].querySelectorAll('thead th')).map(th => th.innerText.trim());
      if (ths.some(h => h.includes('问题件类型'))) { ht = i; break; }
    }
    if (ht === null) return 'ERR:ambiguous-or-notfound';
    const ths = Array.from(tables[ht].querySelectorAll('thead th')).map(th => th.innerText.trim());
    const replyIdx = ths.findIndex(h => h.includes('最新回复内容'));
    if (replyIdx < 0) return 'ERR:ambiguous-or-notfound';
    let match = null, count = 0;
    for (const t of tables) {
      for (const tr of t.querySelectorAll('tbody tr')) {
        const ok = hint && tr.innerText.includes(hint) && (!hint2 || tr.innerText.includes(hint2));
        if (ok) { match = tr; count++; }
      }
    }
    if (count !== 1) return 'ERR:ambiguous-row';
    const cells = Array.from(match.querySelectorAll('td')).map(td => td.innerText.trim());
    const reply = cells[replyIdx] || '';
    return reply.includes(text.slice(0, 10)) ? reply : 'ERR:text-not-in-target-row';
  }
"""


async def _with_page(coro_fn):
    """连接调试 Chrome 执行 coro_fn(page)，完事关连接。"""
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(CDP_WS_URL)
        ctx = browser.contexts[0]
        page = next((pg for pg in ctx.pages if "problemQuery" in pg.url), None)
        if page is None:
            raise RuntimeError("problemQuery 页未打开")
        return await coro_fn(page)
    finally:
        await pw.stop()


async def _click_quick_reply(page, hint: str) -> None:
    """点目标行的快速回复链接（平行数组定位：运单号优先，描述兜底）。"""
    await asyncio.sleep(random.uniform(2, 5))  # 反风控
    idx = await page.evaluate(_JS_LOCATE, [hint, ""])
    if idx == -2:
        raise RuntimeError(f"ambiguous row match (multiple rows match hint={hint[:12]}) — needs manual")
    if idx < 0:
        raise RuntimeError(f"target row not found on page (hint={hint[:12]})")
    link = page.locator(".el-popover__reference-wrapper", has_text="快速回复").nth(idx)
    if not await link.count():
        raise RuntimeError(f"no quick-reply link at index {idx}")
    await link.click(timeout=10_000)
    await page.wait_for_timeout(1800)


async def fill_for_confirm(hint: str, reply_text: str, hint2: str = "") -> dict:
    """填弹窗（不提交）——工作台确认前的准备。弹窗停在那里等人工。"""
    async def inner(page):
        idx = await page.evaluate(_JS_LOCATE, [hint, hint2])
        if idx == -2:
            raise RuntimeError(f"ambiguous row (hint={hint[:12]}) — needs manual")
        if idx < 0:
            raise RuntimeError(f"target row not found (hint={hint[:12]})")
        link = page.locator(".el-popover__reference-wrapper", has_text="快速回复").nth(idx)
        if not await link.count():
            raise RuntimeError(f"no quick-reply link at index {idx}")
        # 点击后弹窗可能不弹（SPA 未就绪）——重试最多 3 次
        filled = False
        for attempt in range(3):
            await link.click(timeout=10_000)
            await page.wait_for_timeout(2000)
            filled = await page.evaluate(_JS_FILL, reply_text)
            if filled:
                break
        if not filled:
            raise RuntimeError("fill failed after 3 attempts (popover not visible)")
        filled = await page.evaluate(_JS_FILL, reply_text)
        if not filled:
            return {"ok": False, "error": "fill failed (popover not visible)"}
        return {"ok": True, "filled": reply_text[:20]}
    return await _with_page(inner)


async def fill_and_submit(hint: str, reply_text: str, hint2: str = "") -> dict:
    """同一连接：填 → 点提交 → 回读。确认后调用（或 full 模式）。"""
    async def inner(page):
        idx = await page.evaluate(_JS_LOCATE, [hint, hint2])
        if idx == -2:
            raise RuntimeError(f"ambiguous row (hint={hint[:12]}) — needs manual")
        if idx < 0:
            raise RuntimeError(f"target row not found (hint={hint[:12]})")
        link = page.locator(".el-popover__reference-wrapper", has_text="快速回复").nth(idx)
        if not await link.count():
            raise RuntimeError(f"no quick-reply link at index {idx}")
        filled = False
        for attempt in range(3):
            await link.click(timeout=10_000)
            await page.wait_for_timeout(2000)
            filled = await page.evaluate(_JS_FILL, reply_text)
            if filled:
                break
        if not filled:
            return {"ok": False, "error": "fill failed after retries"}
        filled = await page.evaluate(_JS_FILL, reply_text)
        if not filled:
            return {"ok": False, "error": "fill failed"}
        clicked = await page.evaluate(_JS_SUBMIT)
        if not clicked:
            return {"ok": False, "error": "submit button not found"}
        await page.wait_for_timeout(2500)
        readback = await page.evaluate(_JS_READBACK, [hint, hint2, reply_text])
        if str(readback).startswith("ERR:"):
            return {"ok": False, "submitted": False, "readback": "",
                    "error": readback, "note": "readback verification failed"}
        return {"ok": True, "submitted": True, "readback": readback or "",
                "note": "verified"}
    return await _with_page(inner)


async def confirm_flow(hint: str, reply_text: str) -> dict:
    """工作台确认后的执行：重新填（弹窗可能已关）→ 提交 → 回读。"""
    return await fill_and_submit(hint, reply_text)
