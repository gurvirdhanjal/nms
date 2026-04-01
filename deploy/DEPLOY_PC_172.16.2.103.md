# NMS Admin Server — Deployment Guide
## Machine: 172.16.2.103 | Docker Desktop + TimescaleDB

This is the step-by-step guide for this specific machine.
No general theory — just what to run, in order.

```
Machine IP:   172.16.2.103
Dashboard:    http://172.16.2.103:5001
Database:     localhost:5433  (TimescaleDB in Docker)
Project path: D:\device_monitoring_tactical
```

---

## Before you start — checklist

- [ ] Windows 10 or 11 (64-bit)
- [ ] You are logged in as Administrator (or have admin rights)
- [ ] The project folder exists at `D:\device_monitoring_tactical`
- [ ] Internet access (to download Docker Desktop, Python, NSSM)

---

## Step 1 — Install Docker Desktop

1. Download from **https://www.docker.com/products/docker-desktop/**
   File name: `Docker Desktop Installer.exe`

2. Run the installer → accept all defaults → enable WSL2 when prompted → **Restart**

3. After restart, Docker Desktop opens automatically. Wait for the whale icon
   in the taskbar tray to stop animating (takes ~60 seconds on first boot).

4. Open **Command Prompt** and verify:
   ```cmd
   docker version
   docker compose version
   ```
   Both should print version info. If you see an error, Docker is not ready yet — wait
   another 30 seconds and try again.

---

## Step 2 — Start TimescaleDB

Open **Command Prompt** in the project folder:

```cmd
cd D:\device_monitoring_tactical
docker compose -f docker-compose.timescaledb.yml up -d
```

First run downloads the TimescaleDB image (~400 MB). Wait for it to finish.

**Verify it is healthy:**

```cmd
docker ps
```

Look for this line:

```
monitoring_timescaledb   ...   Up X minutes (healthy)
```

If it shows `(health: starting)`, wait 30 seconds and run `docker ps` again.

**Test the database is reachable:**

```cmd
docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db -c "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';"
```

Expected output: a version number like `2.25.2`. Type `exit` if psql stays open.

---

## Step 3 — Install Python 3.10

1. Download from **https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe**

2. Run the installer:
   - **Check "Add Python 3.10 to PATH"** ← important
   - Click "Install Now"

3. Close and reopen Command Prompt, then verify:
   ```cmd
   python --version
   ```
   Expected: `Python 3.10.11`

4. Install project dependencies:
   ```cmd
   cd D:\device_monitoring_tactical
   pip install -r requirements.txt
   ```
   This takes 2–5 minutes. Wait for it to finish.

5. Verify the database driver:
   ```cmd
   python -c "import psycopg2; print('OK')"
   ```
   Expected: `OK`

---

## Step 4 — Create the .env file

```cmd
copy D:\device_monitoring_tactical\deploy\config.templates\nms-server.env.template D:\device_monitoring_tactical\.env
notepad D:\device_monitoring_tactical\.env
```

The file opens in Notepad. You need to fill in **three values**.

### Generate SECRET_KEY

Open a second Command Prompt and run:

```cmd
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output. In Notepad, replace `REPLACE_WITH_GENERATED_64_CHAR_HEX` with it:

```env
SECRET_KEY=a3f8c2d1...your 64 character hex string here...
```

### Generate FERNET_KEY

```cmd
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output. Replace `REPLACE_WITH_GENERATED_FERNET_KEY`:

```env
FERNET_KEY=abc123...your base64 string here...==
```

> **Important:** Once the server runs for the first time and devices are added,
> never change FERNET_KEY. It encrypts stored SNMP passwords. Changing it
> corrupts those values.

### Generate TRACKING_API_KEY

