# NMS Admin Server — Windows Server 2019 (No Docker)

> **Use this guide if you want to avoid Docker entirely.**
> TimescaleDB installs natively on Windows as a PostgreSQL extension.
> PostgreSQL registers as a Windows service and starts at boot automatically —
> no Docker, no WSL2, no Task Scheduler workarounds needed.

---

## How this compares to the Docker approach

| | Docker approach | This guide (native) |
|--|----------------|---------------------|
| TimescaleDB runs as | Docker container | Windows service (`postgresql-x64-16`) |
| Starts at boot | Task Scheduler + WSL2 | Automatic (Windows service) |
| Port | 5433 (Docker mapped) | 5433 (set during install) |
| Data stored in | Docker named volume | `C:\PostgreSQL\16\data\` |
| Backup tool | `pg_dump` inside container | `pg_dump` directly from Windows |
| Complexity | High (WSL2 setup) | Low (two installers, done) |

---

## Contents

1. [Step 1 — Install PostgreSQL 16](#1-step-1--install-postgresql-16)
2. [Step 2 — Install TimescaleDB extension](#2-step-2--install-timescaledb-extension)
3. [Step 3 — Create the database and user](#3-step-3--create-the-database-and-user)
4. [Step 4 — Verify TimescaleDB is working](#4-step-4--verify-timescaledb-is-working)
5. [Step 5 — Install Python and dependencies](#5-step-5--install-python-and-dependencies)
6. [Step 6 — Create .env and generate keys](#6-step-6--create-env-and-generate-keys)
7. [Step 7 — First boot smoke-test](#7-step-7--first-boot-smoke-test)
8. [Step 8 — Register NMS server as a Windows service (NSSM)](#8-step-8--register-nms-server-as-a-windows-service-nssm)
9. [Step 9 — Open firewall port 5001](#9-step-9--open-firewall-port-5001)
10. [Verifying the full stack](#10-verifying-the-full-stack)
11. [Managing services](#11-managing-services)
12. [Database backup and restore](#12-database-backup-and-restore)
13. [Updating the server](#13-updating-the-server)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Step 1 — Install PostgreSQL 16

### 1.1 Download the installer

Go to **https://www.enterprisedb.com/downloads/postgres-postgresql-downloads**

Download: **PostgreSQL 16** → **Windows x86-64**

The file is named something like `postgresql-16.x-windows-x64.exe`.

### 1.2 Run the installer

Launch the installer as Administrator. Use these settings on each screen:

| Screen | Setting |
|--------|---------|
| Installation Directory | `C:\PostgreSQL\16` |
| Select Components | PostgreSQL Server ✓, Command Line Tools ✓ (uncheck pgAdmin and Stack Builder) |
| Data Directory | `C:\PostgreSQL\16\data` |
| Password | Set a strong password for the `postgres` superuser — **write it down** |
| Port | **5433** ← change from the default 5432 |
| Locale | Default |

> **Why port 5433?**
> The NMS `DATABASE_URL` is pre-configured for `localhost:5433` to match the old Docker
> mapping. Keeping the same port means you do not need to edit any `.env` files.

Click through and finish. The installer creates and starts the `postgresql-x64-16`
Windows service automatically.

### 1.3 Add psql to your PATH

Open **PowerShell as Administrator**:

```powershell
[System.Environment]::SetEnvironmentVariable(
    "Path",
    $env:Path + ";C:\PostgreSQL\16\bin",
    [System.EnvironmentVariableTarget]::Machine
)
```

Close and reopen PowerShell, then verify:

```powershell
psql --version
# Expected: psql (PostgreSQL) 16.x
```

---

## 2. Step 2 — Install TimescaleDB extension

TimescaleDB provides a Windows installer that copies the required files into your
PostgreSQL installation and edits `postgresql.conf` for you.

### 2.1 Download TimescaleDB for Windows

Go to **https://github.com/timescale/timescaledb/releases**

Find the latest `2.x` release. Under **Assets**, download:

```
timescaledb-postgresql-16-windows-amd64.zip
```

> Make sure the filename contains `postgresql-16` to match your PostgreSQL version.

### 2.2 Run the TimescaleDB installer

Extract the zip. Inside you will find `setup.exe` (or `timescaledb.exe`).
Run it as **Administrator**:

```
setup.exe
```

The installer will:
1. Detect your PostgreSQL 16 installation at `C:\PostgreSQL\16`
2. Copy `timescaledb-2.x.x.dll` and related files to `C:\PostgreSQL\16\lib\`
3. Copy extension control/SQL files to `C:\PostgreSQL\16\share\extension\`
4. Add `timescaledb` to `shared_preload_libraries` in `postgresql.conf`

When asked if you want to tune memory settings, choose **Yes** — it sets
`shared_buffers` and `work_mem` appropriate for your server RAM.

### 2.3 Restart PostgreSQL to load the extension

```powershell
Restart-Service postgresql-x64-16
```

Verify PostgreSQL started back up:

```powershell
Get-Service postgresql-x64-16
# Status: Running
```

---

## 3. Step 3 — Create the database and user

Connect to PostgreSQL using the `postgres` superuser:

```powershell
psql -U postgres -p 5433 -h localhost
```

Enter the password you set during install. You are now in the psql shell.
Run these commands one by one:

```sql
-- Create the application user
CREATE USER monitoring_man WITH PASSWORD 'admin123';

