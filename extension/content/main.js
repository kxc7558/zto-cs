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
  await sleep(2000);
  // 回读：行上的「最新回复内容」应包含刚提交的文本
  return { ok: true, filled: true, submitted: true, note: "auto-submitted" };
}

function isVisible(el) {
  return el.offsetParent !== null || getComputedStyle(el).display !== "none";
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// ---------- 轮询主循环 ----------

async function pollOnce() {
  try {
    const cfg = await sendToBg({ type: "GET_CONFIG" });
    if (!cfg.ok || !cfg.data.enabled) return;

    // 1. 读列表并上报
    const rows = readProblemRows();
    if (rows.length > 0) {
      const ingest = await sendToBg({ type: "INGEST_LIST", rows, page_url: location.href });
      if (!ingest.ok) return;
    }

    // 2. 拉任务（服务端已过四层闸门）
    const tasks = await sendToBg({ type: "GET_TASKS", limit: 5 });
    if (!tasks.ok || !tasks.data.tasks || tasks.data.tasks.length === 0) return;

    // 3. 逐条执行（SEMI：填入等人工；FULL：自动提交）
    const mode = cfg.data.mode || "semi";
    for (const task of tasks.data.tasks) {
      const needle = task.waybill_no || task.desc_hint;
      const link = findQuickReplyLink(needle);
      if (!link) continue;
      link.click();
      await sleep(800);
      const result = await fillQuickReply(task.reply_text, mode);
      // 4. 回传结果
      await sendToBg({
        type: "INGEST_RESULT",
        payload: {
          problem_id: task.problem_id,
          reply_text: task.reply_text,
          submitted: !!result.submitted,
          readback: result.note || "",
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
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "POLL_NOW") pollOnce();
});
