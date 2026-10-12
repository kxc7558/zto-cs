// 后台 service worker：与 FastAPI 服务通信（桥接轮询/上报）。
// 分工：插件=手，服务端=脑。本文件只做 HTTP 中转，不做任何决策。

const API = "http://127.0.0.1:8765";

async function api(path, options) {
  const r = await fetch(API + path, options);
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

// ---- 定时任务：拉任务下发给 content script ----
// 注：content script 自身在页面加载后轮询；alarm 仅作兜底（页面休眠时）
chrome.alarms.create("poll-tasks", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== "poll-tasks") return;
  // 通知活动的 problemQuery 标签页拉任务
  chrome.tabs.query({ url: ["https://datahub-portlet.zto56.com/*", "https://ws.zto56.com/*"] }, (tabs) => {
    for (const t of tabs) {
      chrome.tabs.sendMessage(t.id, { type: "POLL_NOW" }, () => void chrome.runtime.lastError);
    }
  });
});
