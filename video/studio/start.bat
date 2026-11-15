@echo off
rem Video Studio: http://127.0.0.1:8001/  (needs the Ovoz app on :8000 for voice-over)
setlocal
set "HERE=%~dp0"
set "PYTHONUTF8=1"
cd /d "%HERE%"
"%HERE%..\.venv\Scripts\python.exe" -m uvicorn server:app --app-dir "%HERE%." --host 127.0.0.1 --port 8001 %*
