@echo off
echo Starting frontend dev server...
cd /d e:\qoderproject\20260527\frontend
rem 本地后端固定 8002,不设则 vite 代理默认打 8000
set VITE_API_TARGET=http://localhost:8002
call npm run dev
pause