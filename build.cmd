@echo off
rem Build: 1) Python backend -> MobiFolioCore.exe  2) Electron shell -> dist-electron\<app>\  (ASCII only)
rem User data lives in %LOCALAPPDATA%\MobiFolio (not inside dist-electron), so packaging never touches it.
rem Usage: build.cmd [--nopause]
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set "NOPAUSE="
if /i "%~1"=="--nopause" set "NOPAUSE=1"
tasklist /fi "imagename eq MobiFolioCore.exe" 2>nul | find /i "MobiFolioCore.exe" >nul
if not errorlevel 1 ( echo CLOSE THE APP FIRST: MobiFolioCore.exe is running & goto :fail )
python -m pip install --quiet --disable-pip-version-check "pyinstaller==6.22.3"
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name MobiFolioCore --add-data "ui;ui" --version-file version_info.txt --noupx --icon app\icon\MobiFolio.ico server.py
if errorlevel 1 ( echo BACKEND BUILD FAILED & goto :fail )
rem Lite edition: same backend, started directly (opens an Edge/Chrome app window itself; no Electron). Single ~9 MB exe.
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name MobiFolioLite --add-data "ui;ui" --version-file version_info_lite.txt --noupx --icon app\icon\MobiFolio.ico server.py
if errorlevel 1 ( echo LITE BUILD FAILED & goto :fail )
cd app
if not exist node_modules ( call npm ci --no-audit --no-fund )
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
