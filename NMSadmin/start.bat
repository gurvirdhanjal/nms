@echo off
setlocal

:: ============================================================
:: start.bat — Launch the NMS Admin Panel server
:: Run from the NMSadmin\ folder
:: ============================================================

set EXE=NMSAdmin.exe

if not exist "%EXE%" (
    echo [ERROR] %EXE% not found.
    echo         Run build_nmsadmin.bat from the project root first.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [WARNING] .env file not found.
    echo           Copy .env.example to .env and fill in your values.
    echo           Attempting to start with defaults anyway...
    echo.
)

echo [NMS] Starting NMS Admin Panel...
echo [NMS] Server will be available at http://localhost:5000
echo [NMS] Press Ctrl+C to stop.
echo.

%EXE%
endlocal
