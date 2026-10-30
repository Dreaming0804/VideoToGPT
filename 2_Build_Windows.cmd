@echo off
setlocal
cd /d "%~dp0"
set "PY="
if defined VIDEOTOGPT_PYTHON if exist "%VIDEOTOGPT_PYTHON%" set "PY=%VIDEOTOGPT_PYTHON%"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "D:\VideoText\.venv\Scripts\python.exe" set "PY=D:\VideoText\.venv\Scripts\python.exe"
if not defined PY set "PY=python"
"%PY%" -m pip install pyinstaller playwright
if errorlevel 1 goto fail
"%PY%" "%~dp0build_windows.py"
if errorlevel 1 goto fail
echo Portable build finished. Read build_log.txt for output path.
pause
exit /b 0
:fail
echo Build failed. Please inspect build_log.txt.
pause
exit /b 1
