@echo off
rem MobiFolio dev launcher: the python backend opens its own app window (Edge/Chrome --app). ASCII only.
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python server.py
