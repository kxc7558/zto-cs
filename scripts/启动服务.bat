@echo off
chcp 936 >nul
cd /d d:\zto-cs

if not exist .venv (
  echo [ZTO-CS] 首次运行：创建虚拟环境...
  python -m venv .venv
  echo [ZTO-CS] 安装依赖（清华镜像）...
  .venv\Scripts\pip install -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi uvicorn playwright httpx
  if errorlevel 1 (
    echo [ZTO-CS] 镜像失败，回退走 7890 代理...
    set HTTPS_PROXY=http://127.0.0.1:7890
    set HTTP_PROXY=http://127.0.0.1:7890
    .venv\Scripts\pip install -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi uvicorn playwright httpx
  )
)

echo [ZTO-CS] 启动服务（127.0.0.1:8765）...
start "" http://127.0.0.1:8765/docs
.venv\Scripts\python -m uvicorn api.main:app --host 127.0.0.1 --port 8765
pause
