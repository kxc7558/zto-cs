@echo off
chcp 936 >nul
setlocal
set "CDP_DIR=%LOCALAPPDATA%\zto-cs\chrome-cdp\zto"
set "CHROME="
for %%P in (
  "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
  "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
) do if not defined CHROME if exist %%P set "CHROME=%%~P"
if not defined CHROME (
  echo [错误] 未找到 Chrome 浏览器，请先安装 Chrome 后重试。
  pause
  exit /b 1
)
echo [ZTO-CS] 启动专用调试 Chrome（CDP 9222，登录态保存在 %CDP_DIR%）
echo 提醒：请确认已关闭 VPN/代理，否则访问中通会 500
if not exist "%CDP_DIR%" mkdir "%CDP_DIR%"
start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%CDP_DIR%" --no-first-run --no-default-browser-check --no-proxy-server "https://ws.zto56.com/module/index?mv=index"
echo [ZTO-CS] 浏览器已启动。首次使用请在打开的窗口里登录中通（安盾/微信扫码）。
pause
