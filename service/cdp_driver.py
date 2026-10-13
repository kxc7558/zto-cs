"""CDP 直驱（摒弃插件后）：服务端直接在调试 Chrome 里执行页面动作。

填弹窗 / 点提交 / 回读校验——全部在服务端，Playwright 连 9222 调试 Chrome。
（原插件桥接的"读数据"已由 problem_reader 接管，本模块只做"写"的两步。）
"""
import asyncio
import random

from playwright.async_api import async_playwright

from shared.config import CDP_WS_URL


async def _page():
    """连接调试 Chrome，找 problemQuery 页。"""
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(CDP_WS_URL)
        ctx = browser.contexts[0]
        page = next((pg for pg in ctx.pages if "problemQuery" in pg.url), None)
        if page is None:
            raise RuntimeError("problemQuery 页未打开（请在中通系统里打开问题件查询）")
        return pw, page
    except Exception:
        await pw.stop()
        raise


async def fill_reply(desc_hint: str, reply_text: str) -> dict:
    """找到目标行的「快速回复」，点开弹窗，填入回复（不提交）。semi 模式停在这里。"""
    pw, page = await _page()
    try:
        await asyncio.sleep(random.uniform(2, 5))  # 反风控：拟人延迟
        # vxe-table 固定列结构：快速回复链接在独立表格里，与主数据行**平行数组**（同索引同行）。
        # 定位：主数据表里目标行的索引 → 该索引的快速回复链接。
        link_index = await page.evaluate("""
          (hint) => {
            // 主数据行（readProblemRows 同款顺序：headerTable 后第一个有数据的表）
            const tables = Array.from(document.querySelectorAll('table'));
            let ht = null;
            for (let i = 0; i < tables.length; i++) {
              const ths = Array.from(tables[i].querySelectorAll('thead th')).map(th => th.innerText.trim());
              if (ths.some(h => h.includes('问题件类型'))) { ht = i; break; }
            }
            if (ht === null) return -1;
            let dataIdx = 0;
            for (let k = ht + 1; k < tables.length; k++) {
              let found = false;
              for (const tr of tables[k].querySelectorAll('tbody tr')) {
                if (hint && tr.innerText.includes(hint)) return dataIdx;
                dataIdx++;
              }
              if (found) break;
            }
            return -1;
          }
        """, desc_hint)
        if link_index < 0:
            return {"ok": False, "error": "target row not found on page"}
        # 该索引的快速回复链接
        link = page.locator(".el-popover__reference-wrapper", has_text="快速回复").nth(link_index)
        has_entry = await link.count()
        if not has_entry:
            return {"ok": False, "error": f"no quick-reply link at index {link_index}"}
        await link.click(timeout=10_000)
        await page.wait_for_timeout(1800)
        # 填内容
        result = await page.evaluate("""
          () => {
            const pops = Array.from(document.querySelectorAll('.el-popover')).filter(p =>
              p.querySelector('.problem-upload--quick-reply') && getComputedStyle(p).display !== 'none');
            if (!pops.length) return {ok: false, error: 'popover not visible'};
            const ta = pops[0].querySelector('textarea');
            if (!ta) return {ok: false, error: 'textarea not found'};
            const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
            setter.call(ta, %s);
            ta.dispatchEvent(new Event('input', {bubbles: true}));
            return {ok: true, filled: ta.value.slice(0, 20)};
          }
        """ % (repr(reply_text)))
        return result
    finally:
        await pw.stop()


async def submit_reply(desc_hint: str, reply_text: str) -> dict:
    """确认后执行：点弹窗提交 + 回读校验。"""
    pw, page = await _page()
    try:
        await asyncio.sleep(random.uniform(2, 5))
        # 点弹窗里的提交按钮
        submit = page.locator(".el-popover", has=page.locator(".problem-upload--quick-reply")).locator(
            "button", has_text="提交").last
        try:
            await submit.click(timeout=10_000)
        except Exception as e:
            return {"ok": False, "error": f"submit click failed: {type(e).__name__}"}
        await page.wait_for_timeout(2500)
        # 回读：目标行的「最新回复内容」列应包含刚提交的文本
        readback = await page.evaluate("""
          (hint) => {
            const tables = Array.from(document.querySelectorAll('table'));
            let ht = null;
            for (let i = 0; i < tables.length; i++) {
              const ths = Array.from(tables[i].querySelectorAll('thead th')).map(th => th.innerText.trim());
              if (ths.some(h => h.includes('问题件类型'))) { ht = i; break; }
            }
            if (ht === null) return '';
            const ths = Array.from(tables[ht].querySelectorAll('thead th')).map(th => th.innerText.trim());
            const replyIdx = ths.findIndex(h => h.includes('最新回复内容'));
            if (replyIdx < 0) return '';
            for (const t of tables) {
              for (const tr of t.querySelectorAll('tbody tr')) {
                if (hint && tr.innerText.includes(hint)) {
                  const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                  return cells[replyIdx] || '';
                }
              }
            }
            return '';
          }
        """, desc_hint)
        verified = bool(readback) and (reply_text[:12] in readback)
        return {"ok": verified, "submitted": verified,
                "readback": readback or "",
                "note": "verified" if verified else "readback-missing"}
    finally:
        await pw.stop()


