@echo off
setlocal

cd /d "%~dp0.."

echo =====================================================
echo  NMS Admin Server - Build
echo =====================================================
echo.

echo [1/2] Cleaning previous build...
if exist dist\NMSAdminServer rmdir /s /q dist\NMSAdminServer
if exist build\NMSAdminServer rmdir /s /q build\NMSAdminServer

echo [2/2] Building exe...
pyinstaller --noconfirm --clean web_main.spec
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed.
    pause
    exit /b %errorlevel%
)

echo.
echo =====================================================
echo  Build complete: dist\NMSAdminServer\NMSAdminServer.exe
echo.
echo  Next step: copy your .env into dist\NMSAdminServer\
echo  Then run NMSAdminServer.exe
echo =====================================================
pause
