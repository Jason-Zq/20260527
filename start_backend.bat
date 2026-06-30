@echo off
chcp 65001 >nul
echo ============================================
echo  Doc Review 本地启动 (后端 + 1 个 OCR worker)
echo ============================================
echo.
echo 后端和 worker 是两个独立进程,会各自开一个窗口。
echo 关闭对应窗口即停止该进程。
echo.

set ROOT=e:\qoderproject\20260527
set PY=%ROOT%\.venv312\Scripts\python.exe

start "doc-review-backend" cmd /k "cd /d %ROOT%\backend && set PYTHONIOENCODING=utf-8 && set PYTHONUTF8=1 && %PY% -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

echo 等待后端起来再拉 worker(避免同时初始化抢资源)...
timeout /t 8 /nobreak >nul

start "doc-review-worker-1" cmd /k "cd /d %ROOT%\backend && set PYTHONIOENCODING=utf-8 && set PYTHONUTF8=1 && %PY% -m worker_runner --worker-id local-worker-1"

echo.
echo 已分别启动后端与 worker。健康检查: http://localhost:8000/api/healthz
