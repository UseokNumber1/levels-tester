@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment ^(one-time^)...
    python -m venv .venv
    if errorlevel 1 exit /b %errorlevel%
)

rem Dependencies are installed only once; a working import skips this step.
.venv\Scripts\python.exe -c "import level_tester.api.app" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies ^(one-time^)...
    .venv\Scripts\python.exe -m pip install -e ".[dev]"
    if errorlevel 1 exit /b %errorlevel%
)

echo Starting Levels Tester at http://127.0.0.1:8000/
.venv\Scripts\python.exe -m uvicorn level_tester.api.app:app --host 127.0.0.1 --port 8000
