@echo off
title Prompt Review
cd /d "%~dp0"
echo 1) Phase 2 (last_prompt)
echo 2) Phase 3 (last_phase3)
echo 3) Phase 1 (last_phase1)
echo 4) ALL three phases
echo 5) Custom path
set /p ch="Choice (1-5): "

if "%ch%"=="1" python review_prompt.py last_prompt
if "%ch%"=="2" python review_prompt.py last_phase3
if "%ch%"=="3" python review_prompt.py last_phase1
if "%ch%"=="4" python review_prompt.py all
if "%ch%"=="5" (
    set /p p="Path: "
    python review_prompt.py "%p%"
)
if errorlevel 1 (
    echo.
    echo Error occurred.
    pause
)
pause
