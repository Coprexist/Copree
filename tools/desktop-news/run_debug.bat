run_debug.bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_find_python.bat" || exit /b 1
echo 调试窗口模式：有标题栏、不置底。关闭窗口或按 Ctrl+Alt+Q 退出。
%PYCMD% news_desktop.py --debug
pause
