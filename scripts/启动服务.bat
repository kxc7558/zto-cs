@echo off
chcp 936 >nul
cd /d "%~dp0.."

if not exist runtime\python.exe (
  echo [ZTO-CS] 未安装：请先运行 安装.bat
  pause
  exit /b 1
)

echo [ZTO-CS] 启动服务...
echo   本机工作台：   http://127.0.0.1:8765
start "" http://127.0.0.1:8765
runtime\python.exe scripts\app.py
pause
