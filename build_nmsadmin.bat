@echo off
setlocal enabledelayedexpansion

:: ============================================================
:: build_nmsadmin.bat
:: Builds the NMS Admin Panel EXE and stages it into NMSadmin\
::
:: Usage:  build_nmsadmin.bat
:: Output: NMSadmin\   (ready-to-deploy folder)
:: ============================================================

set PYTHON=python
set PYINSTALLER=pyinstaller
set SPEC=nmsadmin.spec
set DIST_SRC=dist\NMSadmin
set DEPLOY_DIR=NMSadmin

echo.
echo ============================================================
echo  NMS Admin Panel -- Build Script
echo ============================================================
echo.

:: -- 1. Confirm Python is available
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python not found. Add Python to PATH and retry.
    exit /b 1
)

:: -- 2. Install / upgrade PyInstaller if needed
echo [1/4] Checking PyInstaller...
%PYTHON% -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo       Installing PyInstaller...
    %PYTHON% -m pip install pyinstaller --quiet
)
echo       OK

:: -- 3. Run PyInstaller
echo [2/4] Building EXE  (this takes 3-5 minutes the first time)...
%PYINSTALLER% --clean %SPEC%
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed. See output above.
    exit /b 1
)
echo       Build complete.

:: -- 4. Copy dist output into NMSadmin\ deploy folder
echo [3/4] Staging deploy folder...

if not exist "%DEPLOY_DIR%" mkdir "%DEPLOY_DIR%"

:: Copy everything PyInstaller produced
xcopy /E /Y /I "%DIST_SRC%\*" "%DEPLOY_DIR%\" >nul
if errorlevel 1 (
    echo [ERROR] Could not copy dist output to %DEPLOY_DIR%
    exit /b 1
)

:: Copy .env.example if not already there
if not exist "%DEPLOY_DIR%\.env" (
    if exist "%DEPLOY_DIR%\.env.example" (
        echo       .env not found -- copying .env.example as starting point
        copy "%DEPLOY_DIR%\.env.example" "%DEPLOY_DIR%\.env" >nul
        echo       EDIT %DEPLOY_DIR%\.env before running the server!
    )
)

echo       Staged to %DEPLOY_DIR%\

:: -- 5. Summary
echo [4/4] Done.
echo.
echo  Deploy folder : %DEPLOY_DIR%\
echo  Main EXE      : %DEPLOY_DIR%\NMSAdmin.exe
echo  Config        : %DEPLOY_DIR%\.env   (edit before first run)
echo.
echo  To start the server:
echo    cd %DEPLOY_DIR%
echo    start.bat
echo.
echo  To install as a Windows service (NSSM):
echo    cd %DEPLOY_DIR%
echo    install_service.bat
echo.
echo ============================================================
endlocal
