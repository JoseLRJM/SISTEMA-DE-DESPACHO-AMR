@echo off
title AGV App

echo ================================
echo Iniciando AGV App (5 workers)...
echo ================================

cd /d %~dp0agv-app

REM Ejecutar servidor con 5 workers
..\python\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000

pause
