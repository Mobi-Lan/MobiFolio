@echo off
rem MobiFolio MINI dev launcher: same backend, mini UI (mini\ui). ASCII only.
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
set MABI_UI_DIR=mini\ui
python server.py
