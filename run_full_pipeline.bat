@echo off
REM Downloads Nifty 50 data, verifies it, scores companies, runs backtests.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( echo Run setup.bat first. & pause & exit /b 1 )
".venv\Scripts\python.exe" scripts\run_all.py %* 2>&1 | ".venv\Scripts\python.exe" -c "import sys;f=open('reports/pipeline_log.txt','w',encoding='utf-8');[ (sys.stdout.write(l),sys.stdout.flush(),f.write(l)) for l in sys.stdin]"
echo.
echo Report: reports\RUN_REPORT.md
pause
