@echo off
cd /d "%~dp0"
echo Installing required Python packages...
where py >nul 2>nul && (py -m pip install --upgrade requests reportlab openpyxl holidays) || (python -m pip install --upgrade requests reportlab openpyxl holidays)
echo.
echo Done. Next: fill in config.ini, then run 2_login.bat
pause
