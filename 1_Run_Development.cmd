@echo off
setlocal
cd /d "%~dp0"
set "PY="
if defined VIDEOTOGPT_PYTHON if exist "%VIDEOTOGPT_PYTHON%" set "PY=%VIDEOTOGPT_PYTHON%"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "D:\VideoText\.venv\Scripts\python.exe" set "PY=D:\VideoText\.venv\Scripts\python.exe"
if not defined PY set "PY=python"
"%PY%" -c "import faster_whisper, av, ctranslate2" >nul 2>nul
if errorlevel 1 (
 echo Python dependencies missing. See README.md and install requirements.txt.
 pause
 exit /b 1
)
"%PY%" "%~dp0VideoToGPT.py"
if errorlevel 1 (
 echo Application exited with an error.
 pause
 exit /b 1
)
exit /b 0
