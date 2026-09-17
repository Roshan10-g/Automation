@echo off
title Keka Session Sync
cd /d "%~dp0"
echo ===================================================
echo        Starting Keka Attendance Session Sync
echo ===================================================
echo.
python sync_login_helper.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] An issue occurred during sync.
    echo Press any key to close...
    pause >nul
) else (
    echo.
    echo [SUCCESS] Everything completed successfully.
    echo Closing window in 8 seconds...
    timeout /t 8 >nul
)
