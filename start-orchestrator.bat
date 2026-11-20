@echo off
setlocal
cd /d "%~dp0"
echo Starting AutoTrading Ideal Platform Orchestrator (standalone admin)...
echo.
python -m orchestrator
if errorlevel 1 (
  echo.
  echo Failed to start. Ensure Python 3 is installed and on PATH.
  exit /b 1
)
