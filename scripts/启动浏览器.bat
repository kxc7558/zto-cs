@echo off
chcp 936 >nul
echo [ZTO-CS] 启动专用调试浏览器（CDP 9222，登录态持久化在 chrome-cdp\zto）
echo 注意：请确保 VPN 已关闭，否则访问中通会 500
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\Users\32492\chrome-cdp\zto" --no-first-run --no-default-browser-check --no-proxy-server "https://ws.zto56.com/module/index?mv=index"
echo [ZTO-CS] 浏览器已启动。首次使用请在该窗口手动登录（安盾/微信扫码）。
pause
