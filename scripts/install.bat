@echo off
rem ---------------------------------------------------------------
rem  Copree launcher installer for Windows.
rem
rem  This file is deliberately ASCII-only and CRLF: cmd.exe reads batch
rem  files in the console code page, so any Chinese literal here would be
rem  written into the .lnk as mojibake. All localized strings live in
rem  create-shortcuts.ps1 (UTF-8 with BOM, decoded by spec).
rem ---------------------------------------------------------------
chcp 65001 >nul
title Copree installer
setlocal

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo  [i] Administrator rights are needed to create the shortcuts.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "INSTALL_DIR=%LOCALAPPDATA%\Copree"
set "EXE_NAME=Copree.exe"
echo   Install dir: %INSTALL_DIR%

if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
echo  [OK] install dir

echo  [..] copying files
xcopy /E /I /Y "%~dp0dist\Copree\*" "%INSTALL_DIR%\" >nul 2>&1
echo  [OK] files copied

echo  [..] creating shortcuts
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create-shortcuts.ps1" -InstallDir "%INSTALL_DIR%" -ExeName "%EXE_NAME%"
if errorlevel 1 (
    echo  [x] failed to create shortcuts
    exit /b 1
)

echo.
echo   Done. Launcher: %INSTALL_DIR%\%EXE_NAME%
echo.
pause
