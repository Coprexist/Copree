run_once.bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_find_python.bat" || exit /b 1
echo 立即抓取一次（不显示界面），结束后按任意键关闭。
%PYCMD% news_desktop.py --once
pause
