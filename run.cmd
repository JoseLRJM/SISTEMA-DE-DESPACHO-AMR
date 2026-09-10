@echo off
title AGV App

echo ================================
echo Iniciando AGV App (1 worker - unico modo soportado)...
echo ================================

cd /d %~dp0agv-app

REM Un runtime por base local. No usar workers ni replicas adicionales.
set WEB_CONCURRENCY=1
..\python\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 --lifespan on

pause
