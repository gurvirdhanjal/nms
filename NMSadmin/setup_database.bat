@echo off
setlocal EnableDelayedExpansion

:: ============================================================
:: setup_database.bat — Native PostgreSQL 16 + TimescaleDB setup
:: Run this ONCE before starting NMSAdmin.exe
:: No Docker required.
:: ============================================================

set PG_VERSION=16
set PG_PORT=5433
set DB_USER=monitoring_man
set DB_PASS=admin123
set DB_NAME=monitoring_db

echo.
echo ============================================================
echo  NMS Admin Panel — Database Setup (No Docker)
echo ============================================================
echo.

:: ---- Check if pg_isready exists (PostgreSQL already installed) ----
where pg_isready >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] PostgreSQL binaries found in PATH.
    goto :check_service
)

:: Try common PostgreSQL install paths
set PG_BIN=
for %%D in (
    "C:\Program Files\PostgreSQL\16\bin"
    "C:\Program Files\PostgreSQL\15\bin"
    "C:\Program Files\TimescaleDB\postgresql-16\bin"
) do (
    if exist "%%~D\pg_isready.exe" (
        set PG_BIN=%%~D
        goto :found_pg
    )
)

echo [!] PostgreSQL not found. Installing via winget...
echo.

:: ---- Try winget first ----
where winget >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [INFO] Using winget to install PostgreSQL 16...
    winget install --id PostgreSQL.PostgreSQL.16 --silent --accept-package-agreements --accept-source-agreements
    if !ERRORLEVEL! EQU 0 (
        echo [OK] PostgreSQL 16 installed.
        set PG_BIN=C:\Program Files\PostgreSQL\16\bin
        goto :install_timescale
    )
)

:: ---- Manual download fallback ----
echo.
echo [!] winget install failed or not available.
echo.
echo  MANUAL STEPS:
echo  1. Download PostgreSQL 16 Windows installer from:
echo     https://www.enterprisedb.com/downloads/postgres-postgresql-downloads
echo     (pick version 16.x, Windows x86-64)
echo.
echo  2. Download TimescaleDB 2.x Windows installer from:
echo     https://docs.timescale.com/self-hosted/latest/install/installation-windows/
echo     (use "TimescaleDB for PostgreSQL 16")
echo.
echo  3. Install PostgreSQL first, then TimescaleDB.
echo.
echo  4. After both are installed, re-run this script.
echo.
pause
exit /b 1

:found_pg
echo [OK] Found PostgreSQL at: %PG_BIN%
set "PATH=%PG_BIN%;%PATH%"

:install_timescale
echo.
echo [INFO] Checking for TimescaleDB extension...

:: Check if timescaledb extension is already available
psql -U postgres -p 5432 -c "SELECT 1 FROM pg_available_extensions WHERE name='timescaledb';" 2>nul | findstr /C:"(1 row)" >nul
if %ERRORLEVEL% EQU 0 (
    echo [OK] TimescaleDB extension is available.
    goto :check_service
)

echo [!] TimescaleDB extension not found.
echo.
echo  To install TimescaleDB on Windows:
echo  1. Download from: https://docs.timescale.com/self-hosted/latest/install/installation-windows/
echo  2. Run the installer — it will auto-detect your PostgreSQL 16 installation.
echo  3. Re-run this script after installation.
echo.
echo  NOTE: The NMS app works without TimescaleDB but time-series reports
echo        will be slower. To skip and use plain PostgreSQL, press Y.
echo.
set /p SKIP_TS="Skip TimescaleDB and use plain PostgreSQL? [Y/N]: "
if /I "!SKIP_TS!"=="Y" (
    echo [INFO] Proceeding with plain PostgreSQL (no TimescaleDB).
    set PG_PORT=5432
    echo [INFO] Using standard port 5432.
    echo [INFO] Update DATABASE_URL in .env to use port 5432 instead of 5433.
    goto :check_service
) else (
    pause
    exit /b 1
)

:check_service
echo.
echo [INFO] Checking PostgreSQL service...

:: Try to find and start the service
for %%S in ("postgresql-x64-16" "postgresql-x64-15" "postgresql") do (
    sc query %%~S >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        sc start %%~S >nul 2>&1
        echo [OK] PostgreSQL service %%~S started.
        goto :wait_ready
    )
)

echo [WARN] Could not find a PostgreSQL service by standard names.
echo        Trying to connect anyway...

:wait_ready
echo.
echo [INFO] Waiting for PostgreSQL to be ready...
set RETRIES=10
:wait_loop
pg_isready -h 127.0.0.1 -p 5432 -U postgres >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :setup_db
set /a RETRIES-=1
if %RETRIES% LEQ 0 (
    echo [ERROR] PostgreSQL did not become ready. Check the service is running.
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul
goto :wait_loop

:setup_db
echo [OK] PostgreSQL is ready.
echo.
echo [INFO] Creating database user and database...

:: Create user (ignore error if already exists)
psql -h 127.0.0.1 -p 5432 -U postgres -c "CREATE USER %DB_USER% WITH PASSWORD '%DB_PASS%';" 2>nul
psql -h 127.0.0.1 -p 5432 -U postgres -c "ALTER USER %DB_USER% CREATEDB;" 2>nul

:: Create database
psql -h 127.0.0.1 -p 5432 -U postgres -c "CREATE DATABASE %DB_NAME% OWNER %DB_USER%;" 2>nul

:: Enable TimescaleDB extension (silently fails if not installed)
psql -h 127.0.0.1 -p 5432 -U postgres -d %DB_NAME% -c "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;" 2>nul
if %ERRORLEVEL% EQU 0 (
    echo [OK] TimescaleDB extension enabled.
) else (
    echo [WARN] TimescaleDB extension not available — running in plain PostgreSQL mode.
)

echo.
echo ============================================================
echo  Database setup complete!
echo ============================================================
echo.
echo  Connection string for .env:
echo  DATABASE_URL=postgresql+psycopg2://%DB_USER%:%DB_PASS%@localhost:5432/%DB_NAME%
echo.
echo  NOTE: The above uses port 5432 (native PostgreSQL).
echo        Update the DATABASE_URL line in your .env file.
echo.
echo  The NMS app will create all tables automatically on first start.
echo ============================================================
echo.
pause
endlocal
