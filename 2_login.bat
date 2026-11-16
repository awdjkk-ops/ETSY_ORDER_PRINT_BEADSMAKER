@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
where py >nul 2>nul && (py orderprint.py login) || (python orderprint.py login)
pause
