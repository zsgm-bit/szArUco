@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0..\.venv\Scripts\python.exe"
if exist "%PY%" goto build
echo [setup] .venv not found, please run run.bat first.
pause
exit /b 1
:build
"%PY%" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ pyinstaller
"%PY%" build_exe.py
echo.
echo [done] Output in dist\ folder.
pause