-- Create the application database owned by that user
CREATE DATABASE monitoring_db OWNER monitoring_man;

-- Connect to the new database
\c monitoring_db

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Grant full privileges to the application user
GRANT ALL PRIVILEGES ON DATABASE monitoring_db TO monitoring_man;
GRANT ALL ON SCHEMA public TO monitoring_man;

-- Exit psql
\q
```

> **Change the password** if this server is on a network reachable by others.
> Update `DATABASE_URL` in `.env` accordingly.

---

## 4. Step 4 — Verify TimescaleDB is working

Connect as the application user and confirm the extension is active:

```powershell
psql -U monitoring_man -p 5433 -h localhost -d monitoring_db
```

```sql
SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';
```

Expected output:

```
 extversion
------------
 2.x.x
(1 row)
```

Exit psql:

```sql
\q
```

### Verify port 5433 is listening

```powershell
netstat -ano | findstr :5433
# Should show LISTENING on 0.0.0.0:5433
```

---

## 5. Step 5 — Install Python and dependencies

```powershell
# Download Python 3.10.11 (64-bit)
Invoke-WebRequest `
  -Uri "https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe" `
  -OutFile "$env:TEMP\python310.exe"

# Install for all users, add to PATH
Start-Process "$env:TEMP\python310.exe" `
  -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Shortcuts=0" `
  -Wait
```

Close and reopen PowerShell:

```powershell
python --version
# Python 3.10.11
```

Install project dependencies:

```powershell
cd D:\device_monitoring_tactical
pip install -r requirements.txt
```

Verify the PostgreSQL driver:

```powershell
python -c "import psycopg2; print('psycopg2 OK', psycopg2.__version__)"
```

---

## 6. Step 6 — Create .env and generate keys

```powershell
copy "D:\device_monitoring_tactical\deploy\config.templates\nms-server.env.template" `
     "D:\device_monitoring_tactical\.env"

notepad D:\device_monitoring_tactical\.env
```

The `DATABASE_URL` line is already correct for a native install on port 5433:

```env
DATABASE_URL=postgresql+psycopg2://monitoring_man:admin123@localhost:5433/monitoring_db
```

Generate and fill in the three required secrets. Run each in PowerShell and paste the
output into the matching line in `.env`:

```powershell
# SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"

# FERNET_KEY  (encrypts SNMP community strings — never change once data is in DB)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# TRACKING_API_KEY  (agents use this — put the same value in agents' config.json)
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Your `.env` should look like this when done:

```env
APP_ENV=production
DATABASE_URL=postgresql+psycopg2://monitoring_man:admin123@localhost:5433/monitoring_db
SECRET_KEY=a3f8c2d1e4b7...64 hex chars...
FERNET_KEY=abc123...base64...==
TRACKING_API_KEY=random-urlsafe-token
SESSION_COOKIE_SECURE=False
SESSION_TIMEOUT_MINUTES=30
REQUIRE_POSTGRES=true
PORT=5001
WEB_HOST=0.0.0.0
REDIS_URL=redis://localhost:6379/0
SNMP_COMMUNITY=public
SNMP_VERSION=2c
```

Save and close Notepad.

---

## 7. Step 7 — First boot smoke-test

Run the server manually to confirm the database connection and schema migration work:

```powershell
cd D:\device_monitoring_tactical
python nms_server_main.py
```

Expected output:

```
[DB] Backend=postgresql Host=localhost DB=monitoring_db
[DB] Database connection OK
[OK] Discovery Service primed: ...
[OK] Default admin user created.
[OK] Monitor collector hydrated.
[OK] Monitoring scheduler started.
[OK] Interface poller started.
[NMS] Admin server running on http://0.0.0.0:5001
[NMS] Accessible at: http://172.16.2.103:5001
```

Open a browser and go to **http://172.16.2.103:5001**. You should see the NMS login page.
Log in with `admin` / `admin123`.

Verify tables were created:

```powershell
psql -U monitoring_man -p 5433 -h localhost -d monitoring_db -c "\dt" | measure -line
# Should show 30+ tables
```

Press `Ctrl+C` to stop the test run.

---

## 8. Step 8 — Register NMS server as a Windows service (NSSM)

Place `nssm.exe` (64-bit) at `D:\device_monitoring_tactical\deploy\tools\nssm.exe`.
Download from **https://nssm.cc/download** → win64 build.

Open **PowerShell as Administrator**:

```powershell
$PROJECT = "D:\device_monitoring_tactical"
$PYTHON  = (Get-Command python).Source
$NSSM    = "$PROJECT\deploy\tools\nssm.exe"
$LOGDIR  = "C:\ProgramData\nms-server"

