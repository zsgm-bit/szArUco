@echo off
setlocal
cd /d "%~dp0"
set "VENV=%~dp0..\.venv"
if exist "%VENV%\Scripts\python.exe" goto run
echo [setup] Creating virtual environment at ..\.venv ...
python -m venv "%VENV%"
"%VENV%\Scripts\python.exe" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ -r "%~dp0requirements.txt"
:run
"%VENV%\Scripts\python.exe" "%~dp0main.py"
if errorlevel 1 pause
