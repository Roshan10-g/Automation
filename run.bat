@echo off
title Keka Attendance Bot
cd /d "%~dp0"

echo ======================================================
echo           KEKA ATTENDANCE TELEGRAM BOT
echo ======================================================
echo.

if not exist ".env" (
    echo [NOTICE] .env file not found. Creating from .env.example...
    copy .env.example .env
    echo Please open .env and configure your TELEGRAM_BOT_TOKEN and KEKA_URL.
    pause
    exit /b
)

echo Checking dependencies...
python -c "import playwright, telegram, apscheduler" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing required packages...
    pip install -r requirements.txt
    playwright install chromium
)

echo.
echo Select an option:
echo  1. Start Telegram Bot (Daily Morning/Evening automation)
echo  2. Run One-Time Login Setup (Sign in via Microsoft/Google SSO)
echo  3. Test Keka Connection and Status
echo.
set /p opt="Choose (1/2/3): "

if "%opt%"=="1" (
    echo.
    echo Starting Telegram Bot...
    python bot.py
) else if "%opt%"=="2" (
    echo.
    echo Launching One-Time Login Setup...
    python setup_login.py
) else if "%opt%"=="3" (
    echo.
    echo Testing Keka Connection...
    python test_keka.py
) else (
    echo Invalid choice.
)

pause