New-Item -ItemType Directory -Force -Path $LOGDIR | Out-Null

# Register
& $NSSM install NMSAdminServer $PYTHON "nms_server_main.py"
& $NSSM set NMSAdminServer AppDirectory $PROJECT
& $NSSM set NMSAdminServer DisplayName "NMS Admin Server"
& $NSSM set NMSAdminServer Description "NMS Flask — Waitress port 5001 @ 172.16.2.103"
& $NSSM set NMSAdminServer Start SERVICE_AUTO_START

# Logging
& $NSSM set NMSAdminServer AppStdout "$LOGDIR\stdout.log"
& $NSSM set NMSAdminServer AppStderr "$LOGDIR\stderr.log"
& $NSSM set NMSAdminServer AppRotateFiles 1
& $NSSM set NMSAdminServer AppRotateBytes 10485760

# Restart policy — 5s delay is enough since PostgreSQL is a native service
# and will already be running when Windows boots
& $NSSM set NMSAdminServer AppExit Default Restart
& $NSSM set NMSAdminServer AppRestartDelay 5000

# Start
& $NSSM start NMSAdminServer
```

> **Boot order is automatic.**
> `postgresql-x64-16` starts as a Windows service before NSSM services.
> NSSM's 5-second restart delay is a safety margin — not required like it was with Docker.

Verify the service is running:

```powershell
& $NSSM status NMSAdminServer
# SERVICE_RUNNING
```

---

## 9. Step 9 — Open firewall port 5001

```powershell
New-NetFirewallRule `
  -DisplayName "NMS Admin Server (port 5001)" `
  -Direction    Inbound `
  -Protocol     TCP `
  -LocalPort    5001 `
  -Action       Allow `
  -Profile      Domain,Private
```

Verify:

```powershell
Get-NetFirewallRule -DisplayName "NMS Admin Server*" |
  Select-Object DisplayName, Enabled, Direction
```

---

## 10. Verifying the full stack

### PostgreSQL service is running

```powershell
Get-Service postgresql-x64-16
# Status: Running
```

### Port 5433 is listening

```powershell
netstat -ano | findstr :5433
# TCP  0.0.0.0:5433  0.0.0.0:0  LISTENING
```

### NMS server service is running

```powershell
& "D:\device_monitoring_tactical\deploy\tools\nssm.exe" status NMSAdminServer
# SERVICE_RUNNING
```

### HTTP response

```powershell
Invoke-WebRequest -Uri "http://localhost:5001/health" -UseBasicParsing
# StatusCode: 200
```

From a LAN browser: **http://172.16.2.103:5001**

### Startup log is clean

```powershell
Get-Content "C:\ProgramData\nms-server\stdout.log" -Tail 20
```

Should contain `[DB] Database connection OK` and `[NMS] Accessible at: http://172.16.2.103:5001`
with no Python tracebacks.

---

## 11. Managing services

### NMS Admin Server

```powershell
$NSSM = "D:\device_monitoring_tactical\deploy\tools\nssm.exe"

& $NSSM start   NMSAdminServer
& $NSSM stop    NMSAdminServer
& $NSSM restart NMSAdminServer
& $NSSM status  NMSAdminServer
& $NSSM edit    NMSAdminServer    # opens GUI
```

### PostgreSQL / TimescaleDB

```powershell
Start-Service   postgresql-x64-16
Stop-Service    postgresql-x64-16
Restart-Service postgresql-x64-16
Get-Service     postgresql-x64-16
```

Or via `services.msc` — look for **postgresql-x64-16**.

> **Always stop NMS before stopping PostgreSQL:**
>
> ```powershell
> & $NSSM stop NMSAdminServer
> Stop-Service postgresql-x64-16
> ```

### Connect to the database

```powershell
psql -U monitoring_man -p 5433 -h localhost -d monitoring_db
```

Useful psql commands:

```sql
\dt                          -- list all tables
SELECT count(*) FROM devices;
SELECT * FROM v_timescaledb_job_health;
\q
```

---

## 12. Database backup and restore

No Docker needed — `pg_dump` and `pg_restore` are installed with PostgreSQL at
`C:\PostgreSQL\16\bin\`.

### Backup

```powershell
$date = Get-Date -Format "yyyyMMdd"
New-Item -ItemType Directory -Force -Path D:\nms_backups | Out-Null

