@echo off
:: ============================================================
:: NMS Service Installer
:: Registers all 3 NMS components as Windows services via NSSM.
:: Run as Administrator from the project root.
::
:: Usage:  deploy\install_services.bat
:: ============================================================

setlocal EnableDelayedExpansion

net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] This script must be run as Administrator.
    pause & exit /b 1
)

set "ROOT=%~dp0.."
set "NSSM=%~dp0tools\nssm.exe"
set "DIST=%ROOT%\dist"

set "ADMIN_EXE=%DIST%\NMSAdminServer\NMSAdminServer.exe"
set "AGENT_EXE=%DIST%\NMSCoreAgent\NMSCoreAgent.exe"
set "TRACKING_EXE=%DIST%\NMSTrackingAgent\NMSTrackingAgent.exe"

set "ADMIN_DIR=C:\NMS\NMSAdminServer"
set "AGENT_DIR=C:\NMS\NMSCoreAgent"
set "TRACKING_DIR=C:\NMS\NMSTrackingAgent"

set "ADMIN_LOG=C:\ProgramData\NMS\AdminServer"
set "AGENT_LOG=C:\ProgramData\nms-agent"
set "TRACKING_LOG=C:\ProgramData\NMS\TrackingAgent"

:: ── Preflight checks ───────────────────────────────────────────────────────
echo.
echo ========================================
echo  NMS Service Installer
echo ========================================
echo.

if not exist "%NSSM%" (
    echo [ERROR] nssm.exe not found at: %NSSM%
    echo         Make sure deploy\tools\nssm.exe exists.
    pause & exit /b 1
)

