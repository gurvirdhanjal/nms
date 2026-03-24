@echo off
:: Install NMS Admin Server EXE as a Windows service via NSSM.
:: Run as Administrator from the project root.
::
:: Prerequisites:
::   1. dist\NMSAdminServer\ folder built with NMSAdminServer.spec
::   2. deploy\tools\nssm.exe present (64-bit)
::   3. PostgreSQL 16 + TimescaleDB Windows service running on port 5433
::   4. Memurai (Redis-compatible) Windows service running on port 6379

set INSTALL_DIR=C:\NMS\NMSAdminServer
set LOG_DIR=C:\ProgramData\nms-server
set NSSM=%~dp0tools\nssm.exe

echo [1/5] Checking build output...
if not exist "dist\NMSAdminServer\NMSAdminServer.exe" (
    echo.
    echo     BUILD NOT FOUND. Run this first from the project root:
    echo       scripts\build_agents.bat
    echo.
    echo     Then re-run this installer.
    pause & exit /b 1
)

echo        Copying build to install directory...
if not exist "C:\NMS" mkdir "C:\NMS"
xcopy /E /I /Y dist\NMSAdminServer "%INSTALL_DIR%"
if errorlevel 1 (
    echo     ERROR: xcopy failed. Make sure you are running as Administrator.
    pause & exit /b 1
)

echo [2/5] Creating config and log directories...
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [3/5] Setting up .env config file...
if not exist "%INSTALL_DIR%\.env" (
    copy deploy\config.templates\nms-server.env.template "%INSTALL_DIR%\.env"
    echo.
    echo     IMPORTANT: Edit %INSTALL_DIR%\.env before continuing.
    echo     You must set: SECRET_KEY, FERNET_KEY, TRACKING_API_KEY
    echo     DATABASE_URL is pre-filled for localhost:5433 (native PostgreSQL 16 + TimescaleDB).
    echo.
    echo     Press any key after editing .env, or Ctrl+C to abort.
    pause
) else (
    echo     .env already exists — skipping template copy.
)

echo [4/5] Verifying server starts (quick test)...
echo     (Starting server briefly to check DB connection and .env — Ctrl+C after 5 seconds)
"%INSTALL_DIR%\NMSAdminServer.exe"
echo.
echo     If you saw "[DB] Database connection OK" above, the server is ready.
echo     Press any key to continue with NSSM registration...
pause

echo [5/5] Registering NMSAdminServer with NSSM...
"%NSSM%" install NMSAdminServer "%INSTALL_DIR%\NMSAdminServer.exe"
"%NSSM%" set NMSAdminServer AppDirectory "%INSTALL_DIR%"
"%NSSM%" set NMSAdminServer DisplayName "NMS Admin Server"
"%NSSM%" set NMSAdminServer Description "NMS Flask Dashboard — Waitress, port 5001, 172.16.2.103"
"%NSSM%" set NMSAdminServer Start SERVICE_AUTO_START
"%NSSM%" set NMSAdminServer AppStdout "%LOG_DIR%\stdout.log"
"%NSSM%" set NMSAdminServer AppStderr "%LOG_DIR%\stderr.log"
"%NSSM%" set NMSAdminServer AppRotateFiles 1
"%NSSM%" set NMSAdminServer AppRotateBytes 10485760
"%NSSM%" set NMSAdminServer AppExit Default Restart
"%NSSM%" set NMSAdminServer AppRestartDelay 15000
"%NSSM%" start NMSAdminServer

echo.
echo Done. NMSAdminServer service is running.
echo   Dashboard: http://172.16.2.103:5001
echo   Logs:      %LOG_DIR%\stdout.log
