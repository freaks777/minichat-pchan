@echo off
chcp 65001 > nul
REM RP Standalone Server Launcher
REM Usage: start_server.bat [--debug] [--model MODEL_ID]

set ROOT=%~dp0
set VENV_PYTHON=%ROOT%.venv\Scripts\python.exe

if not exist "%VENV_PYTHON%" (
    echo [ERROR] .venv not found.
    echo Run: uv venv ^&^& uv pip install -r requirements.txt sentence-transformers chromadb
    pause
    exit /b 1
)

cd /d "%ROOT%backend"

REM Clear PYTHONPATH to avoid Hermes Agent venv contamination
set PYTHONPATH=

REM HuggingFace cache
set HF_HOME=E:\LLM\models
set SENTENCE_TRANSFORMERS_HOME=E:\LLM\models

echo === RP Standalone Server ===
echo Port: 8765
echo Python: %VENV_PYTHON%
echo.

"%VENV_PYTHON%" main.py %*

pause
