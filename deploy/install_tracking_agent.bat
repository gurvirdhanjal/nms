@echo off
:: Install NMS Tracking Agent as a Windows service via NSSM
:: Requires: admin privileges, NSSM at deploy\tools\nssm.exe
:: Edit config.json with the correct server URL before running this script.

set INSTALL_DIR=C:\Program Files\NMS\NMSTrackingAgent
set CONFIG_DIR=C:\ProgramData\nms-tracking-agent
set NSSM=%~dp0tools\nssm.exe

echo [1/4] Copying files...
xcopy /E /I /Y dist\NMSTrackingAgent "%INSTALL_DIR%"
if errorlevel 1 (
    echo     ERROR: Could not copy files to %INSTALL_DIR%. Run as administrator.
    pause & exit /b 1
)

echo [2/4] Creating config dir (if not exists)...
if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%"
if not exist "%CONFIG_DIR%\config.json" (
    copy deploy\config.templates\nms-tracking-config.json "%CONFIG_DIR%\config.json"
    echo.
    echo     IMPORTANT: Edit %CONFIG_DIR%\config.json and set server_url before starting!
    echo     Press any key to continue after editing, or Ctrl+C to abort.
    pause
)

echo [3/4] Run test mode to verify connectivity...
"%INSTALL_DIR%\NMSTrackingAgent.exe" --test
if errorlevel 1 (
    echo.
    echo     TEST FAILED. Fix %CONFIG_DIR%\config.json before proceeding.
    pause & exit /b 1
)

echo [4/4] Registering service with NSSM...
"%NSSM%" install NMSTrackingAgent "%INSTALL_DIR%\NMSTrackingAgent.exe"
"%NSSM%" set NMSTrackingAgent AppDirectory "%INSTALL_DIR%"
"%NSSM%" set NMSTrackingAgent DisplayName "NMS Workstation Tracking Agent"
"%NSSM%" set NMSTrackingAgent Description "Sends workstation telemetry to the NMS Dashboard"
"%NSSM%" set NMSTrackingAgent Start SERVICE_AUTO_START
"%NSSM%" set NMSTrackingAgent AppStdout "%CONFIG_DIR%\stdout.log"
"%NSSM%" set NMSTrackingAgent AppStderr "%CONFIG_DIR%\stderr.log"
"%NSSM%" set NMSTrackingAgent AppRotateFiles 1
"%NSSM%" set NMSTrackingAgent AppRotateBytes 5242880
"%NSSM%" set NMSTrackingAgent AppExit Default Restart
"%NSSM%" set NMSTrackingAgent AppRestartDelay 5000
"%NSSM%" start NMSTrackingAgent

echo.
echo Done. Service NMSTrackingAgent is running.
echo NOTE: The tracking agent uses keyboard/screen capture which may trigger AV alerts.
echo       If blocked, run deploy\optional_defender_exclusion.bat as admin.
