@echo off
rem Build: 1) Python backend -> MabiScoreBox.exe  2) Electron shell -> dist-electron\<app>\  (ASCII only)
rem User data (dist-electron\*\data) is backed up to %TEMP%\scorebox_data_bak before packaging and restored after.
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
set "APPDIR="
for /d %%D in ("dist-electron\*-win32-x64") do set "APPDIR=%%~fD"
set "BAK=%TEMP%\scorebox_data_bak"
rmdir /s /q "%BAK%" 2>nul
if defined APPDIR if exist "%APPDIR%\data" (
  xcopy /e /i /q /y "%APPDIR%\data" "%BAK%" >nul
  echo data backed up to %BAK%
)
cd app
if not exist node_modules ( call npm install --no-audit --no-fund )
call npm run package
if errorlevel 1 (
  cd ..
  echo ELECTRON PACKAGE FAILED
  if exist "%BAK%" echo YOUR DATA IS SAFE AT: %BAK%  -- copy it back to dist-electron\...\data
  goto :fail
)
cd ..
set "APPDIR="
for /d %%D in ("dist-electron\*-win32-x64") do set "APPDIR=%%~fD"
if defined APPDIR if exist "%BAK%" (
  xcopy /e /i /q /y "%BAK%" "%APPDIR%\data" >nul
  rmdir /s /q "%BAK%" 2>nul
  echo data restored
)
echo BUILD OK: %APPDIR%
if not defined NOPAUSE pause
exit /b 0
:fail
if not defined NOPAUSE pause
exit /b 1
