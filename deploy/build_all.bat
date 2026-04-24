@echo off
:: ============================================================
:: NMS Build Script — PyInstaller EXEs only
::
:: Prerequisites:
::   - Python + PyInstaller installed in active venv/PATH
::   - Run from the project root (parent of deploy\)
::
:: Usage:  deploy\build_all.bat
:: ============================================================

setlocal

set "ROOT=%~dp0.."
cd /d "%ROOT%"

echo.
echo ========================================
echo  NMS Build Pipeline
echo ========================================
echo.

:: ── Check PyInstaller ──────────────────────────────────────────────────────
where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PyInstaller not found in PATH.
    echo         Activate your virtual environment first:
    echo           venv\Scripts\activate
    pause & exit /b 1
)

:: ── Stop running services + processes (releases file locks on dist\) ────────
echo Stopping any running NMS services and processes...
sc stop NMSAdminServer   >nul 2>&1
sc stop NMSCoreAgent     >nul 2>&1
sc stop NMSTrackingAgent >nul 2>&1
taskkill /F /IM NMSAdminServer.exe   >nul 2>&1
taskkill /F /IM NMSCoreAgent.exe     >nul 2>&1
taskkill /F /IM NMSTrackingAgent.exe >nul 2>&1
:: Give Windows a moment to fully release file handles
timeout /t 2 /nobreak >nul
echo Done.
echo.

:: ── [1/3] NMS Admin Server ─────────────────────────────────────────────────
echo [1/3] Building NMSAdminServer (web_main.py)...
pyinstaller build_admin.spec --noconfirm
if errorlevel 1 ( echo [ERROR] NMSAdminServer build failed. & pause & exit /b 1 )
echo       Done -- dist\NMSAdminServer\NMSAdminServer.exe
echo.

:: ── [2/3] NMS Tracking Agent ───────────────────────────────────────────────
echo [2/3] Building NMSTrackingAgent (service.py)...
pyinstaller build_tracking.spec --noconfirm
if errorlevel 1 ( echo [ERROR] NMSTrackingAgent build failed. & pause & exit /b 1 )
echo       Done -- dist\NMSTrackingAgent\NMSTrackingAgent.exe
echo.

:: ── [3/3] NMS Core Agent ───────────────────────────────────────────────────
echo [3/3] Building NMSCoreAgent (server_agent.py)...
pyinstaller build_agent.spec --noconfirm
if errorlevel 1 ( echo [ERROR] NMSCoreAgent build failed. & pause & exit /b 1 )
echo       Done -- dist\NMSCoreAgent\NMSCoreAgent.exe
echo.

echo ========================================
echo  Build Complete!
echo ========================================
echo  dist\NMSAdminServer\NMSAdminServer.exe
echo  dist\NMSCoreAgent\NMSCoreAgent.exe
echo  dist\NMSTrackingAgent\NMSTrackingAgent.exe
echo ========================================
pause
