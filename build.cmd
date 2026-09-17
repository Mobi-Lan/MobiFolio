@echo off
rem Build: 1) Python backend -> MabiScoreBox.exe  2) Electron shell -> dist-electron\<app>\  (ASCII only)
rem User data (dist-electron\*\data) is backed up before packaging and restored after.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
python -m pip install --quiet --disable-pip-version-check pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name MabiScoreBox --add-data "ui;ui" server.py
if errorlevel 1 ( echo BACKEND BUILD FAILED & pause & exit /b 1 )
copy /y dist\MabiScoreBox.exe MabiScoreBox.exe >nul
set "APPDIR="
for /d %%D in ("dist-electron\*-win32-x64") do set "APPDIR=%%~fD"
if defined APPDIR if exist "%APPDIR%\data" (
  rmdir /s /q "%TEMP%\scorebox_data_bak" 2>nul
  xcopy /e /i /q /y "%APPDIR%\data" "%TEMP%\scorebox_data_bak" >nul
  echo data backed up
)
cd app
if not exist node_modules ( call npm install --no-audit --no-fund )
call npm run package
if errorlevel 1 ( echo ELECTRON PACKAGE FAILED & pause & exit /b 1 )
cd ..
for /d %%D in ("dist-electron\*-win32-x64") do set "APPDIR=%%~fD"
if exist "%TEMP%\scorebox_data_bak" (
  xcopy /e /i /q /y "%TEMP%\scorebox_data_bak" "%APPDIR%\data" >nul
  echo data restored
)
echo BUILD OK: %APPDIR%
pause
