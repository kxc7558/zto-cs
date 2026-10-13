// content script：中通问题件页（problemQuery.html）里的"手"。
// 分工：插件只执行，决策全在服务端（模板/规则/AI审核/审计）。
//
// 页面结构按 2026-10-12 真机实测（d:\AI-Knowledge\10-Projects\中通快运客服智助-CDP方案.md §14）：
// - 列表是 vxe-table：表头一个 table（thead th 含「问题件类型」），数据一个 table（tbody tr）
// - 行尾操作列有「快速回复」（el-popover，弹 problem-upload--quick-reply 表单）和「查看」
// - 快速回复弹窗：textarea（必填，maxlength 500，placeholder=请选择回复内容）+ 提交按钮
// - 运单号在部分类型行里显示为链接（z-copier）

const POLL_INTERVAL_MS = 60000;   // 页面内轮询间隔（服务端可下发覆盖）
const SEND_MIN_INTERVAL_MS = 45000; // FULL 模式：同会话两次自动提交最小间隔（抄 BOSS 验证值）

let lastSendAt = 0;
let pollTimer = null;

// ---------- 页面读取：问题件列表 ----------

function readProblemRows() {
  // 表头 table + 数据 table 配对（实测：表头与数据在分开的 table）
  const tables = Array.from(document.querySelectorAll("table"));
  let headerTable = null;
  for (let i = 0; i < tables.length; i++) {
    const ths = Array.from(tables[i].querySelectorAll("thead th")).map((th) => th.innerText.trim());
    if (ths.some((h) => h.includes("问题件类型"))) { headerTable = i; break; }
  }
  if (headerTable === null) return [];

  const ths = Array.from(tables[headerTable].querySelectorAll("thead th")).map((th) => th.innerText.trim());
  const idx = {};
  ths.forEach((h, i) => { if (h && !(h in idx)) idx[h] = i; });

  const rows = [];
  for (let k = headerTable + 1; k < tables.length; k++) {
    const dataRows = [];
    for (const tr of tables[k].querySelectorAll("tbody tr")) {
      let cells = Array.from(tr.querySelectorAll("td")).map((td) => td.innerText.trim());
      // 行首可能多出复选框/操作空列（实测 td 20 vs th 21）：按"首个非空对齐"补/裁
      if (cells.length !== ths.length) {
        const hNonEmpty = ths.findIndex((h) => h);
        const jNonEmpty = cells.findIndex((c) => c);
        if (hNonEmpty >= 0 && jNonEmpty >= 0) {
          const pad = hNonEmpty - jNonEmpty;
          if (pad > 0) cells = Array(pad).fill("").concat(cells);
          else if (pad < 0) cells = cells.slice(-pad);
        }
      }
      dataRows.push(cells);
    }
    if (dataRows.length > 0) {
      for (const cells of dataRows) {
        const row = {};
        for (const [h, i] of Object.entries(idx)) row[h] = cells[i] ?? "";
        rows.push(row);
      }
      break; // 取第一个有数据的数据表
    }
  }
  return rows;
}

// ---------- 页面写入：快速回复 ----------

function findQuickReplyLink(needle) {
  // 在目标行（按运单号或描述前 20 字匹配）找「快速回复」链接
  const links = Array.from(document.querySelectorAll(".el-popover__reference-wrapper")).filter(
    (e) => e.innerText.trim() === "快速回复"
  );
  for (const link of links) {
    const tr = link.closest("tr");
    if (tr && needle && tr.innerText.includes(needle)) return link;
  }
  return null;
}

async function fillQuickReply(replyText, mode) {
  // 前提：findQuickReplyLink 已点击，弹窗已打开（problem-upload--quick-reply）
  const pop = Array.from(document.querySelectorAll(".el-popover")).find(
    (p) => p.querySelector(".problem-upload--quick-reply") && isVisible(p)
  );
  if (!pop) return { ok: false, error: "popover not visible" };
  const ta = pop.querySelector("textarea");
  if (!ta) return { ok: false, error: "textarea not found" };

  // 填入回复内容（React/Vue 受控输入：先设 value 再触发 input 事件）
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
  setter.call(ta, replyText);
  ta.dispatchEvent(new Event("input", { bubbles: true }));

  if (mode === "semi") {
    // SEMI：填好就停，等人工点「提交」
    return { ok: true, filled: true, submitted: false, note: "filled-awaiting-human-submit" };
  }
  // FULL：自动点提交（带限速）
  const now = Date.now();
  if (now - lastSendAt < SEND_MIN_INTERVAL_MS) {
    return { ok: true, filled: true, submitted: false, note: "rate-limited-waiting" };
  }
  const submitBtn = Array.from(pop.querySelectorAll("button")).find((b) => b.innerText.trim() === "提交");
  if (!submitBtn) return { ok: false, error: "submit button not found" };
  submitBtn.click();
  lastSendAt = now;
  await sleep(2500);
  // 回读校验：目标行的「最新回复内容」应包含刚提交的文本（界面提示成功不算数）
  const readback = readRowReply(needle);
  const ok = replyText.slice(0, 12) && (readback || "").includes(replyText.slice(0, 12));
  return { ok: true, filled: true, submitted: !!ok, readback: readback || "",
           note: ok ? "auto-submitted-verified" : "submitted-but-readback-missing" };
}