```cmd
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the output. Replace `REPLACE_WITH_STRONG_RANDOM_TOKEN`:

```env
TRACKING_API_KEY=your-random-token-here
```

> Write this value down — you will paste it into every agent's `config.json`.

### Check the DATABASE_URL line

It should already read:

```env
DATABASE_URL=postgresql+psycopg2://monitoring_man:admin123@localhost:5433/monitoring_db
```

Do not change this line. `localhost:5433` is correct for Docker.

**Save and close Notepad.**

---

## Step 5 — Smoke-test (run the server manually once)

Make sure TimescaleDB is still running (`docker ps` shows healthy), then:

```cmd
cd D:\device_monitoring_tactical
python nms_server_main.py
```

Expected output — look for these lines (order may vary):

```
[DB] Database connection OK
[OK] Default admin user created.
[OK] Monitoring scheduler started.
[NMS] Accessible at: http://172.16.2.103:5001
```

Open a browser on this PC: **http://localhost:5001**
Open a browser on another PC on the network: **http://172.16.2.103:5001**

You should see the NMS login page. Login: `admin` / `admin123`

Press **Ctrl+C** to stop the server.

If you see `could not connect to server` instead — TimescaleDB is not running.
Run `docker start monitoring_timescaledb` and try again.

---

## Step 6 — Get NSSM

NSSM registers the server as a Windows service so it auto-starts at boot and
restarts on crash.

1. Download from **https://nssm.cc/download**
   File: `nssm-2.24.zip`

2. Extract the zip

3. Copy `nssm-2.24\win64\nssm.exe` to:
   ```
   D:\device_monitoring_tactical\deploy\tools\nssm.exe
   ```

Verify:

```cmd
D:\device_monitoring_tactical\deploy\tools\nssm.exe version
```

Expected: `NSSM version 2.24 ...`

---

## Step 7 — Register the server as a Windows service

Open **Command Prompt as Administrator** (right-click → Run as administrator):

```cmd
set PROJECT=D:\device_monitoring_tactical
set NSSM=%PROJECT%\deploy\tools\nssm.exe
set LOGDIR=C:\ProgramData\nms-server

:: Create log directory
mkdir "%LOGDIR%"

:: Find Python path
where python
```

Note the Python path printed (e.g. `C:\Python310\python.exe` or
`C:\Users\YourName\AppData\Local\Programs\Python\Python310\python.exe`).

```cmd
:: Replace C:\Python310\python.exe with your actual Python path below
set PYTHON=C:\Python310\python.exe

:: Register the service
%NSSM% install NMSAdminServer "%PYTHON%" "nms_server_main.py"
%NSSM% set NMSAdminServer AppDirectory "%PROJECT%"
%NSSM% set NMSAdminServer DisplayName "NMS Admin Server"
%NSSM% set NMSAdminServer Description "NMS Dashboard — 172.16.2.103:5001"
%NSSM% set NMSAdminServer Start SERVICE_AUTO_START
%NSSM% set NMSAdminServer AppStdout "%LOGDIR%\stdout.log"
%NSSM% set NMSAdminServer AppStderr "%LOGDIR%\stderr.log"
%NSSM% set NMSAdminServer AppRotateFiles 1
%NSSM% set NMSAdminServer AppRotateBytes 10485760
%NSSM% set NMSAdminServer AppExit Default Restart
%NSSM% set NMSAdminServer AppRestartDelay 15000

:: Start the service
%NSSM% start NMSAdminServer
```

**Check it started:**

```cmd
%NSSM% status NMSAdminServer
```

Expected: `SERVICE_RUNNING`

Open **http://172.16.2.103:5001** — the login page should appear.

---

## Step 8 — Make Docker start automatically at boot

Docker Desktop must be running for the TimescaleDB container to start.

1. Open **Docker Desktop**
2. Click the gear icon (Settings)
3. Go to **General**
4. Enable **"Start Docker Desktop when you sign in to your computer"**
5. Click **Apply & Restart**

The `monitoring_timescaledb` container has `restart: unless-stopped` in the compose
file, so it resumes automatically every time Docker starts — no extra configuration
needed.

**Boot order after this setup:**

```
PC boots → user signs in
  → Docker Desktop starts automatically
      → monitoring_timescaledb container resumes (restart: unless-stopped)
  → NSSM starts NMSAdminServer automatically
      → if DB not ready yet: crash → wait 15s → retry
      → when DB ready: server starts, runs schema migrations, serves on :5001
