@echo off
powershell -NoProfile -Command "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'main\\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
if exist bot.pid del bot.pid 2>nul
timeout /t 2 /nobreak >nul
start /min "" python main.py
echo Bot restarted.
