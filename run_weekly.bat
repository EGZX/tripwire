@echo off
rem Weekly screener run. Writes reports\screen_<date>.xlsx/.md and a log in reports\logs.
cd /d "%~dp0"
if not exist reports\logs mkdir reports\logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i
".venv\Scripts\python.exe" -m screener >> "reports\logs\run_%TODAY%.log" 2>&1