```

---

## Step 9 — Open the firewall (if other PCs can't reach :5001)

If the login page works on this PC but not from other machines:

Open **Command Prompt as Administrator**:

```cmd
netsh advfirewall firewall add rule name="NMS Admin Server port 5001" ^
  dir=in action=allow protocol=TCP localport=5001
```

Test from another machine: **http://172.16.2.103:5001**

---

## Verification checklist (run after a reboot)

```cmd
:: 1. Docker is running
docker ps
::    → monitoring_timescaledb  Up X minutes (healthy)

:: 2. NMS service is running
D:\device_monitoring_tactical\deploy\tools\nssm.exe status NMSAdminServer
::    → SERVICE_RUNNING

:: 3. Server responds
curl http://localhost:5001/health
::    → HTTP 200

:: 4. Startup log is clean
type C:\ProgramData\nms-server\stdout.log
::    → [DB] Database connection OK
::    → [NMS] Accessible at: http://172.16.2.103:5001
```

---

## Day-to-day operations

```cmd
set NSSM=D:\device_monitoring_tactical\deploy\tools\nssm.exe

:: Restart the server (e.g. after changing .env)
%NSSM% restart NMSAdminServer

:: View live logs
powershell -Command "Get-Content 'C:\ProgramData\nms-server\stdout.log' -Wait -Tail 50"

:: Stop server before stopping Docker
%NSSM% stop NMSAdminServer
docker stop monitoring_timescaledb

:: Start both back up
docker start monitoring_timescaledb
%NSSM% start NMSAdminServer

:: Connect to DB directly
docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db
```

---

## Backup

```cmd
:: Create backup folder
mkdir D:\nms_backups

:: Take a backup (run this daily)
docker exec monitoring_timescaledb pg_dump -U monitoring_man -d monitoring_db -Fc -f /tmp/backup.dump
docker cp monitoring_timescaledb:/tmp/backup.dump D:\nms_backups\nms_backup_%date:~-4,4%%date:~-7,2%%date:~-10,2%.dump
```

---

## Troubleshooting

| Problem | Check | Fix |
|---------|-------|-----|
| Server won't start | `type C:\ProgramData\nms-server\stderr.log` | See table below |
| Can't reach :5001 from other PCs | Firewall rule | Run Step 9 |
| Docker not starting at boot | Docker Desktop settings | Re-check Step 8 |
| DB connection error after reboot | Container not healthy yet | `docker start monitoring_timescaledb` then `%NSSM% restart NMSAdminServer` |

**Stderr errors:**

| Error text | Fix |
|-----------|-----|
| `could not connect to server` port 5433 | `docker start monitoring_timescaledb` |
| `FERNET_KEY` not set | Check `.env` has `FERNET_KEY=...` |
| `No module named 'waitress'` | `pip install -r requirements.txt` |
| `address already in use` port 5001 | Something else is on 5001 — `netstat -ano \| findstr :5001` |
| `password authentication failed` | Check `monitoring_man:admin123` in `DATABASE_URL` matches docker-compose |

---

## Quick reference

```
DASHBOARD    http://172.16.2.103:5001
LOGIN        admin / admin123

DB START     docker start monitoring_timescaledb
DB STOP      docker stop  monitoring_timescaledb
DB STATUS    docker ps
DB CONNECT   docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db

APP START    nssm start   NMSAdminServer
APP STOP     nssm stop    NMSAdminServer
APP RESTART  nssm restart NMSAdminServer
APP LOGS     type C:\ProgramData\nms-server\stdout.log

ENV FILE     D:\device_monitoring_tactical\.env
NSSM         D:\device_monitoring_tactical\deploy\tools\nssm.exe

BACKUP       docker exec monitoring_timescaledb pg_dump -U monitoring_man -d monitoring_db -Fc -f /tmp/b.dump
             docker cp monitoring_timescaledb:/tmp/b.dump D:\nms_backups\
```
