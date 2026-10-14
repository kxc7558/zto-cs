@echo off
chcp 936 >nul
title 中通客服智助 - 安装
cd /d %~dp0

echo ============================================
echo   中通客服智助 · 问题件助手 一键安装
echo ============================================
echo.

REM 1. 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
  echo [错误] 未检测到 Python。请先安装 Python 3.10 以上版本：
  echo        https://www.python.org/downloads/  （安装时勾选 Add to PATH）
  pause
  exit /b 1
)
echo [1/5] Python 已检测到

REM 2. 创建虚拟环境 + 装依赖（清华镜像，失败走代理）
if not exist .venv (
  echo [2/5] 首次安装：创建环境（约 1 分钟）...
  python -m venv .venv
  .venv\Scripts\pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
  if errorlevel 1 (
    echo [提示] 镜像失败，改走代理重试...
    set HTTPS_PROXY=http://127.0.0.1:7890
    set HTTP_PROXY=http://127.0.0.1:7890
    .venv\Scripts\pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
  )
) else (
  echo [2/5] 环境已存在，跳过
)

REM 3. 注册开机自启（计划任务，登录时自动启动服务）
echo [3/5] 注册开机自启...
schtasks /Create /F /SC ONLOGON /TN "ZTO-CS-Assistant" /TR "\"%~dp0scripts\启动服务.bat\"" >nul 2>&1
if errorlevel 1 (
  echo [提示] 自启动注册失败（可能权限不足），不影响使用，手动双击"启动服务.bat"即可
) else (
  echo       已注册：开机登录后自动启动
)

REM 3b. 放行局域网只读端口 8766（需管理员权限；失败不影响本机使用）
netsh advfirewall firewall add rule name="ZTO-CS-8766-ReadOnly" dir=in action=allow protocol=TCP localport=8766 >nul 2>&1

REM 4. 启动服务
echo [4/5] 启动服务...
call "%~dp0scripts\启动服务.bat"

REM 5. 显示机器码（远程更新登记用，发给你即可）
echo.
echo ============================================
echo   安装完成！
echo   工作台地址：http://127.0.0.1:8765
echo.
echo   [远程更新登记] 把下面这行发给管理员：
.venv\Scripts\python -c "from service.updater import machine_id; machine_id()" >nul 2>&1
type "%~dp0config\machine_id"
echo.
echo   访问码（工作台首次输入用，请一并抄给客服）：
.venv\Scripts\python -c "from shared import bridge_auth; bridge_auth.access_code()" >nul 2>&1
type "%~dp0config\access_code.txt"
echo   （或运行 scripts\显示机器码.bat 查看）
echo ============================================
pause