pg_dump -U monitoring_man -p 5433 -h localhost -d monitoring_db -Fc `
  -f "D:\nms_backups\nms_backup_$date.dump"
```

You will be prompted for the password. To avoid the prompt, create a pgpass file:

```powershell
# Create pgpass file (no password prompt)
$pgpassDir = "$env:APPDATA\postgresql"
New-Item -ItemType Directory -Force -Path $pgpassDir | Out-Null
"localhost:5433:monitoring_db:monitoring_man:admin123" |
  Out-File "$pgpassDir\pgpass.conf" -Encoding ASCII
```

Schedule daily backups via Task Scheduler pointing to a `.ps1` file containing the
`pg_dump` command above.

### Restore

```powershell
$NSSM = "D:\device_monitoring_tactical\deploy\tools\nssm.exe"
& $NSSM stop NMSAdminServer

# Drop and recreate the database
psql -U postgres -p 5433 -h localhost -c "DROP DATABASE monitoring_db;"
psql -U postgres -p 5433 -h localhost -c "CREATE DATABASE monitoring_db OWNER monitoring_man;"

# Restore from dump
pg_restore -U monitoring_man -p 5433 -h localhost -d monitoring_db `
  "D:\nms_backups\nms_backup_YYYYMMDD.dump"

& $NSSM start NMSAdminServer
```

---

## 13. Updating the server

```powershell
$NSSM = "D:\device_monitoring_tactical\deploy\tools\nssm.exe"
& $NSSM stop NMSAdminServer

cd D:\device_monitoring_tactical
git pull
pip install -r requirements.txt

# Schema migrations run automatically on next startup
& $NSSM start NMSAdminServer
```

---

## 14. Troubleshooting

### `psql: error: connection to server at "localhost", port 5433 failed`

```powershell
# Check if PostgreSQL is running
Get-Service postgresql-x64-16

# Check if it's listening on 5433
netstat -ano | findstr :5433

# Start it if stopped
Start-Service postgresql-x64-16
```

If it won't start, check the PostgreSQL log at `C:\PostgreSQL\16\data\log\`.

### `CREATE EXTENSION timescaledb` fails — "could not open extension control file"

The TimescaleDB installer did not find your PostgreSQL installation. Reinstall the
TimescaleDB package and when prompted for the PostgreSQL path, enter `C:\PostgreSQL\16`.

### TimescaleDB extension installed but PostgreSQL fails to start after restart

`shared_preload_libraries` was set correctly but the DLL cannot load.
Check the log at `C:\PostgreSQL\16\data\log\` for:

```
FATAL: could not load library "timescaledb": The specified module could not be found.
```

Fix: re-run the TimescaleDB `setup.exe` to reinstall the DLL files.

### `FERNET_KEY` error on startup

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the output into `.env` as `FERNET_KEY=...`, then restart the service.

### NMSAdminServer service stops after a few seconds

```powershell
Get-Content "C:\ProgramData\nms-server\stderr.log" -Tail 30
```

| Error | Fix |
|-------|-----|
| `could not connect to server` port 5433 | `Start-Service postgresql-x64-16` |
| `password authentication failed` | Check `monitoring_man` password in `.env` matches the one set in Step 3 |
| `FERNET_KEY environment variable not set` | Add `FERNET_KEY=...` to `.env` |
| `No module named 'waitress'` | `pip install -r requirements.txt` |

### How to change the PostgreSQL port after install

Edit `C:\PostgreSQL\16\data\postgresql.conf`:

```
port = 5433
```

Restart the service:

```powershell
Restart-Service postgresql-x64-16
```

Then update `DATABASE_URL` in `.env` to match.

---

## Quick reference card

```
PG START       Start-Service postgresql-x64-16
PG STOP        Stop-Service  postgresql-x64-16
PG STATUS      Get-Service   postgresql-x64-16
PG CONNECT     psql -U monitoring_man -p 5433 -h localhost -d monitoring_db

APP START      nssm start   NMSAdminServer
APP STOP       nssm stop    NMSAdminServer
APP RESTART    nssm restart NMSAdminServer
APP LOGS       Get-Content C:\ProgramData\nms-server\stdout.log -Tail 50 -Wait

BACKUP         pg_dump -U monitoring_man -p 5433 -h localhost -d monitoring_db -Fc -f D:\nms_backups\backup.dump

ENV FILE       D:\device_monitoring_tactical\.env
DB DATA DIR    C:\PostgreSQL\16\data\
DB LOGS        C:\PostgreSQL\16\data\log\

FIREWALL       New-NetFirewallRule -DisplayName "NMS port 5001" -Direction Inbound -Protocol TCP -LocalPort 5001 -Action Allow -Profile Domain,Private

DASHBOARD      http://172.16.2.103:5001
```
