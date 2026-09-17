@echo off
rem mabi-playlist launcher. ASCII only (Korean console rule). Python must speak UTF-8.
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
start "" http://127.0.0.1:19997
python server.py
