run_demo.bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_find_python.bat" || exit /b 1
echo 用样例数据预览排版（不联网），先看清楚字号和位置是否合适。
%PYCMD% news_desktop.py --demo --debug
pause
