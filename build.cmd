@echo off
rem Build: 1) Python backend -> MabiScoreBox.exe  2) Electron shell -> dist-electron\<app>\  (ASCII only)
rem User data lives in %LOCALAPPDATA%\MabiScoreBox (not inside dist-electron), so packaging never touches it.
rem Usage: build.cmd [--nopause]
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set "NOPAUSE="
if /i "%~1"=="--nopause" set "NOPAUSE=1"
tasklist /fi "imagename eq MabiScoreBox.exe" 2>nul | find /i "MabiScoreBox.exe" >nul
if not errorlevel 1 ( echo CLOSE THE APP FIRST: MabiScoreBox.exe is running & goto :fail )
python -m pip install --quiet --disable-pip-version-check pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name MabiScoreBox --add-data "ui;ui" server.py
if errorlevel 1 ( echo BACKEND BUILD FAILED & goto :fail )
copy /y dist\MabiScoreBox.exe MabiScoreBox.exe >nul
if errorlevel 1 ( echo COPY FAILED: MabiScoreBox.exe is locked? & goto :fail )
cd app
if not exist node_modules ( call npm install --no-audit --no-fund )
call npm run package
if errorlevel 1 ( cd .. & echo ELECTRON PACKAGE FAILED & goto :fail )
cd ..
set "APPDIR="
for /d %%D in ("dist-electron\*-win32-x64") do set "APPDIR=%%~fD"
echo BUILD OK: %APPDIR%
if not defined NOPAUSE pause
exit /b 0
:fail
if not defined NOPAUSE pause
exit /b 1
