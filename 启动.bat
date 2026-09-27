@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "APP_PY=%LOCALAPPDATA%\MHXYHelper\runtime\python311\python.exe"
if exist "%APP_PY%" (
  "%APP_PY%" start.py
  exit /b %errorlevel%
)

echo [ERROR] Compatible runtime not found.
echo Please reinstall the application runtime, then start again.
pause
exit /b 1
