@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
py -3.13 -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Setup complete. Open Run.cmd to start SCG Logger.
pause
exit /b 0
:failed
echo Setup failed. Check that Python 3.13 and the Python launcher are installed.
pause
exit /b 1
