@echo off
rem Local read-only dashboard over the screener runs on http://127.0.0.1:8765 (Ctrl+C to stop).
rem Extra arguments are passed through, e.g.  run_dashboard.bat --port 8770
cd /d "%~dp0"
".venv\Scripts\python.exe" -m dashboard --open %*
