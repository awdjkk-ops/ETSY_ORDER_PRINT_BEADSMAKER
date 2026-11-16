@echo off
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw etsy_app.py) || (start "" pythonw etsy_app.py)
