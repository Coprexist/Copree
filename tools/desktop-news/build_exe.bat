build_exe.bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_find_python.bat" || exit /b 1
echo 打包成单文件 exe（需要联网安装 pyinstaller，仅打包机需要）
%PYCMD% -m pip install --upgrade pyinstaller || goto :failed
%PYCMD% -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name DesktopNews ^
  --add-data "config.example.json;." ^
  news_desktop.py || goto :failed
echo.
echo 打包完成：dist\DesktopNews.exe
echo 把 dist\DesktopNews.exe 复制到目标机任意目录，双击运行，再用 install_autostart.bat 装自启。
pause
exit /b 0
:failed
echo 打包失败，请检查上面的错误信息。
pause
exit /b 1
