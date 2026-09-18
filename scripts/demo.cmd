@echo off
rem MobiFolio demo launcher: fake CLI (demo\demo_cli.py) + demo data folder. No game required. ASCII only.
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set MABI_DEMO=1
set MABI_DATA_DIR=%LOCALAPPDATA%\MobiFolio-demo
cd /d "%~dp0.."
python demo\demo_seed.py
python server.py
