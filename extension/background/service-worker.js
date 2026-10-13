// 后台 service worker：与 FastAPI 服务通信（桥接轮询/上报）。
// 分工：插件=手，服务端=脑。本文件只做 HTTP 中转，不做任何决策。

const API = "http://127.0.0.1:8765";

// 桥接密钥：首次安装时从 options 页粘贴（存 chrome.storage.local），
// 每次请求附 X-Bridge-Token（review HIGH-1：插件端从未发 token 导致全部 401）。
async function getToken() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["bridgeToken"], (r) => resolve(r.bridgeToken || ""));
  });
}

async function api(path, options = {}) {
  const token = await getToken();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers["X-Bridge-Token"] = token;
  const r = await fetch(API + path, { ...options, headers });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

// ---- 消息路由（content script → service worker → FastAPI）----

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "INGEST_LIST") {
        const data = await api("/bridge/ingest/list", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rows: msg.rows, page_url: msg.page_url }),
        });
        sendResponse({ ok: true, data });
      } else if (msg.type === "GET_TASKS") {
        const data = await api(`/bridge/tasks/pending?limit=${msg.limit || 5}`);
        sendResponse({ ok: true, data });
      } else if (msg.type === "INGEST_RESULT") {
        const data = await api("/bridge/ingest/result", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(msg.payload),
        });
        sendResponse({ ok: true, data });
      } else if (msg.type === "GET_CONFIRMATIONS") {
        const data = await api("/bridge/confirmations");
        sendResponse({ ok: true, data });
      } else if (msg.type === "BRIDGE_CONFIRM") {
        const data = await api("/bridge/confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ problem_id: msg.problem_id }),
        });
        sendResponse({ ok: true, data });
      } else if (msg.type === "GET_CONFIG") {
        const data = await api("/bridge/config");
        sendResponse({ ok: true, data });
      } else {
        sendResponse({ ok: false, error: "unknown message type" });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e.message || e) });
    }
  })();
  return true; // async response
});

// ---- 主动注入 content script（MV3 --load-extension 的声明式注入对已有 tab 不生效）----
async function ensureInjected(tabId) {
  return new Promise((resolve) => {
    // 先探测是否已注入（发个轻量消息）
    chrome.tabs.sendMessage(tabId, { type: "GET_CONFIG" }, (resp) => {
      if (chrome.runtime.lastError) {
        // 未注入 → 主动注入
        chrome.scripting.executeScript(
          { target: { tabId }, files: ["content/main.js"] },
          (results) => resolve(!!results)
        );
      } else {
        resolve(true); // 已注入
      }
    });
  });
}

// ---- 定时任务：拉任务下发给 content script ----
chrome.alarms.create("poll-tasks", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== "poll-tasks") return;
  chrome.tabs.query({ url: ["https://datahub-portlet.zto56.com/*", "https://ws.zto56.com/*"] }, async (tabs) => {
    for (const t of tabs) {
      await ensureInjected(t.id);
      chrome.tabs.sendMessage(t.id, { type: "POLL_NOW" }, () => void chrome.runtime.lastError);
    }
  });
  // 检查工作台已确认的回复 → 下发执行指令
  try {
    const conf = await api("/bridge/confirmations");
    // confirmed 状态的条目需要执行——拉 confirmed 列表
    const hist = await api("/bridge/history");
    const toExecute = (hist.items || []).filter(
      (x) => x.status === "confirmed" && x.confirmed_by === "human:workbench"
    );
    for (const item of toExecute) {
      if (tabs.length) {
        chrome.tabs.sendMessage(tabs[0].id, {
          type: "EXECUTE_SUBMIT",
          problem_id: item.problem_id,
          reply_text: item.reply_text,
          desc_hint: item.waybill_no || "",
        }, (resp) => void chrome.runtime.lastError);
      }
    }
  } catch (e) { /* 服务不可达静默 */ }
});

// 启动时也注入一次
chrome.tabs.query({ url: ["https://datahub-portlet.zto56.com/*", "https://ws.zto56.com/*"] }, async (tabs) => {
  for (const t of tabs) await ensureInjected(t.id);
});
