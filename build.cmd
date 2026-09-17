@echo off
rem Build: 1) Python backend -> MabiScoreBox.exe  2) Electron shell -> dist-electron\<app>\  ASCII only.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
python -m pip install --quiet --disable-pip-version-check pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name MabiScoreBox --add-data "ui;ui" server.py
if errorlevel 1 ( echo BACKEND BUILD FAILED & exit /b 1 )
copy /y dist\MabiScoreBox.exe MabiScoreBox.exe >nul
cd app
if not exist node_modules ( call npm install --no-audit --no-fund )
call npm run package
if errorlevel 1 ( echo ELECTRON PACKAGE FAILED & exit /b 1 )
cd ..
echo BUILD OK: %~dp0dist-electron\
