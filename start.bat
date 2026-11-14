@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Setting up virtual environment...
  py -3.12 -m venv .venv 2>nul || python -m venv .venv || goto :error
  .venv\Scripts\python -m pip install -q -r requirements.txt || goto :error
  echo.
  echo Online model ready. For local GPU models see README.md ^(requirements-gpu.txt^).
)
start "" http://localhost:8000
.venv\Scripts\python -m uvicorn app:app --port 8000
goto :eof
:error
echo Setup failed. Make sure Python 3.12 is installed.
pause
