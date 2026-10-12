// options 页：保存桥接密钥到 chrome.storage.local，显示密钥指纹供人工核对
const API = "http://127.0.0.1:8765";

document.getElementById("save").onclick = async () => {
  const token = document.getElementById("token").value.trim();
  if (!token) return;
  await chrome.storage.local.set({ bridgeToken: token });
  document.getElementById("ok").textContent = "已保存";
  verify(token);
};

async function verify(token) {
  try {
    const r = await fetch(API + "/bridge/config", { headers: { "X-Bridge-Token": token } });
    if (r.ok) {
      document.getElementById("ok").textContent = "已保存 ✓ 连接成功";
    } else {
      document.getElementById("ok").textContent = "已保存（密钥不对，请核对）";
    }
  } catch (e) {
    document.getElementById("ok").textContent = "已保存（服务未启动）";
  }
}

// 载入已有配置
chrome.storage.local.get(["bridgeToken"], (r) => {
  if (r.bridgeToken) {
    document.getElementById("token").value = r.bridgeToken;
    verify(r.bridgeToken);
  }
});
