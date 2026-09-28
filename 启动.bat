@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 优先兼容旧版随应用附带的运行时，再查找系统安装的 Python 3.10+。
set "APP_PY="
set "APP_PY_IS_PATH="
set "BUNDLED_PY=%LOCALAPPDATA%\MHXYHelper\runtime\python311\python.exe"
if exist "%BUNDLED_PY%" (
  "%BUNDLED_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "APP_PY=%BUNDLED_PY%"
    set "APP_PY_IS_PATH=1"
  )
)

if not defined APP_PY (
  py -3.13 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 set "APP_PY=py -3.13"
)
if not defined APP_PY (
  py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 set "APP_PY=py -3.12"
)
if not defined APP_PY (
  py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 set "APP_PY=py -3.11"
)
if not defined APP_PY (
  py -3.10 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 set "APP_PY=py -3.10"
)
if not defined APP_PY (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 set "APP_PY=python"
)

if not defined APP_PY (
  echo [ERROR] Compatible Python runtime not found.
  echo Please install Python 3.10 or later from https://www.python.org/downloads/
  echo During installation, select "Add Python to PATH".
  pause
  exit /b 1
)

if defined APP_PY_IS_PATH (
  "%APP_PY%" start.py
) else (
  %APP_PY% start.py
)
exit /b %errorlevel%
