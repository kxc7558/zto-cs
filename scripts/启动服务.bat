@echo off
chcp 936 >nul
cd /d "%~dp0.."

if not exist .venv (
  echo [ZTO-CS] 首次运行：创建虚拟环境...
  python -m venv .venv
  echo [ZTO-CS] 安装依赖（清华镜像）...
  .venv\Scripts\pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
  if errorlevel 1 (
    echo [提示] 镜像失败，回退走 7890 代理...
    set HTTPS_PROXY=http://127.0.0.1:7890
    set HTTP_PROXY=http://127.0.0.1:7890
    .venv\Scripts\pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
  )
)

echo [ZTO-CS] 启动服务...
echo   本机工作台：   http://127.0.0.1:8765
echo   局域网只读页： http://本机IP:8766 （其他客服查看用）
start "" http://127.0.0.1:8765
.venv\Scripts\python scripts\start_all.py
pause
