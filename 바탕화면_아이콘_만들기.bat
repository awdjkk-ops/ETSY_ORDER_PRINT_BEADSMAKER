@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_shortcut.ps1"
if errorlevel 1 (
  echo.
  echo 아이콘을 만들지 못했습니다. 위 내용을 확인해 주세요.
  pause
  exit /b 1
)
echo.
echo 바탕화면에 "Etsy 주문서 관리" 아이콘을 만들었습니다.
timeout /t 3 >nul