echo Checking build output...
set "MISSING=0"
if not exist "%ADMIN_EXE%"    ( echo   [!] Missing: %ADMIN_EXE%    & set "MISSING=1" )
if not exist "%AGENT_EXE%"    ( echo   [!] Missing: %AGENT_EXE%    & set "MISSING=1" )
if not exist "%TRACKING_EXE%" ( echo   [!] Missing: %TRACKING_EXE%" & set "MISSING=1" )
if "%MISSING%"=="1" (
    echo.
    echo Run deploy\build_all.bat first, then re-run this script.
    pause & exit /b 1
)
echo   All EXEs found.
echo.

:: ── Ask which components to install ───────────────────────────────────────
echo Which components do you want to install?
echo   [1] All three (Admin Server + Core Agent + Tracking Agent)
echo   [2] Admin Server only
echo   [3] Core Agent only
echo   [4] Tracking Agent only
echo.
set /p "CHOICE=Enter choice (1-4): "

set "DO_ADMIN=0"
set "DO_AGENT=0"
set "DO_TRACKING=0"

if "%CHOICE%"=="1" ( set "DO_ADMIN=1" & set "DO_AGENT=1" & set "DO_TRACKING=1" )
if "%CHOICE%"=="2" ( set "DO_ADMIN=1" )
if "%CHOICE%"=="3" ( set "DO_AGENT=1" )
if "%CHOICE%"=="4" ( set "DO_TRACKING=1" )

if "%DO_ADMIN%%DO_AGENT%%DO_TRACKING%"=="000" (
    echo Invalid choice. Exiting.
    pause & exit /b 1
)

:: ══════════════════════════════════════════════════════════════════════════
:: ADMIN SERVER
:: ══════════════════════════════════════════════════════════════════════════
if "%DO_ADMIN%"=="1" (
    echo.
    echo [1/3] Installing NMSAdminServer...

    :: Stop + remove if already registered
    "%NSSM%" status NMSAdminServer >nul 2>&1
    if not errorlevel 1 (
        echo       Removing existing service...
        "%NSSM%" stop NMSAdminServer >nul 2>&1
        "%NSSM%" remove NMSAdminServer confirm >nul 2>&1
    )

    :: Copy files
    if not exist "%ADMIN_DIR%" mkdir "%ADMIN_DIR%"
    if not exist "%ADMIN_LOG%"  mkdir "%ADMIN_LOG%"
    xcopy /E /I /Y "%DIST%\NMSAdminServer" "%ADMIN_DIR%" >nul
    copy /Y "%NSSM%" "%ADMIN_DIR%\nssm.exe" >nul

    :: Write .env if not present
    if not exist "%ADMIN_DIR%\.env" (
        if exist "%ROOT%\deploy\config.templates\nms-server.env.template" (
            copy /Y "%ROOT%\deploy\config.templates\nms-server.env.template" "%ADMIN_DIR%\.env" >nul
            echo.
            echo   *** ACTION REQUIRED ***
            echo   Edit %ADMIN_DIR%\.env before the service starts.
            echo   Set: SECRET_KEY, TRACKING_API_KEY, DATABASE_URL
            echo   Press any key after editing, or Ctrl+C to abort.
            pause >nul
        )
    )

    :: Register service
    "%NSSM%" install NMSAdminServer "%ADMIN_DIR%\NMSAdminServer.exe"
    "%NSSM%" set NMSAdminServer AppDirectory      "%ADMIN_DIR%"
    "%NSSM%" set NMSAdminServer DisplayName       "NMS Admin Server"
    "%NSSM%" set NMSAdminServer Description       "NMS Flask Dashboard (Waitress, port 5001)"
    "%NSSM%" set NMSAdminServer Start             SERVICE_AUTO_START
    "%NSSM%" set NMSAdminServer AppStdout         "%ADMIN_LOG%\stdout.log"
    "%NSSM%" set NMSAdminServer AppStderr         "%ADMIN_LOG%\stderr.log"
    "%NSSM%" set NMSAdminServer AppRotateFiles    1
    "%NSSM%" set NMSAdminServer AppRotateBytes    10485760
    "%NSSM%" set NMSAdminServer AppExit           Default Restart
    "%NSSM%" set NMSAdminServer AppRestartDelay   15000

    :: Restrict stop to Administrators + SYSTEM only
    sc sdset NMSAdminServer "D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)" >nul

    "%NSSM%" start NMSAdminServer
    echo       NMSAdminServer started.
    echo       Dashboard: http://localhost:5001
    echo       Logs:      %ADMIN_LOG%\stdout.log
)

:: ══════════════════════════════════════════════════════════════════════════
:: CORE AGENT
:: ══════════════════════════════════════════════════════════════════════════
if "%DO_AGENT%"=="1" (
    echo.
    echo [2/3] Installing NMSCoreAgent...

    "%NSSM%" status NMSCoreAgent >nul 2>&1
    if not errorlevel 1 (
        echo       Removing existing service...
        "%NSSM%" stop NMSCoreAgent >nul 2>&1
        "%NSSM%" remove NMSCoreAgent confirm >nul 2>&1
    )

    if not exist "%AGENT_DIR%" mkdir "%AGENT_DIR%"
    if not exist "%AGENT_LOG%"  mkdir "%AGENT_LOG%"
    xcopy /E /I /Y "%DIST%\NMSCoreAgent" "%AGENT_DIR%" >nul
    copy /Y "%NSSM%" "%AGENT_DIR%\nssm.exe" >nul

    :: Run config GUI so user sets server URL + token before service starts
    echo       Opening configuration window...
    "%AGENT_DIR%\NMSCoreAgent.exe" --configure

    "%NSSM%" install NMSCoreAgent "%AGENT_DIR%\NMSCoreAgent.exe"
    "%NSSM%" set NMSCoreAgent AppDirectory      "%AGENT_LOG%"
    "%NSSM%" set NMSCoreAgent DisplayName       "NMS Core Agent"
    "%NSSM%" set NMSCoreAgent Description       "NMS Metrics Collection Agent"
    "%NSSM%" set NMSCoreAgent Start             SERVICE_AUTO_START
    "%NSSM%" set NMSCoreAgent AppStdout         "%AGENT_LOG%\stdout.log"
    "%NSSM%" set NMSCoreAgent AppStderr         "%AGENT_LOG%\stderr.log"
    "%NSSM%" set NMSCoreAgent AppRotateFiles    1
    "%NSSM%" set NMSCoreAgent AppRotateBytes    10485760
    "%NSSM%" set NMSCoreAgent AppExit           Default Restart
    "%NSSM%" set NMSCoreAgent AppRestartDelay   10000

    sc sdset NMSCoreAgent "D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)" >nul

    "%NSSM%" start NMSCoreAgent
    echo       NMSCoreAgent started.
    echo       Logs: %AGENT_LOG%\stdout.log
)

:: ══════════════════════════════════════════════════════════════════════════
:: TRACKING AGENT
:: ══════════════════════════════════════════════════════════════════════════
if "%DO_TRACKING%"=="1" (
    echo.
    echo [3/3] Installing NMSTrackingAgent...

    "%NSSM%" status NMSTrackingAgent >nul 2>&1
    if not errorlevel 1 (
        echo       Removing existing service...
        "%NSSM%" stop NMSTrackingAgent >nul 2>&1
        "%NSSM%" remove NMSTrackingAgent confirm >nul 2>&1
    )

    if not exist "%TRACKING_DIR%" mkdir "%TRACKING_DIR%"
    if not exist "%TRACKING_LOG%"  mkdir "%TRACKING_LOG%"
    xcopy /E /I /Y "%DIST%\NMSTrackingAgent" "%TRACKING_DIR%" >nul
    copy /Y "%NSSM%" "%TRACKING_DIR%\nssm.exe" >nul

    :: Run config GUI
    echo       Opening configuration window...
    "%TRACKING_DIR%\NMSTrackingAgent.exe" --configure

    "%NSSM%" install NMSTrackingAgent "%TRACKING_DIR%\NMSTrackingAgent.exe"
    "%NSSM%" set NMSTrackingAgent AppDirectory      "%TRACKING_DIR%"
    "%NSSM%" set NMSTrackingAgent DisplayName       "NMS Tracking Agent"
    "%NSSM%" set NMSTrackingAgent Description       "NMS Employee Monitoring Agent"
    "%NSSM%" set NMSTrackingAgent Start             SERVICE_AUTO_START
    "%NSSM%" set NMSTrackingAgent AppStdout         "%TRACKING_LOG%\stdout.log"
    "%NSSM%" set NMSTrackingAgent AppStderr         "%TRACKING_LOG%\stderr.log"
    "%NSSM%" set NMSTrackingAgent AppRotateFiles    1
    "%NSSM%" set NMSTrackingAgent AppRotateBytes    10485760
    "%NSSM%" set NMSTrackingAgent AppExit           Default Restart
    "%NSSM%" set NMSTrackingAgent AppRestartDelay   10000

    sc sdset NMSTrackingAgent "D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)" >nul

    "%NSSM%" start NMSTrackingAgent
    echo       NMSTrackingAgent started.
    echo       Logs: %TRACKING_LOG%\stdout.log
)

:: ── Done ───────────────────────────────────────────────────────────────────
echo.
echo ========================================
echo  Installation complete.
echo  Verify in services.msc or run:
echo    sc query NMSAdminServer
echo    sc query NMSCoreAgent
echo    sc query NMSTrackingAgent
echo ========================================
pause
