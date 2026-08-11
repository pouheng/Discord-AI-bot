@echo off
setlocal
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul
if not errorlevel 1 goto :run
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
goto :run

:run
echo ========================================
echo  Shu Shi Dui Zhuang Xi Tong - Guan Li Ye
echo ========================================
echo.
echo Using: %PY%
echo Starting server... (browser will open automatically)
echo Press Ctrl+C to stop.
echo.
%PY% collide_ui.py

echo.
echo Server stopped or failed.
echo ========================================
pause
endlocal