@echo off
:: ============================================================
:: NMS Admin Server — Docker Deploy
:: Builds the image and starts postgres + app containers.
:: Run from the project root (folder that contains docker-compose.yml).
::
:: Usage:  deploy\docker_deploy.bat
:: ============================================================

setlocal
cd /d "%~dp0.."

echo.
echo ========================================
echo  NMS Docker Deploy
echo ========================================
echo.

:: ── Check Docker ──────────────────────────────────────────────────────────
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker is not running or not installed.
    echo         Start Docker Desktop and try again.
    pause & exit /b 1
)

:: ── Ensure .env exists ────────────────────────────────────────────────────
if not exist ".env" (
    echo [!] .env not found. Generating default .env...

    :: Generate a pseudo-random secret key from timestamp + random numbers
    set "RKEY=%RANDOM%%RANDOM%%RANDOM%%RANDOM%"
    set "RTOKEN=%RANDOM%%RANDOM%%RANDOM%%RANDOM%"

    (
        echo APP_ENV=production
        echo DATABASE_URL=postgresql+psycopg2://monitoring_man:admin123@db:5432/monitoring_db
        echo SECRET_KEY=nms-%RKEY%-changeme
        echo TRACKING_API_KEY=%RTOKEN%
        echo WEB_HOST=0.0.0.0
        echo PORT=5001
        echo WEB_OPEN_BROWSER=0
        echo SESSION_COOKIE_SECURE=False
        echo SESSION_TIMEOUT_MINUTES=30
        echo REQUIRE_POSTGRES=true
        echo REDIS_URL=redis://localhost:6379/0
        echo REDIS_SSE_ENABLED=false
        echo GEMINI_REPORT_INSIGHTS_ENABLED=false
        echo SMTP_SERVER=smtp.gmail.com
        echo SMTP_PORT=587
        echo SMTP_USERNAME=
        echo SMTP_PASSWORD=
        echo SNMP_COMMUNITY=public
        echo SNMP_VERSION=2c
        echo MONITORING_INTERVAL=300
    ) > .env

    echo     .env created at %CD%\.env
    echo.
    echo     IMPORTANT: Note your TRACKING_API_KEY for agent installs:
    echo       TRACKING_API_KEY=%RTOKEN%
    echo.
    echo     Opening .env in Notepad — edit SECRET_KEY and save when done.
    echo     Press any key to open...
    pause >nul
    notepad .env
    echo     Press any key to continue with deploy...
    pause >nul
)

:: ── Build + start ─────────────────────────────────────────────────────────
echo Building Docker image...
docker compose build --no-cache
if errorlevel 1 ( echo [ERROR] Docker build failed. & pause & exit /b 1 )

echo.
echo Starting containers (db + app)...
docker compose up -d
if errorlevel 1 ( echo [ERROR] docker compose up failed. & pause & exit /b 1 )

:: ── Wait for app to be ready ──────────────────────────────────────────────
echo.
echo Waiting for app to be ready...
set "READY=0"
for /L %%i in (1,1,30) do (
    if "!READY!"=="0" (
        curl -s -o nul -w "%%{http_code}" http://localhost:5001/health 2>nul | findstr "200" >nul 2>&1
        if not errorlevel 1 (
            set "READY=1"
            echo   Ready after %%i seconds.
        ) else (
            timeout /t 1 /nobreak >nul
        )
    )
)
if "!READY!"=="0" (
    echo   [WARN] App did not respond on /health after 30s.
    echo          Check logs: docker compose logs app
)

echo.
echo ========================================
echo  Deploy complete!
echo  Dashboard: http://localhost:5001
echo  Default login: admin / admin123
echo.
echo  Useful commands:
echo    docker compose logs -f app     (live logs)
echo    docker compose logs -f db      (postgres logs)
echo    docker compose restart app     (restart app only)
echo    docker compose down            (stop everything)
echo    docker compose down -v         (stop + wipe DB volume)
echo ========================================
pause