function readRowReply(needle) {
  // 读目标行的「最新回复内容」列（表头映射）
  const tables = Array.from(document.querySelectorAll("table"));
  let headerTable = null;
  for (let i = 0; i < tables.length; i++) {
    const ths = Array.from(tables[i].querySelectorAll("thead th")).map((th) => th.innerText.trim());
    if (ths.some((h) => h.includes("问题件类型"))) { headerTable = i; break; }
  }
  if (headerTable === null) return "";
  const ths = Array.from(tables[headerTable].querySelectorAll("thead th")).map((th) => th.innerText.trim());
  const replyIdx = ths.findIndex((h) => h.includes("最新回复内容"));
  if (replyIdx < 0) return "";
  for (let k = headerTable + 1; k < tables.length; k++) {
    for (const tr of tables[k].querySelectorAll("tbody tr")) {
      if (needle && tr.innerText.includes(needle)) {
        const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.innerText.trim());
        return cells[replyIdx] || "";
      }
    }
  }
  return "";
}

function isVisible(el) {
  return el.offsetParent !== null || getComputedStyle(el).display !== "none";
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// ---------- 确认提交（工作台确认后，服务端下发指令）----------

async function executeSubmit(replyText) {
  // 前提：弹窗已开且已填（fillQuickReply 填的）
  const pop = Array.from(document.querySelectorAll(".el-popover")).find(
    (p) => p.querySelector(".problem-upload--quick-reply") && isVisible(p)
  );
  if (!pop) return { ok: false, error: "popover not visible for submit" };
  const submitBtn = Array.from(pop.querySelectorAll("button")).find((b) => b.innerText.trim() === "提交");
  if (!submitBtn) return { ok: false, error: "submit button not found" };
  const now = Date.now();
  if (now - lastSendAt < SEND_MIN_INTERVAL_MS) {
    return { ok: false, error: "rate-limited" };
  }
  submitBtn.click();
  lastSendAt = now;
  await sleep(2500);
  return { ok: true, submitted: true, note: "submitted-after-confirm" };
}

// ---------- 轮询主循环 ----------

// 点击「查询」刷新数据（中通页面数据不会自动刷新，轮询前必须点）
async function clickQuery() {
  const btn = Array.from(document.querySelectorAll("button")).find(
    (b) => b.innerText.trim() === "查询"
  );
  if (!btn) return false;
  btn.click();
  await sleep(4000); // 等表格刷新（vxe-table 渲染）
  return true;
}

async function pollOnce() {
  try {
    const cfg = await sendToBg({ type: "GET_CONFIG" });

    // 0. 先点「查询」刷新数据（中通页面数据不自动刷新）
    await clickQuery();

    // 1. 读列表并上报（**不受开关限制**——off 模式也要上报，工作台才有数据看）
    const rows = readProblemRows();
    if (rows.length > 0) {
      await sendToBg({ type: "INGEST_LIST", rows, page_url: location.href });
    }

    // 2. 拉任务（仅 enabled 时；服务端已过闸门）
    if (!cfg.ok || !cfg.data.enabled) return;
    const tasks = await sendToBg({ type: "GET_TASKS", limit: 5 });
    if (!tasks.ok || !tasks.data.tasks || tasks.data.tasks.length === 0) return;

    // 3. 逐条执行（SEMI：填入等人工；FULL：自动提交）
    const mode = cfg.data.mode || "semi";
    for (const task of tasks.data.tasks) {
      const needle = task.waybill_no || task.desc_hint;
      const link = findQuickReplyLink(needle);
      if (!link) continue;
      // el-popover 的 Vue 触发绑在内层元素——点 wrapper 的第一个子元素（实测 wrapper.click() 不弹）
      const target = link.firstElementChild || link;
      ['mousedown', 'mouseup', 'click'].forEach(ev => {
        target.dispatchEvent(new MouseEvent(ev, {bubbles: true, cancelable: true, view: window}));
      });
      await sleep(800);
      const result = await fillQuickReply(task.reply_text, mode);
      // 4. 回传结果（readback 供服务端判定，submitted 旗子不算数）
      await sendToBg({
        type: "INGEST_RESULT",
        payload: {
          problem_id: task.problem_id,
          reply_text: task.reply_text,
          submitted: !!result.submitted,
          readback: result.readback || result.note || "",
          mode,
          error: result.error || null,
        },
      });
      if (result.submitted) await sleep(3000); // 拟人间隔
      if (mode === "semi") break; // SEMI 一次只填一条，等人处理
    }
  } catch (e) {
    console.warn("[zto-cs bridge] poll error:", e);
  }
}

function sendToBg(msg) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(msg, (resp) => {
      void chrome.runtime.lastError;
      resolve(resp || { ok: false, error: "no response" });
    });
  });
}

// ---------- 启动 ----------

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(pollOnce, POLL_INTERVAL_MS);
  // 页面加载后 3 秒先跑一轮
  setTimeout(pollOnce, 3000);
}

startPolling();
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "POLL_NOW") {
    pollOnce();
  } else if (msg.type === "EXECUTE_SUBMIT") {
    (async () => {
      const r = await executeSubmit(msg.reply_text || "");
      if (r.ok && r.submitted) {
        // 提交成功 → 回读校验 → 上报 committed
        const readback = readRowReply(msg.desc_hint || "");
        await sendToBg({
          type: "INGEST_RESULT",
          payload: {
            problem_id: msg.problem_id,
            reply_text: msg.reply_text || "",
            submitted: true,
            readback: readback || r.note,
            mode: "semi",
          },
        });
      } else {
        await sendToBg({
          type: "INGEST_RESULT",
          payload: {
            problem_id: msg.problem_id || "",
            reply_text: msg.reply_text || "",
            submitted: false,
            readback: "",
            mode: "semi",
            error: r.error || "submit failed",
          },
        });
      }
      sendResponse(r);
    })();
    return true;
  }
});
