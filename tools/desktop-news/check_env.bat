check_env.bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_find_python.bat" || exit /b 1
%PYCMD% news_desktop.py --check
pause
