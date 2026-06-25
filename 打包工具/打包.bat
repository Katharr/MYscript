@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Please install Python 3.10+ and check "Add to PATH".
  pause
  exit /b
)
echo 正在打包 exe，请稍候……
python build.py
echo.
pause
