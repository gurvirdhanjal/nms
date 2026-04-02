@echo off
:: ============================================================
:: install_service.bat — Install NMSAdmin as a Windows service
:: using NSSM (Non-Sucking Service Manager).
::
:: Requirements:
::   - Run as Administrator
::   - NSSM must be in PATH or in this folder
::
:: Download NSSM: https://nssm.cc/download
:: ============================================================

set SERVICE_NAME=NMSAdmin
set EXE_PATH=%~dp0NMSAdmin.exe
set LOG_DIR=%~dp0logs

:: Create logs directory
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: Check admin rights
net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] This script must be run as Administrator.
    pause
    exit /b 1
)

:: Check NSSM
where nssm >nul 2>&1
if errorlevel 1 (
    if exist "%~dp0nssm.exe" (
        set PATH=%~dp0;%PATH%
    ) else (
        echo [ERROR] nssm.exe not found in PATH or current folder.
        echo         Download from https://nssm.cc/download and place next to this script.
        pause
        exit /b 1
    )
)

echo [1/3] Stopping existing service (if any)...
nssm stop %SERVICE_NAME% >nul 2>&1
nssm remove %SERVICE_NAME% confirm >nul 2>&1

echo [2/3] Installing service...
nssm install %SERVICE_NAME% "%EXE_PATH%"
nssm set %SERVICE_NAME% AppDirectory "%~dp0"
nssm set %SERVICE_NAME% DisplayName "NMS Admin Panel"
nssm set %SERVICE_NAME% Description "Network Monitoring System — Admin Server (Flask + Waitress)"
nssm set %SERVICE_NAME% Start SERVICE_AUTO_START
nssm set %SERVICE_NAME% AppStdout "%LOG_DIR%\nmsadmin.log"
nssm set %SERVICE_NAME% AppStderr "%LOG_DIR%\nmsadmin_error.log"
nssm set %SERVICE_NAME% AppRotateFiles 1
nssm set %SERVICE_NAME% AppRotateBytes 10485760

echo [3/3] Starting service...
nssm start %SERVICE_NAME%

echo.
echo [OK] Service "%SERVICE_NAME%" installed and started.
echo      Logs: %LOG_DIR%\
echo      Stop:    nssm stop %SERVICE_NAME%
echo      Restart: nssm restart %SERVICE_NAME%
echo      Remove:  nssm remove %SERVICE_NAME% confirm
echo.
pause
