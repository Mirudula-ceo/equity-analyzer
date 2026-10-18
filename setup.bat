@echo off
REM ============================================================
REM  One-time setup: virtual environment + packages + tests
REM  Full log: reports\setup_log.txt
REM ============================================================
cd /d "%~dp0"
if not exist reports mkdir reports
set LOG=reports\setup_log.txt
echo Setup started %date% %time% > %LOG%
REM remove obsolete YAML config files from the first version
if exist config\settings.yaml del config\settings.yaml
if exist config\qualitative_evidence.yaml del config\qualitative_evidence.yaml
echo Checking Python...
set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" ( where python >nul 2>nul && set PY=python )
if "%PY%"=="" (
  echo Python was not found. Install it from https://www.python.org/downloads/ and tick "Add to PATH".
  echo PYTHON NOT FOUND >> %LOG%
  pause & exit /b 1
)
%PY% --version
%PY% --version >> %LOG% 2>&1
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment in .venv ...
  %PY% -m venv .venv >> %LOG% 2>&1 || (echo VENV FAILED >> %LOG% & echo Failed to create virtual environment & pause & exit /b 1)
)
echo Installing packages (first time takes a few minutes; details in %LOG%)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >> %LOG% 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt >> %LOG% 2>&1
if errorlevel 1 (
  echo PIP INSTALL FAILED >> %LOG%
  echo Package install failed - see %LOG%
  pause & exit /b 1
)
echo PIP INSTALL OK >> %LOG%
echo Running automated tests...
".venv\Scripts\python.exe" -m pytest -q tests > reports\test_results.txt 2>&1
type reports\test_results.txt
echo SETUP FINISHED >> %LOG%
echo.
echo Setup finished. Next: double-click run_full_pipeline.bat (downloads data and runs backtests).
pause
