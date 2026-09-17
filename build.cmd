@echo off
rem Build a single-file exe (no console, opens browser). ASCII only (Korean console rule).
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
python -m pip install --quiet --disable-pip-version-check pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name MabiScoreBox --add-data "ui;ui" server.py
if errorlevel 1 (
  echo BUILD FAILED
  exit /b 1
)
echo BUILD OK: %~dp0dist\MabiScoreBox.exe
