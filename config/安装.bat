@echo off
chcp 936 >nul
title 中通客服智助 - 安装
cd /d "%~dp0"

echo ============================================
echo   中通客服智助 · 问题件助手 一键安装
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
  echo [错误] 未检测到 Python。请先安装 Python 3.10 以上版本：
  echo        https://www.python.org/downloads/  （安装时勾选 Add to PATH）
  pause
  exit /b 1
)
echo [1/5] Python 已检测到

if not exist .venv (
  echo [2/5] 首次安装：创建环境（约 2 分钟）...
  python -m venv .venv
  .venv\Scripts\pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r configequirements.txt
  if errorlevel 1 (
    echo [提示] 镜像失败，改走代理重试...
    set HTTPS_PROXY=http://127.0.0.1:7890
    set HTTP_PROXY=http://127.0.0.1:7890
    .venv\Scripts\pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r configequirements.txt
  )
) else (
  echo [2/5] 环境已存在
)

echo [3/5] 注册开机自启...
schtasks /Create /F /SC ONLOGON /TN "ZTO-CS-Assistant" /TR "\"%~dp0scripts\启动服务.bat\"" >nul 2>&1
if errorlevel 1 (
  echo       自启注册失败（权限不足），不影响使用，双击"启动服务.bat"即可
) else (
  echo       已注册：开机登录后自动启动
)

echo [4/5] 启动服务...
call "%~dp0scripts\启动服务.bat"

echo [5/5] 完成！
echo.
echo ============================================
echo   安装完成！
echo   工作台地址：http://127.0.0.1:8765
echo.
echo   【访问码】首次打开工作台页面时输入（8 位）：
for /f %%i in ('type config\bridge_secret.key 2^>nul') do set ACCESS=%%i
echo   %ACCESS%
echo.
echo   【机器码】发管理员开通远程更新：
echo   %~dp0config\machine_id
echo ============================================
pause
