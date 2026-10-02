install_autostart.bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_find_python.bat" || exit /b 1
%PYCMD% autostart.py install --delay 20
echo.
%PYCMD% autostart.py status
pause
