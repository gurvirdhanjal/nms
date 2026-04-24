@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%setup_postgres_from_env.ps1" %*
exit /b %ERRORLEVEL%
