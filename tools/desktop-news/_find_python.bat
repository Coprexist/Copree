_find_python.bat
@echo off
rem 统一的 Python 定位逻辑：先把 PYCMD 设好供调用方使用
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD (
  echo.
  echo 未找到 Python。请到 https://www.python.org/downloads/ 安装 Python 3.9 或更高版本，
  echo 安装时务必勾选 "Add python.exe to PATH" 和 "tcl/tk and IDLE"。
  echo.
  pause
  exit /b 1
)
exit /b 0
