@echo off
title Instalador AGV App (Offline)

echo ================================
echo Instalando AGV App...
echo ================================

REM Ir a la carpeta base
cd /d %~dp0

REM Verificar Python embebido
if not exist python\python.exe (
    echo ERROR: No se encontro python embebido
    pause
    exit /b
)

REM Verificar paquetes
if not exist paquetes (
    echo ERROR: No se encontro carpeta paquetes
    pause
    exit /b
)

REM Instalar pip si no existe
echo Verificando pip...
python\python.exe -m pip --version >nul 2>&1
if errorlevel 1 (
    echo Instalando pip...
    python\python.exe get-pip.py
)

REM Instalar dependencias offline
echo Instalando dependencias...
python\python.exe -m pip install --no-index --find-links=paquetes -r agv-app\requirements.txt

if errorlevel 1 (
    echo ERROR instalando dependencias
    pause
    exit /b
)

echo ================================
echo Instalacion completada
echo ================================

REM Ejecutar app automaticamente
call run.cmd