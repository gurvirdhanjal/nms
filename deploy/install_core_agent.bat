@echo off
:: Install NMS Core Agent as a Windows service via NSSM
:: Requires: admin privileges, NSSM at deploy\tools\nssm.exe
:: Edit config.json with the correct server URL before running this script.

set INSTALL_DIR=C:\Program Files\NMS\NMSCoreAgent
set CONFIG_DIR=C:\ProgramData\nms-agent
set NSSM=%~dp0tools\nssm.exe

echo [1/4] Copying files...
xcopy /E /I /Y dist\NMSCoreAgent "%INSTALL_DIR%"
if errorlevel 1 (
    echo     ERROR: Could not copy files to %INSTALL_DIR%. Run as administrator.
    pause & exit /b 1
)

echo [2/4] Creating config dir (if not exists)...
if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%"
if not exist "%CONFIG_DIR%\config.json" (
    copy deploy\config.templates\nms-agent-config.json "%CONFIG_DIR%\config.json"
    echo.
    echo     IMPORTANT: Edit %CONFIG_DIR%\config.json and set nms_server_url before starting!
    echo     Press any key to continue after editing, or Ctrl+C to abort.
    pause
)

echo [3/4] Run test mode to verify connectivity...
"%INSTALL_DIR%\NMSCoreAgent.exe" --test
if errorlevel 1 (
    echo.
    echo     TEST FAILED. Fix %CONFIG_DIR%\config.json before proceeding.
    pause & exit /b 1
)

echo [4/4] Registering service with NSSM...
"%NSSM%" install NMSCoreAgent "%INSTALL_DIR%\NMSCoreAgent.exe"
"%NSSM%" set NMSCoreAgent AppDirectory "%INSTALL_DIR%"
"%NSSM%" set NMSCoreAgent DisplayName "NMS Core Monitoring Agent"
"%NSSM%" set NMSCoreAgent Description "Sends server health metrics to the NMS Dashboard"
"%NSSM%" set NMSCoreAgent Start SERVICE_AUTO_START
"%NSSM%" set NMSCoreAgent AppStdout "%CONFIG_DIR%\stdout.log"
"%NSSM%" set NMSCoreAgent AppStderr "%CONFIG_DIR%\stderr.log"
"%NSSM%" set NMSCoreAgent AppRotateFiles 1
"%NSSM%" set NMSCoreAgent AppRotateBytes 5242880
"%NSSM%" set NMSCoreAgent AppExit Default Restart
"%NSSM%" set NMSCoreAgent AppRestartDelay 5000
"%NSSM%" start NMSCoreAgent

echo.
echo Done. Service NMSCoreAgent is running.
echo NOTE: If Windows Defender blocks the agent, run deploy\optional_defender_exclusion.bat as admin.