async def fill_and_submit(desc_hint: str, reply_text: str) -> dict:
    """同一连接内：填弹窗 → 点提交 → 回读。用于自动确认（full）或快速确认。"""
    pw, page = await _page()
    try:
        await asyncio.sleep(random.uniform(2, 5))
        link_index = await page.evaluate("""
          (hint) => {
            const tables = Array.from(document.querySelectorAll('table'));
            let ht = null;
            for (let i = 0; i < tables.length; i++) {
              const ths = Array.from(tables[i].querySelectorAll('thead th')).map(th => th.innerText.trim());
              if (ths.some(h => h.includes('问题件类型'))) { ht = i; break; }
            }
            if (ht === null) return -1;
            let dataIdx = 0;
            for (let k = ht + 1; k < tables.length; k++) {
              for (const tr of tables[k].querySelectorAll('tbody tr')) {
                if (hint && tr.innerText.includes(hint)) return dataIdx;
                dataIdx++;
              }
            }
            return -1;
          }
        """, desc_hint)
        if link_index < 0:
            return {"ok": False, "error": "target row not found"}
        link = page.locator(".el-popover__reference-wrapper", has_text="快速回复").nth(link_index)
        if not await link.count():
            return {"ok": False, "error": "no quick-reply link"}
        await link.click(timeout=10_000)
        await page.wait_for_timeout(1800)
        # 填
        filled = await page.evaluate("""
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
        """, reply_text)
        if not filled:
            return {"ok": False, "error": "fill failed"}
        # 提交（同一连接，弹窗肯定还开着）
        submit = page.locator(".el-popover", has=page.locator(".problem-upload--quick-reply")).locator(
            "button", has_text="提交").last
        # 用 evaluate 找可见弹窗的提交按钮（locator has 语法在 50 个 popover 上可能慢）
        clicked = await page.evaluate("""
          () => {
            const pops = Array.from(document.querySelectorAll('.el-popover')).filter(p =>
              p.querySelector('.problem-upload--quick-reply') && getComputedStyle(p).display !== 'none');
            if (!pops.length) return false;
            const btn = Array.from(pops[0].querySelectorAll('button')).find(b => b.innerText.trim() === '提交');
            if (!btn) return false;
            btn.click();
            return true;
          }
        """)
        if not clicked:
            return {"ok": False, "error": "submit button not found"}
        await page.wait_for_timeout(2500)
        # 回读
        readback = await page.evaluate("""
          (hint) => {
            const tables = Array.from(document.querySelectorAll('table'));
            let ht = null;
            for (let i = 0; i < tables.length; i++) {
              const ths = Array.from(tables[i].querySelectorAll('thead th')).map(th => th.innerText.trim());
              if (ths.some(h => h.includes('问题件类型'))) { ht = i; break; }
            }
            if (ht === null) return '';
            const ths = Array.from(tables[ht].querySelectorAll('thead th')).map(th => th.innerText.trim());
            const replyIdx = ths.findIndex(h => h.includes('最新回复内容'));
            if (replyIdx < 0) return '';
            for (const t of tables) {
              for (const tr of t.querySelectorAll('tbody tr')) {
                if (hint && tr.innerText.includes(hint)) {
                  const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                  return cells[replyIdx] || '';
                }
              }
            }
            return '';
          }
        """, desc_hint)
        verified = bool(readback) and (reply_text[:12] in readback)
        return {"ok": verified, "submitted": verified, "readback": readback or "",
                "note": "verified" if verified else "readback-missing"}
    finally:
        await pw.stop()
