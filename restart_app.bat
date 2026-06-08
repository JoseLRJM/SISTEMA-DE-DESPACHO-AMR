@echo off
setlocal

cd /d "%~dp0"

set "APP_PID=%~1"
if "%APP_HOST%"=="" set "APP_HOST=0.0.0.0"
if "%APP_PORT%"=="" set "APP_PORT=8000"
if exist ".venv\Scripts\python.exe" (
  set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
  set "PYTHON_EXE=python"
)

echo [SOFTWARE UPDATE] cwd=%CD%
echo [SOFTWARE UPDATE] script=%~f0
echo [SOFTWARE UPDATE] target_pid=%APP_PID%
echo [SOFTWARE UPDATE] host=%APP_HOST% port=%APP_PORT%
echo [SOFTWARE UPDATE] python=%PYTHON_EXE%

timeout /t 2 /nobreak >nul

if not "%APP_PID%"=="" (
  echo [SOFTWARE UPDATE] Cerrando PID %APP_PID%
  taskkill /F /PID %APP_PID% >nul 2>nul
)

timeout /t 1 /nobreak >nul

echo [SOFTWARE UPDATE] Reiniciando uvicorn...
start "AGV APP" cmd /c "%PYTHON_EXE% -m uvicorn main:app --host %APP_HOST% --port %APP_PORT%"

endlocal
