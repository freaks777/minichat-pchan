@echo off
REM RP Standalone Server Launcher
REM Usage: start_server.bat [--debug] [--model MODEL_ID]

set WORKDIR=%~dp0backend

cd /d "%WORKDIR%"

echo === RP Standalone Server ===
echo Port: 8765
echo.

python main.py %*

pause
