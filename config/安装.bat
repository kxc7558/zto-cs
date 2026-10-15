@echo off
chcp 936 >nul
title 中通客服智助 - 安装
cd /d "%~dp0"

echo ============================================
echo   中通客服智助 · 问题件助手 一键安装
echo   （自带运行环境，无需安装 Python）
echo ============================================
echo.

if not exist runtime\python.exe (
  echo [错误] runtime 目录缺失（安装包不完整），请联系管理员
  pause
  exit /b 1
)
echo [1/5] 自带运行环境检测 OK

echo [2/5] 注册开机自启...
schtasks /Create /F /SC ONLOGON /TN "ZTO-CS-Assistant" /TR "\"%~dp0scripts\启动服务.bat\"" >nul 2>&1
if errorlevel 1 (
  echo       自启注册失败（权限不足），不影响使用
) else (
  echo       已注册：开机登录后自动启动
)

echo [3/5] 创建桌面快捷方式...
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\问题件助手.lnk'); $s.TargetPath = '%~dp0runtime\pythonw.exe'; $s.Arguments = '%~dp0scripts\app.py'; $s.WorkingDirectory = '%~dp0'; $s.Save()" >nul 2>&1
if exist "%USERPROFILE%\Desktop\问题件助手.lnk" (
  echo       桌面快捷方式已创建：问题件助手
) else (
  echo       快捷方式创建失败，可手动双击 runtime\python.exe scripts\app.py
)

echo [4/5] 启动服务...
call "%~dp0scripts\启动服务.bat"

echo [5/5] 完成！
echo.
echo ============================================
echo   安装完成！桌面双击「问题件助手」即可打开
echo   工作台：http://127.0.0.1:8765
echo.
for /f %%i in ('type config\access_code.txt 2^>nul') do set ACCESS=%%i
echo   【访问码】工作台首次打开时输入（8 位）：
echo   %ACCESS%
echo   【机器码】发管理员开通远程更新：
echo   %~dp0config\machine_id
echo ============================================
pause
