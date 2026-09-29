@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv || (echo Python not found. Install it from python.org & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt || (echo pip install failed & pause & exit /b 1)
echo.
echo CivilDesk is running:  http://localhost:8000
echo Close this window to stop.
start "" http://localhost:8000
python -m uvicorn civildesk.main:app --port 8000
pause
