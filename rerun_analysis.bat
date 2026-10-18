@echo off
REM Re-scores companies and re-runs backtests using data already downloaded (no internet needed).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( echo Run setup.bat first. & pause & exit /b 1 )
".venv\Scripts\python.exe" scripts\run_all.py --skip-download
echo.
echo Report: reports\RUN_REPORT.md
pause
