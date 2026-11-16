@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
where py >nul 2>nul && (py autoprint.py --auto) || (python autoprint.py --auto)
