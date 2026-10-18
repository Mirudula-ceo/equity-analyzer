@echo off
REM Opens the Equity Analyzer dashboard in your web browser (http://localhost:8501)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( echo Run setup.bat first. & pause & exit /b 1 )
REM Skip Streamlit's one-time "enter your email" question
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  echo [general]> "%USERPROFILE%\.streamlit\credentials.toml"
  echo email = "">> "%USERPROFILE%\.streamlit\credentials.toml"
)
echo Starting dashboard... it opens in your browser at http://localhost:8501
echo Close this window to stop the dashboard.
".venv\Scripts\python.exe" -m streamlit run app\Company_Search.py --server.headless false --browser.gatherUsageStats false
pause
