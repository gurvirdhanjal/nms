# NMS Admin Server — Windows Server 2019 Deployment Guide

> **This guide replaces `SERVER_DEPLOYMENT_GUIDE.md` for Windows Server 2019.**
>
> The original guide assumes Docker Desktop, which **does not run on Windows Server 2019**.
> This guide uses WSL2 + Docker Engine instead — the correct path for Server 2019.

---

## What is different on Windows Server 2019

| Topic | Windows 10/11 | Windows Server 2019 |
|-------|--------------|---------------------|
| Docker | Docker Desktop | WSL2 + Docker Engine (see Step 2) |
| Microsoft Store | Available | Not available — download Ubuntu `.appx` manually |
| Python install | GUI installer | Same — download from python.org |
| NSSM service | Same | Same |
| Firewall | Mostly open | Port 5001 must be opened manually |
| Docker auto-start | Docker Desktop tray app | `wsl.conf` boot command + Task Scheduler |

---

## Contents

1. [Architecture on Server 2019](#1-architecture-on-server-2019)
2. [Step 1 — Enable WSL2](#2-step-1--enable-wsl2)
3. [Step 2 — Install Ubuntu and Docker Engine](#3-step-2--install-ubuntu-and-docker-engine)
4. [Step 3 — Start TimescaleDB](#4-step-3--start-timescaledb)
5. [Step 4 — Make Docker and TimescaleDB start at boot](#5-step-4--make-docker-and-timescaledb-start-at-boot)
6. [Step 5 — Install Python and dependencies](#6-step-5--install-python-and-dependencies)
7. [Step 6 — Create .env and generate keys](#7-step-6--create-env-and-generate-keys)
8. [Step 7 — First boot smoke-test](#8-step-7--first-boot-smoke-test)
9. [Step 8 — Register as a Windows service (NSSM)](#9-step-8--register-as-a-windows-service-nssm)
10. [Step 9 — Open firewall port 5001](#10-step-9--open-firewall-port-5001)
11. [Verifying the full stack](#11-verifying-the-full-stack)
12. [Managing services after install](#12-managing-services-after-install)
13. [Database backup and restore](#13-database-backup-and-restore)
14. [Updating the server](#14-updating-the-server)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Architecture on Server 2019

```
 ┌───────────────────────────────────────────────────────────┐
 │  Windows Server 2019  (172.16.2.103)                      │
 │                                                           │
 │  ┌──────────────────────────┐                             │
 │  │  NMSAdminServer (NSSM)   │  port 5001 (all interfaces) │
 │  │  NMSAdminServer.exe      │  Waitress / 6 threads       │
 │  │  + monitoring scheduler  │                             │
 │  └──────────────┬───────────┘                             │
 │                 │ psycopg2 → localhost:5433                │
 │  ┌──────────────▼───────────┐                             │
 │  │  WSL2 Ubuntu             │                             │
 │  │  ┌─────────────────────┐ │                             │
 │  │  │  Docker Engine      │ │                             │
 │  │  │  monitoring_tsdb    │ │  5433 → pg 5432             │
 │  │  │  TimescaleDB pg16   │ │                             │
 │  │  └─────────────────────┘ │                             │
 │  └──────────────────────────┘                             │
 │                 │                                         │
 │                 ▼  named volume (persists)                │
 │  timescaledb_data                                         │
 └───────────────────────────────────────────────────────────┘
```

**Port 5433 works from Windows to WSL2.**
WSL2 automatically bridges `localhost` — `psycopg2` connecting to `localhost:5433` on
Windows reaches the Docker container running inside WSL2 without any extra configuration.

---

## 2. Step 1 — Enable WSL2

Open **PowerShell as Administrator** and run all of these:

```powershell
# Enable Windows Subsystem for Linux
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart

# Enable Virtual Machine Platform (required for WSL2)
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
```

**Restart the server now:**

```powershell
Restart-Computer
```

After reboot, open **PowerShell as Administrator** again:

```powershell
# Set WSL2 as the default version
wsl --set-default-version 2
```

### Install the WSL2 Linux kernel update

Windows Server 2019 does not automatically deliver the WSL2 kernel via Windows Update.
Download and install it manually:

```powershell
# Download the kernel update package
Invoke-WebRequest -Uri "https://wslstorestorage.blob.core.windows.net/wslblob/wsl_update_x64.msi" -OutFile "$env:TEMP\wsl_update_x64.msi"

# Install it silently
Start-Process msiexec.exe -ArgumentList "/i $env:TEMP\wsl_update_x64.msi /quiet" -Wait
```

Verify WSL2 kernel is installed:

```powershell
wsl --status
```

Expected output includes `Default Version: 2`.

---

## 3. Step 2 — Install Ubuntu and Docker Engine

### 3.1 Install Ubuntu 22.04 (no Microsoft Store — use manual download)

```powershell
# Download Ubuntu 22.04 appx bundle
Invoke-WebRequest -Uri "https://aka.ms/wslubuntu2204" -OutFile "$env:TEMP\Ubuntu2204.appx"

# Install it
Add-AppxPackage "$env:TEMP\Ubuntu2204.appx"
```

Launch Ubuntu for the first time to complete setup:

```powershell
ubuntu2204.exe
```

When prompted, create a username and password (e.g., `nmsadmin` / your password).
You are now inside Ubuntu. Run all following commands in this Ubuntu terminal unless
noted otherwise.

### 3.2 Install Docker Engine inside Ubuntu

```bash
# Update package list
sudo apt-get update

# Install prerequisites
sudo apt-get install -y ca-certificates curl gnupg lsb-release

# Add Docker's official GPG key
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# Add the Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine + Compose plugin
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Add your user to the docker group (avoids sudo on every command)
sudo usermod -aG docker $USER

# Start Docker now
sudo service docker start
```

Close the Ubuntu terminal and reopen it (to apply group membership), then verify:

```bash
docker version
docker compose version
```

Both should print version info without `permission denied` errors.

---

## 4. Step 3 — Start TimescaleDB

From inside the **Ubuntu WSL2 terminal**, navigate to the project directory.

Your Windows `D:\` drive is accessible inside WSL2 at `/mnt/d/`:

```bash
cd /mnt/d/device_monitoring_tactical
```

Start TimescaleDB:

```bash
docker compose -f docker-compose.timescaledb.yml up -d
```

On first run this pulls the TimescaleDB image (~400 MB). Wait for it to finish.

Verify the container is healthy:

```bash
docker ps
```

Expected:

```
CONTAINER ID   IMAGE                              STATUS
xxxxxxxxxxxx   timescale/timescaledb:latest-pg16  Up X minutes (healthy)
```

Test the database connection from inside WSL2:

```bash
docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db -c \
  "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';"
```

Expected: a version row like `2.25.2`.

Test from **Windows PowerShell** that `localhost:5433` is reachable:

```powershell
Test-NetConnection -ComputerName localhost -Port 5433
```

Expected: `TcpTestSucceeded : True`

---

## 5. Step 4 — Make Docker and TimescaleDB start at boot

### 5.1 Configure Docker to start when the WSL2 distro boots

Inside Ubuntu:

```bash
sudo tee /etc/wsl.conf > /dev/null <<'EOF'
[boot]
command = service docker start
EOF
```

This tells WSL2 to start the Docker daemon every time Ubuntu initialises.

### 5.2 Create a Task Scheduler task to start the WSL2 distro at Windows boot

Open **PowerShell as Administrator** on Windows:

```powershell
$action  = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d Ubuntu-22.04 -- bash -c 'cd /mnt/d/device_monitoring_tactical && docker compose -f docker-compose.timescaledb.yml up -d'"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest

Register-ScheduledTask `
  -TaskName   "Start-TimescaleDB" `
  -Action     $action `
  -Trigger    $trigger `
  -Settings   $settings `
  -Principal  $principal `
  -Description "Start WSL2 Ubuntu and TimescaleDB Docker container at boot"
```

Verify the task was created:

```powershell
Get-ScheduledTask -TaskName "Start-TimescaleDB"
```

### Boot order after this setup

```
Windows Server 2019 boots
  → Task Scheduler fires "Start-TimescaleDB" at startup (runs as SYSTEM)
      → wsl -d Ubuntu-22.04 wakes up
          → /etc/wsl.conf boots Docker daemon
          → docker compose up -d starts monitoring_timescaledb
  → NSSM starts NMSAdminServer (auto)
      → if DB not ready yet: crash → wait 15s → retry (NSSM restart policy)
      → when DB ready: server starts, runs migrations, serves on :5001
```

---

## 6. Step 5 — Install Python and dependencies

### 6.1 Download and install Python 3.10

Open a **Windows PowerShell as Administrator**:

```powershell
# Download Python 3.10.11 (64-bit)
Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe" `
  -OutFile "$env:TEMP\python-3.10.11.exe"

# Install for all users, add to PATH, no shortcuts
Start-Process "$env:TEMP\python-3.10.11.exe" `
  -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Shortcuts=0" `
  -Wait
```

Close and reopen PowerShell, then verify:

```powershell
python --version
# Expected: Python 3.10.11

pip --version
# Expected: pip XX.X from C:\Program Files\Python310\...
```

### 6.2 Install project dependencies

```powershell
cd D:\device_monitoring_tactical
pip install -r requirements.txt
```

Verify Waitress:

```powershell
python -c "import waitress; print('Waitress', waitress.__version__)"
```

Verify psycopg2:

```powershell
python -c "import psycopg2; print('psycopg2', psycopg2.__version__)"
```

---

## 7. Step 6 — Create .env and generate keys

Copy the template:

```powershell
copy "D:\device_monitoring_tactical\deploy\config.templates\nms-server.env.template" `
     "D:\device_monitoring_tactical\.env"
```

Open it:

```powershell
notepad D:\device_monitoring_tactical\.env
```

Generate and fill in the three required secrets — run each command in PowerShell:

**SECRET_KEY:**

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

**FERNET_KEY:**

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**TRACKING_API_KEY:**

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Paste each output into the corresponding line in `.env`. The `DATABASE_URL` and server
binding lines are already correct:

```env
DATABASE_URL=postgresql+psycopg2://monitoring_man:admin123@localhost:5433/monitoring_db
WEB_HOST=0.0.0.0
PORT=5001
APP_ENV=production
REQUIRE_POSTGRES=true
SESSION_COOKIE_SECURE=False
```

> **`localhost:5433`** works because WSL2 bridges `localhost` to the Docker network.
> You do not need to use the WSL2 IP address.

Save and close Notepad.

---

## 8. Step 7 — First boot smoke-test

> Make sure TimescaleDB is running first:
> ```powershell
> # From PowerShell — ask WSL2 to report container status
> wsl -d Ubuntu-22.04 -- docker ps
> ```
> Confirm `monitoring_timescaledb` shows `(healthy)`.

From **PowerShell**:

```powershell
cd D:\device_monitoring_tactical
python run_prod.py
```

Expected output:

```
[DB] Backend=postgresql Host=localhost DB=monitoring_db
[DB] Database connection OK
[OK] Discovery Service primed: ...
[OK] Default admin user created.
Starting Production Server on port 5001...
Access at http://localhost:5001
```

Open a browser on any machine on the LAN and navigate to:

```
http://172.16.2.103:5001
```

You should see the NMS login page. Log in with `admin` / `admin123`.

Press `Ctrl+C` to stop the test run.

---

## 9. Step 8 — Register as a Windows service (NSSM)

Get NSSM (64-bit) into `deploy\tools\nssm.exe` (see `deploy\tools\README.txt`).

Open **PowerShell as Administrator**:

```powershell
$PROJECT = "D:\device_monitoring_tactical"
$PYTHON  = (Get-Command python).Source        # finds the installed python.exe
$NSSM    = "$PROJECT\deploy\tools\nssm.exe"
$LOGDIR  = "C:\ProgramData\nms-server"

# Create log directory
New-Item -ItemType Directory -Force -Path $LOGDIR | Out-Null

# Register service
& $NSSM install NMSAdminServer $PYTHON "nms_server_main.py"
& $NSSM set NMSAdminServer AppDirectory $PROJECT
& $NSSM set NMSAdminServer DisplayName "NMS Admin Server"
& $NSSM set NMSAdminServer Description "NMS Flask dashboard — Waitress port 5001 @ 172.16.2.103"
& $NSSM set NMSAdminServer Start SERVICE_AUTO_START
& $NSSM set NMSAdminServer AppStdout "$LOGDIR\stdout.log"
& $NSSM set NMSAdminServer AppStderr "$LOGDIR\stderr.log"
& $NSSM set NMSAdminServer AppRotateFiles 1
& $NSSM set NMSAdminServer AppRotateBytes 10485760

# Restart on any exit — 15s delay lets TimescaleDB settle after boot
& $NSSM set NMSAdminServer AppExit Default Restart
& $NSSM set NMSAdminServer AppRestartDelay 15000

# Start the service
& $NSSM start NMSAdminServer
```

> **Why `nms_server_main.py` not `run_prod.py`?**
> `nms_server_main.py` starts Waitress AND the monitoring scheduler.
> `run_prod.py` starts Waitress only — background monitoring would not run.

Verify:

```powershell
& "D:\device_monitoring_tactical\deploy\tools\nssm.exe" status NMSAdminServer
# Expected: SERVICE_RUNNING
```

---

## 10. Step 9 — Open firewall port 5001

Windows Server 2019 blocks inbound connections by default. Agents and browsers on other
machines cannot reach port 5001 until you add a rule.

```powershell
New-NetFirewallRule `
  -DisplayName "NMS Admin Server (port 5001)" `
  -Direction    Inbound `
  -Protocol     TCP `
  -LocalPort    5001 `
  -Action       Allow `
  -Profile      Domain,Private
```

> Use `Domain,Private` to restrict to internal network only. Do not use `Public` unless
> you specifically need external access.

Verify the rule was added:

```powershell
Get-NetFirewallRule -DisplayName "NMS Admin Server*" | Select-Object DisplayName, Enabled, Direction
```

---

## 11. Verifying the full stack

Run these checks to confirm every component is healthy after a reboot.

### 1. TimescaleDB (via WSL2)

```powershell
wsl -d Ubuntu-22.04 -- docker ps
```

Look for `monitoring_timescaledb` with status `Up X minutes (healthy)`.

### 2. Port 5433 reachable from Windows

```powershell
Test-NetConnection -ComputerName localhost -Port 5433
# TcpTestSucceeded : True
```

### 3. NMSAdminServer service running

```powershell
& "D:\device_monitoring_tactical\deploy\tools\nssm.exe" status NMSAdminServer
# SERVICE_RUNNING
```

Or using the built-in `sc`:

```powershell
sc query NMSAdminServer
# STATE : 4 RUNNING
```

### 4. HTTP response from the server

```powershell
Invoke-WebRequest -Uri "http://localhost:5001/health" -UseBasicParsing
# StatusCode : 200
```

From another machine on the LAN:

```
http://172.16.2.103:5001
```

### 5. Check the startup log

```powershell
Get-Content "C:\ProgramData\nms-server\stdout.log" -Tail 30
```

Expected lines:

```
[DB] Database connection OK
[OK] Monitoring scheduler started.
[OK] Interface poller started.
[NMS] Admin server running on http://0.0.0.0:5001
[NMS] Accessible at: http://172.16.2.103:5001
```

---

## 12. Managing services after install

### NMS Server (Windows NSSM)

```powershell
$NSSM = "D:\device_monitoring_tactical\deploy\tools\nssm.exe"

& $NSSM start   NMSAdminServer
& $NSSM stop    NMSAdminServer
& $NSSM restart NMSAdminServer
& $NSSM status  NMSAdminServer
& $NSSM edit    NMSAdminServer    # GUI settings editor
```

### TimescaleDB (via WSL2)

```powershell
# Start / stop container
wsl -d Ubuntu-22.04 -- docker start monitoring_timescaledb
wsl -d Ubuntu-22.04 -- docker stop  monitoring_timescaledb

# View last 50 log lines
wsl -d Ubuntu-22.04 -- docker logs monitoring_timescaledb --tail 50

# Follow logs live
wsl -d Ubuntu-22.04 -- docker logs monitoring_timescaledb -f
```

> **Always stop the NMS server before stopping the database.**
>
> ```powershell
> & $NSSM stop NMSAdminServer
> wsl -d Ubuntu-22.04 -- docker stop monitoring_timescaledb
> ```

### Connect to the database directly

```powershell
wsl -d Ubuntu-22.04 -- docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db
```

Useful psql commands:

```sql
\dt                        -- list all tables
SELECT count(*) FROM devices;
SELECT * FROM v_timescaledb_job_health;
\q
```

---

## 13. Database backup and restore

### Backup

```powershell
# Run pg_dump inside the container and copy the dump to Windows
wsl -d Ubuntu-22.04 -- docker exec monitoring_timescaledb `
  pg_dump -U monitoring_man -d monitoring_db -Fc -f /tmp/nms_backup.dump

$date = Get-Date -Format "yyyyMMdd"
wsl -d Ubuntu-22.04 -- docker cp `
  monitoring_timescaledb:/tmp/nms_backup.dump `
  "/mnt/d/nms_backups/nms_backup_$date.dump"
```

Create `D:\nms_backups\` first:

```powershell
New-Item -ItemType Directory -Force -Path D:\nms_backups
```

Schedule this as a daily Task Scheduler task pointing to a `.ps1` script containing
the above two lines.

### Restore

```powershell
$NSSM = "D:\device_monitoring_tactical\deploy\tools\nssm.exe"

# Stop the server
& $NSSM stop NMSAdminServer

# Drop and recreate the database
wsl -d Ubuntu-22.04 -- docker exec monitoring_timescaledb `
  psql -U monitoring_man -d postgres -c "DROP DATABASE monitoring_db;"
wsl -d Ubuntu-22.04 -- docker exec monitoring_timescaledb `
  psql -U monitoring_man -d postgres -c "CREATE DATABASE monitoring_db;"

# Copy dump into container and restore
wsl -d Ubuntu-22.04 -- docker cp `
  /mnt/d/nms_backups/nms_backup_YYYYMMDD.dump `
  monitoring_timescaledb:/tmp/restore.dump
wsl -d Ubuntu-22.04 -- docker exec monitoring_timescaledb `
  pg_restore -U monitoring_man -d monitoring_db /tmp/restore.dump

# Start the server
& $NSSM start NMSAdminServer
```

---

## 14. Updating the server

```powershell
$NSSM = "D:\device_monitoring_tactical\deploy\tools\nssm.exe"

& $NSSM stop NMSAdminServer
cd D:\device_monitoring_tactical

# Pull code changes
git pull

# Update Python dependencies
pip install -r requirements.txt

# Schema migrations run automatically on next startup
& $NSSM start NMSAdminServer
```

---

## 15. Troubleshooting

### `wsl --set-default-version 2` says "The requested operation requires elevation"

Open PowerShell as Administrator (right-click → Run as administrator).

### WSL2 install fails with "A required feature is not installed"

Hyper-V must be enabled. Check:

```powershell
Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V
```

If `State: Disabled`, enable it:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All
Restart-Computer
```

Hyper-V requires the server to have virtualisation extensions enabled in BIOS/UEFI.
On a VM (e.g., VMware, Hyper-V guest), enable nested virtualisation on the hypervisor.

### `docker: command not found` from PowerShell (works in WSL2)

Docker Engine runs inside WSL2 Ubuntu only. All `docker` commands must be run via:

```powershell
wsl -d Ubuntu-22.04 -- docker <command>
```

Or open the Ubuntu WSL2 terminal directly.

### `Test-NetConnection localhost 5433` shows `TcpTestSucceeded: False`

1. Confirm the container is running: `wsl -d Ubuntu-22.04 -- docker ps`
2. Confirm Docker is listening on 5433: `wsl -d Ubuntu-22.04 -- docker port monitoring_timescaledb`
3. Wait 30 seconds after container start for the health check to pass.
4. Check WSL2 networking: WSL2 uses a NAT bridge. On Server 2019, `localhost` forwarding
   should work automatically. If not, use the WSL2 IP instead:
   ```powershell
   # Get WSL2 IP
   wsl -d Ubuntu-22.04 -- hostname -I
   ```
   Then update `DATABASE_URL` in `.env`:
   ```env
   DATABASE_URL=postgresql+psycopg2://monitoring_man:admin123@172.XX.XX.XX:5433/monitoring_db
   ```

### NMSAdminServer service starts but stops after 15 seconds

Check stderr:

```powershell
Get-Content "C:\ProgramData\nms-server\stderr.log" -Tail 30
```

Common causes:

| Error text | Fix |
|-----------|-----|
| `could not connect to server` | TimescaleDB not running — run `wsl -d Ubuntu-22.04 -- docker start monitoring_timescaledb` |
| `FERNET_KEY` not set | Add `FERNET_KEY=...` to `.env` |
| `No module named 'waitress'` | `pip install -r requirements.txt` |
| `address already in use` port 5001 | `netstat -ano \| findstr :5001` — find and stop the other process |

### "Login page loads, login fails immediately"

`SECRET_KEY` in `.env` is likely still the placeholder value. Generate and set it:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
# Paste into .env as SECRET_KEY=...
& "D:\device_monitoring_tactical\deploy\tools\nssm.exe" restart NMSAdminServer
```

### Port 5001 not reachable from other machines but works on localhost

Firewall rule is missing. Re-run Step 9:

```powershell
New-NetFirewallRule -DisplayName "NMS Admin Server (port 5001)" `
  -Direction Inbound -Protocol TCP -LocalPort 5001 -Action Allow -Profile Domain,Private
```

### TimescaleDB container does not start after reboot

Check if the Task Scheduler task ran:

```powershell
Get-ScheduledTaskInfo -TaskName "Start-TimescaleDB" | Select-Object LastRunTime, LastTaskResult
# LastTaskResult: 0 = success, anything else = error
```

If it failed, start the container manually:

```powershell
wsl -d Ubuntu-22.04 -- bash -c "service docker start && cd /mnt/d/device_monitoring_tactical && docker compose -f docker-compose.timescaledb.yml up -d"
```

Then investigate the scheduled task error via Event Viewer:
`Windows Logs → System → filter by source "Task Scheduler"`.

---

## Quick reference card

```
WSL2 UBUNTU    wsl -d Ubuntu-22.04

DB START       wsl -d Ubuntu-22.04 -- docker start monitoring_timescaledb
DB STOP        wsl -d Ubuntu-22.04 -- docker stop  monitoring_timescaledb
DB LOGS        wsl -d Ubuntu-22.04 -- docker logs  monitoring_timescaledb --tail 50
DB CONNECT     wsl -d Ubuntu-22.04 -- docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db

APP START      nssm start  NMSAdminServer
APP STOP       nssm stop   NMSAdminServer
APP RESTART    nssm restart NMSAdminServer
APP LOGS       Get-Content C:\ProgramData\nms-server\stdout.log -Tail 50 -Wait

ENV FILE       D:\device_monitoring_tactical\.env
FIREWALL       New-NetFirewallRule -DisplayName "NMS port 5001" -Direction Inbound -Protocol TCP -LocalPort 5001 -Action Allow -Profile Domain,Private

BACKUP DB      wsl -d Ubuntu-22.04 -- docker exec monitoring_timescaledb pg_dump -U monitoring_man -d monitoring_db -Fc -f /tmp/backup.dump
               wsl -d Ubuntu-22.04 -- docker cp monitoring_timescaledb:/tmp/backup.dump /mnt/d/nms_backups/

DASHBOARD      http://172.16.2.103:5001
```
