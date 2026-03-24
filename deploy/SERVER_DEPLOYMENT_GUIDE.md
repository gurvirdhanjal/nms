# NMS Admin Server — Deployment Guide

Deploy the NMS Flask server on a Windows machine alongside its TimescaleDB dependency
running in Docker. The server uses Waitress (production WSGI), auto-runs schema migrations
on startup, and reads all secrets from a `.env` file.

---

## Contents

1. [Architecture overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Step 1 — Install Docker Desktop](#3-step-1--install-docker-desktop)
4. [Step 2 — Start TimescaleDB](#4-step-2--start-timescaledb)
5. [Step 3 — Install Python and dependencies](#5-step-3--install-python-and-dependencies)
6. [Step 4 — Create the .env file](#6-step-4--create-the-env-file)
7. [Step 5 — Generate secret keys](#7-step-5--generate-secret-keys)
8. [Step 6 — First boot smoke-test](#8-step-6--first-boot-smoke-test)
9. [Step 7 — Register the server as a Windows service (NSSM)](#9-step-7--register-the-server-as-a-windows-service-nssm)
10. [Step 8 — Make TimescaleDB start with Windows](#10-step-8--make-timescaledb-start-with-windows)
11. [Verifying the full stack](#11-verifying-the-full-stack)
12. [Managing the service after install](#12-managing-the-service-after-install)
13. [Environment variable reference](#13-environment-variable-reference)
14. [Database backup and restore](#14-database-backup-and-restore)
15. [Updating the server](#15-updating-the-server)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. Architecture overview

```
 ┌─────────────────────────────────────────────────────┐
 │  Windows Host Machine                               │
 │                                                     │
 │  ┌─────────────────────────┐                        │
 │  │  NMSAdminServer (NSSM)  │  port 5001             │
 │  │  python run_prod.py     │  Waitress / 6 threads  │
 │  │  + background scheduler │                        │
 │  │  + SNMP worker thread   │                        │
 │  └────────────┬────────────┘                        │
 │               │ psycopg2                             │
 │               ▼ localhost:5433                       │
 │  ┌─────────────────────────┐                        │
 │  │  Docker Container       │  port 5433             │
 │  │  monitoring_timescaledb │  (→ pg internal 5432)  │
 │  │  TimescaleDB + pg16     │                        │
 │  └─────────────────────────┘                        │
 │               │                                     │
 │               ▼ named volume                        │
 │  timescaledb_data  (persistent across restarts)     │
 └─────────────────────────────────────────────────────┘
```

**What runs where:**

| Component | How it runs | Port |
|-----------|-------------|------|
| NMS Flask app | Python process via NSSM | 5001 |
| TimescaleDB | Docker container (`monitoring_timescaledb`) | 5433 |
| Redis (optional) | Separate install or Docker | 6379 |
| NMS Core Agent | Separate NSSM service on same or other machines | — |
| NMS Tracking Agent | Separate NSSM service on monitored machines | 5002 |

> **Port 5433, not 5432.** The compose file maps host:5433 → container:5432 to avoid
> conflicting with any existing PostgreSQL installed directly on Windows.

---

## 2. Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Windows 10/11 or Server 2019+ | Any | 64-bit required |
| Docker Desktop for Windows | 4.x+ | With WSL2 backend recommended |
| Python | 3.10.x | Must match the version used to develop (`python --version`) |
| pip | 23+ | Comes with Python |
| NSSM | 2.24 64-bit | For service registration — same binary as agents |
| Git (optional) | Any | Only needed if pulling updates from a repo |
| 4 GB RAM free | — | TimescaleDB uses 2 GB shared_buffers |
| 20 GB disk | — | For DB data volume growth over time |

> **Python version must match exactly.** If the dev machine uses 3.10.11, install 3.10.11
> on the server. `psycopg2-binary` and some C-extension wheels are version-specific.

---

## 3. Step 1 — Install Docker Desktop

1. Download Docker Desktop from **https://www.docker.com/products/docker-desktop/**
2. Run the installer — accept defaults, enable WSL2 integration when prompted
3. After install, start Docker Desktop from the Start menu
4. Wait for the whale icon in the system tray to stop animating (Docker is ready)
5. Verify from a command prompt:
   ```cmd
   
   docker version
   docker compose version
   ```
   Both commands should print version info, no errors.

> **WSL2 vs Hyper-V.** Docker Desktop defaults to WSL2 on Windows 10/11 — use it.
> WSL2 containers start faster and use less memory than Hyper-V mode.

> **Docker Desktop must be running** before the TimescaleDB container can start.
> In Step 10 you will configure Docker Desktop to start automatically with Windows.

---

## 4. Step 2 — Start TimescaleDB

From the project root directory (where `docker-compose.timescaledb.yml` lives):

```cmd
docker compose -f docker-compose.timescaledb.yml up -d
```

This will:
- Pull `timescale/timescaledb:latest-pg16` on first run (about 400 MB)
- Create a named volume `timescaledb_data` for persistent storage
- Start container `monitoring_timescaledb` on port **5433**

### Verify it is healthy

```cmd
docker ps
```

Expected output:

```
CONTAINER ID   IMAGE                              STATUS
xxxxxxxxxxxx   timescale/timescaledb:latest-pg16  Up X minutes (healthy)
```

The `(healthy)` status confirms the PostgreSQL healthcheck passed. If it shows
`(health: starting)`, wait 20–30 seconds and run `docker ps` again.

### Connect and verify TimescaleDB extension

```cmd
docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db
```

Inside the psql shell:

```sql
SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';
```

Expected: a version string like `2.25.2`. Type `\q` to exit.

### Port conflict check

If port 5433 is already in use, find what is using it:

```cmd
netstat -ano | findstr :5433
```

If you have a native PostgreSQL on 5433, change the host port in
`docker-compose.timescaledb.yml` (e.g. `5434:5432`) and update `DATABASE_URL` in `.env`
accordingly.

---

## 5. Step 3 — Install Python and dependencies

### 5.1 Install Python 3.10

Download from **https://www.python.org/downloads/release/python-31011/**

During install:
- Check **"Add Python to PATH"**
- Check **"Install for all users"** (recommended for service installs)

Verify:

```cmd
python --version
```

Expected: `Python 3.10.x`

### 5.2 Install project dependencies

From the project root directory:

```cmd
pip install -r requirements.txt
```

This installs Flask, SQLAlchemy, psycopg2-binary, Waitress, Redis client, and all other
dependencies listed in `requirements.txt`.

If you see errors about `psycopg2-binary` on Windows, install the Visual C++ redistributable:
**https://aka.ms/vs/17/release/vc_redist.x64.exe**

### 5.3 Verify Waitress is available

```cmd
python -c "import waitress; print(waitress.__version__)"
```

Expected: version string like `3.0.0`

---

## 6. Step 4 — Create the .env file

The app reads all secrets and configuration from a `.env` file in the project root.
This file is **never committed to git** (it is in `.gitignore`).

Create `D:\device_monitoring_tactical\.env` with the following content, then fill in
each value:

```env
# ─── Required ──────────────────────────────────────────────────────────────
APP_ENV=production

DATABASE_URL=postgresql+psycopg2://monitoring_man:admin123@localhost:5433/monitoring_db

SECRET_KEY=REPLACE_WITH_GENERATED_KEY

FERNET_KEY=REPLACE_WITH_GENERATED_KEY

# ─── Agent authentication ───────────────────────────────────────────────────
TRACKING_API_KEY=REPLACE_WITH_STRONG_RANDOM_STRING

# ─── Session security ───────────────────────────────────────────────────────
# Keep False until HTTPS is configured; set True once TLS is in place
SESSION_COOKIE_SECURE=False

SESSION_TIMEOUT_MINUTES=30

# ─── Database enforcement ───────────────────────────────────────────────────
REQUIRE_POSTGRES=true

# ─── Optional: Redis (leave default if Redis is not installed) ──────────────
REDIS_URL=redis://localhost:6379/0

# ─── Optional: SMTP email alerts ───────────────────────────────────────────
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=

# ─── Optional: SNMP ────────────────────────────────────────────────────────
SNMP_COMMUNITY=public
SNMP_VERSION=2c
```

> **DATABASE_URL breakdown:**
> ```
> postgresql+psycopg2://  ← SQLAlchemy driver dialect
> monitoring_man:admin123 ← user:password from docker-compose.timescaledb.yml
> @localhost:5433         ← host:port (5433 = Docker mapped port)
> /monitoring_db          ← database name
> ```
> If you changed the port in docker-compose, update `:5433` here to match.

---

## 7. Step 5 — Generate secret keys

Both `SECRET_KEY` and `FERNET_KEY` must be generated once, saved to `.env`, and never
changed after data is in the database (FERNET_KEY encrypts stored SNMP credentials —
rotating it without a migration will corrupt existing encrypted values).

### Generate SECRET_KEY (Flask session signing)

```cmd
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output into `.env`:

```env
SECRET_KEY=a3f8c2d1e4b7...  (your generated value)
```

### Generate FERNET_KEY (field encryption)

```cmd
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output into `.env`:

```env
FERNET_KEY=your-base64-fernet-key=
```

> **Back up these keys.** Store them somewhere safe (password manager, encrypted USB).
> If you lose FERNET_KEY, all encrypted SNMP community strings in the database become
> unreadable and must be re-entered manually.

### Generate TRACKING_API_KEY (agent auth token)

```cmd
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

This same value must also be set in the agents' `config.json`:

```json
{ "tracking_api_key": "your-generated-token" }
```

Or as an env var on agent machines:

```
TRACKING_API_KEY=your-generated-token
```

---

## 8. Step 6 — First boot smoke-test

Before registering as a service, run the server manually to confirm the database
connection and schema migration work correctly.

```cmd
cd D:\device_monitoring_tactical
python run_prod.py
```

Expected console output:

```
Starting Production Server on port 5001...
Access at http://localhost:5001
```

Behind the scenes the app runs all schema migrations via `utils/db_migrations.py`
(creates all tables, hypertables, indexes) before Waitress begins accepting connections.

### Verify in your browser

Open **http://localhost:5001** — you should see the NMS login page.

### Verify the database got migrated

```cmd
docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db -c "\dt"
```

You should see 30+ tables including `devices`, `scan_history`, `server_health_logs`,
`tracking_samples`, etc.

### Stop the test run

Press `Ctrl+C` in the command prompt.

---

## 9. Step 7 — Register the server as a Windows service (NSSM)

Running the server as a Windows service means it starts automatically at boot, restarts
on crash, and logs stdout/stderr to rotating files.

### 9.1 Confirm NSSM is available

```cmd
deploy\tools\nssm.exe version
```

If missing, download the 64-bit NSSM binary and place it at `deploy\tools\nssm.exe`
(see `deploy\tools\README.txt`).

### 9.2 Find your Python executable path

```cmd
where python
```

Note the full path — e.g. `C:\Python310\python.exe` or
`C:\Users\YourName\AppData\Local\Programs\Python\Python310\python.exe`.

### 9.3 Register the service

Run the following from an **Administrator** command prompt.
Replace `C:\Python310\python.exe` with your actual Python path.
Replace `D:\device_monitoring_tactical` with the actual project path.

```cmd
set PROJECT_DIR=D:\device_monitoring_tactical
set PYTHON=C:\Python310\python.exe
set NSSM=D:\device_monitoring_tactical\deploy\tools\nssm.exe
set LOG_DIR=C:\ProgramData\nms-server

:: Create log directory
mkdir "%LOG_DIR%"

:: Register the service
%NSSM% install NMSAdminServer "%PYTHON%" "run_prod.py"

:: Set working directory (critical — .env is loaded relative to this)
%NSSM% set NMSAdminServer AppDirectory "%PROJECT_DIR%"

:: Human-readable names
%NSSM% set NMSAdminServer DisplayName "NMS Admin Server"
%NSSM% set NMSAdminServer Description "Network Monitoring System — Flask/Waitress admin dashboard"

:: Start automatically at boot
%NSSM% set NMSAdminServer Start SERVICE_AUTO_START

:: Log stdout and stderr
%NSSM% set NMSAdminServer AppStdout "%LOG_DIR%\stdout.log"
%NSSM% set NMSAdminServer AppStderr "%LOG_DIR%\stderr.log"
%NSSM% set NMSAdminServer AppRotateFiles 1
%NSSM% set NMSAdminServer AppRotateBytes 10485760

:: Restart on any exit, 10-second delay (lets DB container settle after reboot)
%NSSM% set NMSAdminServer AppExit Default Restart
%NSSM% set NMSAdminServer AppRestartDelay 10000

:: Start the service now
%NSSM% start NMSAdminServer
```

> **AppRestartDelay 10000** (10 seconds).
> On a fresh reboot, Docker Desktop and the TimescaleDB container take 15–30 seconds to
> become ready. If the NMS server starts before the DB is up, it will crash and NSSM will
> restart it. A 10-second delay reduces the number of retry cycles on boot.

### 9.4 Verify the service started

```cmd
%NSSM% status NMSAdminServer
```

Expected: `SERVICE_RUNNING`

Then open **http://localhost:5001** in a browser — the login page should appear.

---

## 10. Step 8 — Make TimescaleDB start with Windows

Docker Desktop must be running and the container must be started before NSSM starts the
Flask server. There are two ways to ensure this.

### Option A — Docker Desktop auto-start (simplest)

1. Open Docker Desktop
2. Go to **Settings → General**
3. Enable **"Start Docker Desktop when you sign in to your computer"**

Docker Desktop will then start automatically at login. The `monitoring_timescaledb`
container has `restart: unless-stopped` in the compose file, so it resumes automatically
whenever Docker starts.

> **Limitation:** Docker Desktop requires a user to be logged in. For a true headless
> server with no interactive login, use Option B.

### Option B — Docker service + Task Scheduler (headless server)

For a machine where no user stays logged in (e.g., a server you RDP into):

**Step 1 — Enable the Docker Engine Windows service:**

Open PowerShell as Administrator:

```powershell
Set-Service -Name com.docker.service -StartupType Automatic
Start-Service com.docker.service
```

**Step 2 — Create a Task Scheduler task to start the container at boot:**

Open Task Scheduler → Create Task:

| Field | Value |
|-------|-------|
| Name | `Start TimescaleDB` |
| Security options | Run whether user is logged on or not |
| Run with highest privileges | Checked |
| Trigger | At startup |
| Action — Program | `docker` |
| Action — Arguments | `compose -f D:\device_monitoring_tactical\docker-compose.timescaledb.yml up -d` |
| Action — Start in | `D:\device_monitoring_tactical` |

Or create via command line (run as Administrator):

```cmd
schtasks /create /tn "Start TimescaleDB" /tr "docker compose -f D:\device_monitoring_tactical\docker-compose.timescaledb.yml up -d" /sc onstart /ru SYSTEM /rl HIGHEST /f
```

**Step 3 — Increase NSSM restart delay for the Flask server**

Since Docker takes longer to start as a service than Docker Desktop, give it more time:

```cmd
nssm set NMSAdminServer AppRestartDelay 30000
```

This means if the Flask server starts before the DB is ready, NSSM will wait 30 seconds
before retrying — enough for Docker to bring the container up.

### Boot order summary

```
Windows boots
    → Docker Engine service starts  (auto)
    → Task Scheduler fires at startup
        → docker compose up -d  (starts monitoring_timescaledb)
    → NSSM starts NMSAdminServer  (auto)
        → if DB not ready: crash → wait 30s → retry
        → when DB ready: Flask app starts, runs migrations, serves traffic
```

---

## 11. Verifying the full stack

Run these checks after a fresh boot or deployment:

### 1. TimescaleDB container is healthy

```cmd
docker ps
```

Look for `monitoring_timescaledb` with status `Up X minutes (healthy)`.

### 2. NMS server service is running

```cmd
nssm status NMSAdminServer
```

Expected: `SERVICE_RUNNING`

### 3. Server is accepting HTTP

```cmd
curl http://localhost:5001/health
```

Expected: `{"status": "ok"}` or HTTP 200. If curl is not installed:

```cmd
powershell -Command "Invoke-WebRequest http://localhost:5001/health"
```

### 4. Database connection is live

```cmd
docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db -c "SELECT count(*) FROM devices;"
```

Expected: a number (0 on first boot, more after devices are added).

### 5. Check server startup log

```cmd
type "C:\ProgramData\nms-server\stdout.log"
```

Look for `Starting Production Server on port 5001...` with no Python tracebacks after it.

### 6. Scheduler is running

After the server is up, check the scheduler health endpoint:

```
GET http://localhost:5001/api/maintenance/scheduler/status
```

(Requires admin login cookie — use a browser after logging in.)
All 9 rollup jobs should show `status: scheduled` or `status: ok`.

---

## 12. Managing the service after install

All commands require an **Administrator** command prompt.

| Action | Command |
|--------|---------|
| Start server | `nssm start NMSAdminServer` |
| Stop server | `nssm stop NMSAdminServer` |
| Restart server | `nssm restart NMSAdminServer` |
| View status | `nssm status NMSAdminServer` |
| Edit settings (GUI) | `nssm edit NMSAdminServer` |
| View all settings | `nssm dump NMSAdminServer` |

### TimescaleDB container management

```cmd
:: Stop
docker stop monitoring_timescaledb

:: Start
docker start monitoring_timescaledb

:: Restart
docker restart monitoring_timescaledb

:: View logs (last 50 lines)
docker logs monitoring_timescaledb --tail 50

:: Follow logs live
docker logs monitoring_timescaledb -f
```

> **Always stop the NMS server before stopping the database container.**
> Otherwise in-flight queries will error and the server's connection pool will degrade.
>
> ```cmd
> nssm stop NMSAdminServer
> docker stop monitoring_timescaledb
> ```

---

## 13. Environment variable reference

All variables go in `D:\device_monitoring_tactical\.env`.

### Required

| Variable | Example | Description |
|----------|---------|-------------|
| `APP_ENV` | `production` | Set to `production` to disable debug mode and template auto-reload |
| `DATABASE_URL` | `postgresql+psycopg2://monitoring_man:admin123@localhost:5433/monitoring_db` | Full DB connection string |
| `SECRET_KEY` | `a3f8c2...` (64 hex chars) | Flask session signing key — generate once, never change |
| `FERNET_KEY` | `abc123==` (base64) | Field encryption key for SNMP strings — generate once, never change |
| `TRACKING_API_KEY` | `random-token` | Shared auth token accepted from agents |

### Session and security

| Variable | Default | Description |
|----------|---------|-------------|
| `SESSION_COOKIE_SECURE` | `False` | Set `True` only when HTTPS is in place |
| `SESSION_TIMEOUT_MINUTES` | `30` | Idle session timeout |
| `REQUIRE_POSTGRES` | `false` | Set `true` to refuse startup if DATABASE_URL is not PostgreSQL |

### Database connection pool

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_POOL_SIZE` | `20` | Persistent connections in pool |
| `DB_POOL_MAX_OVERFLOW` | `20` | Extra connections allowed above pool_size |
| `DB_POOL_TIMEOUT_SECONDS` | `30` | Timeout waiting for a connection |
| `DB_POOL_RECYCLE_SECONDS` | `1800` | Recycle connections after 30 min to prevent stale connections |

### Redis (optional but recommended)

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |

Redis is used for the tracking real-time cache (8-second TTL anti-flicker) and dashboard
cache namespace. The app works without Redis — all Redis operations are best-effort.
If Redis is not installed, leave the default and ignore any Redis connection warnings in logs.

### Performance and reports

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_REPORT_RANGE_DAYS` | `90` | Maximum date range for reports |
| `MAX_EXPORT_ROWS` | `50000` | Maximum rows in a CSV/XLSX export |
| `REPORT_STATEMENT_TIMEOUT_MS` | `5000` | Kill report queries longer than 5 seconds |

---

## 14. Database backup and restore

### Backup

Use `pg_dump` inside the Docker container to produce a portable SQL dump:

```cmd
docker exec monitoring_timescaledb pg_dump -U monitoring_man -d monitoring_db -Fc -f /tmp/nms_backup.dump
docker cp monitoring_timescaledb:/tmp/nms_backup.dump D:\nms_backups\nms_backup_%date:~-4,4%%date:~-7,2%%date:~-10,2%.dump
```

- `-Fc` = custom format (compressed, supports parallel restore)
- The `%date:...%` fragment adds today's date to the filename on Windows

Schedule daily backups with Task Scheduler pointing to a `.bat` file containing these two
commands.

### Restore

```cmd
:: Stop the server first
nssm stop NMSAdminServer

:: Drop and recreate the database
docker exec monitoring_timescaledb psql -U monitoring_man -d postgres -c "DROP DATABASE monitoring_db;"
docker exec monitoring_timescaledb psql -U monitoring_man -d postgres -c "CREATE DATABASE monitoring_db;"

:: Copy dump into container and restore
docker cp D:\nms_backups\nms_backup_YYYYMMDD.dump monitoring_timescaledb:/tmp/restore.dump
docker exec monitoring_timescaledb pg_restore -U monitoring_man -d monitoring_db /tmp/restore.dump

:: Start the server
nssm start NMSAdminServer
```

> TimescaleDB dumps created with `pg_dump -Fc` include TimescaleDB chunk metadata.
> Restore into a TimescaleDB instance only (not plain PostgreSQL).

---

## 15. Updating the server

### Pull code changes

```cmd
nssm stop NMSAdminServer
cd D:\device_monitoring_tactical
git pull
pip install -r requirements.txt
nssm start NMSAdminServer
```

Schema migrations run automatically on startup — no manual migration step required.
The migration runner in `utils/db_migrations.py` is idempotent (safe to run repeatedly).

### Rebuilding after a Python dependency change

If `requirements.txt` changed:

```cmd
nssm stop NMSAdminServer
pip install -r requirements.txt
nssm start NMSAdminServer
```

### Updating TimescaleDB

```cmd
nssm stop NMSAdminServer
docker compose -f docker-compose.timescaledb.yml pull
docker compose -f docker-compose.timescaledb.yml up -d
nssm start NMSAdminServer
```

> **Back up before pulling a new TimescaleDB image.** Major version upgrades (e.g., pg15 →
> pg16) require a data migration. Always check the TimescaleDB upgrade docs before pulling
> a new image tag.

---

## 16. Troubleshooting

### Server crashes immediately at startup

Check the NSSM stderr log first:

```cmd
type "C:\ProgramData\nms-server\stderr.log"
```

**Common causes:**

| Error in log | Cause | Fix |
|-------------|-------|-----|
| `could not connect to server` | TimescaleDB not running | `docker start monitoring_timescaledb` |
| `FERNET_KEY environment variable is not set` | Missing .env or key | Add `FERNET_KEY=...` to `.env` |
| `REQUIRE_POSTGRES is enabled, but backend is 'sqlite'` | DATABASE_URL missing | Check `.env` has `DATABASE_URL=postgresql+...` |
| `ModuleNotFoundError: No module named 'waitress'` | pip install not done | `pip install -r requirements.txt` |
| `address already in use` (port 5001) | Another process on port 5001 | `netstat -ano \| findstr :5001` to find it |
| `change-this-secret-key-in-production` warning | Default SECRET_KEY still set | Generate and set `SECRET_KEY` in `.env` |

### `docker ps` shows container is restarting / unhealthy

```cmd
docker logs monitoring_timescaledb --tail 50
```

Likely causes:
- Port 5433 already in use on the host
- Not enough RAM — TimescaleDB needs ~2.5 GB free
- Docker Desktop not fully started — wait 60 seconds after boot

### Login page loads but login fails

Check that `SECRET_KEY` in `.env` is set and consistent. Changing `SECRET_KEY` invalidates
all existing sessions — users must log in again.

### "Database is locked" or long response times

This only happens with SQLite (dev mode). If `DATABASE_URL` is set to PostgreSQL and you
are still seeing slow queries, check:

```cmd
docker stats monitoring_timescaledb
```

If memory is being constrained, the shared_buffers setting in `docker-compose.timescaledb.yml`
may need reducing or Docker Desktop's memory limit needs increasing (Settings → Resources).

### Rollup jobs not running

Check the scheduler status endpoint (logged-in admin browser):

```
http://localhost:5001/api/maintenance/scheduler/status
```

If jobs show `never_run`, trigger a manual backfill:

```
POST http://localhost:5001/api/maintenance/backfill-rollups
```

Then restart the server to reset the scheduler:

```cmd
nssm restart NMSAdminServer
```

### TimescaleDB data volume survives `docker compose down`?

Yes. The named volume `timescaledb_data` persists unless you explicitly delete it:

```cmd
:: Safe — stops container, keeps volume
docker compose -f docker-compose.timescaledb.yml down

:: DESTRUCTIVE — deletes all database data
docker compose -f docker-compose.timescaledb.yml down -v
```

Never use `-v` in production.

### How do I connect to the database directly?

```cmd
docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db
```

Useful psql commands:

```sql
\dt                          -- list all tables
\d devices                   -- describe a table
SELECT count(*) FROM devices;
SELECT * FROM v_timescaledb_job_health;   -- TimescaleDB background job health
\q                           -- quit
```

---

## Quick reference card

```
START DB      docker start monitoring_timescaledb
STOP DB       docker stop monitoring_timescaledb
DB LOGS       docker logs monitoring_timescaledb --tail 50

START APP     nssm start NMSAdminServer
STOP APP      nssm stop NMSAdminServer
RESTART APP   nssm restart NMSAdminServer
APP LOGS      type C:\ProgramData\nms-server\stdout.log

CONNECT DB    docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db
BACKUP DB     docker exec monitoring_timescaledb pg_dump -U monitoring_man -d monitoring_db -Fc -f /tmp/backup.dump
              docker cp monitoring_timescaledb:/tmp/backup.dump D:\nms_backups\

ENV FILE      D:\device_monitoring_tactical\.env
COMPOSE FILE  D:\device_monitoring_tactical\docker-compose.timescaledb.yml
```
