@echo off
setlocal

cd /d "%~dp0"

set "APP_PID=%~1"

timeout /t 2 /nobreak >nul

if not "%APP_PID%"=="" (
  echo [SOFTWARE UPDATE] Cerrando PID %APP_PID%
  taskkill /F /PID %APP_PID% >nul 2>nul
)

timeout /t 1 /nobreak >nul

echo [SOFTWARE UPDATE] Reiniciando uvicorn...
start "" cmd /c ".venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000"

endlocal
