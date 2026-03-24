@echo off
setlocal

:: ============================================================
:: NMS Full Build Script
:: Builds: NMSAdminServer + NMSCoreAgent + NMSTrackingAgent
:: Server IP: 172.16.2.103:5001
:: ============================================================

echo === Cleaning previous builds ===
rmdir /s /q dist\NMSAdminServer 2>nul
rmdir /s /q dist\NMSCoreAgent 2>nul
rmdir /s /q dist\NMSTrackingAgent 2>nul

echo.
echo === [1/3] Building NMS Admin Server ===
pyinstaller --clean NMSAdminServer.spec
if errorlevel 1 (
    echo Build FAILED for NMSAdminServer
    exit /b 1
)

echo.
echo === [2/3] Building NMS Core Agent ===
pyinstaller --clean server_agent.spec
if errorlevel 1 (
    echo Build FAILED for NMSCoreAgent
    exit /b 1
)

echo.
echo === [3/3] Building NMS Tracking Agent ===
pyinstaller --clean WorkstationAgent.spec
if errorlevel 1 (
    echo Build FAILED for NMSTrackingAgent
    exit /b 1
)

echo.
echo === Copying config templates ===
copy deploy\config.templates\nms-server.env.template       dist\NMSAdminServer\.env.template
copy deploy\config.templates\nms-agent-config.json         dist\NMSCoreAgent\config.json.template
copy deploy\config.templates\nms-tracking-config.json      dist\NMSTrackingAgent\config.json.template

echo.
echo === Build complete — dist\ is ready ===
echo.
echo   ADMIN SERVER:
echo     dist\NMSAdminServer\NMSAdminServer.exe
echo     Copy dist\NMSAdminServer\.env.template to dist\NMSAdminServer\.env
echo     Fill in: SECRET_KEY, FERNET_KEY, TRACKING_API_KEY
echo     Then run:  deploy\install_admin_server.bat
echo.
echo   CORE AGENT (deploy to monitored servers):
echo     dist\NMSCoreAgent\NMSCoreAgent.exe
echo     Config points to: http://172.16.2.103:5001/api/agent/metrics
echo     Run:  deploy\install_core_agent.bat
echo.
echo   TRACKING AGENT (deploy to workstations):
echo     dist\NMSTrackingAgent\NMSTrackingAgent.exe
echo     Config points to: http://172.16.2.103:5001
echo     Run:  deploy\install_tracking_agent.bat
