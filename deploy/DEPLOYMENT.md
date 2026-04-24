# NMS Deployment Guide

Three components — one build command, three installers.

| Component | Source | Installs on |
|-----------|--------|------------|
| **NMSAdminServer** | `web_main.py` | The server machine (one per organisation) |
| **NMSCoreAgent** | `server_agent.py` | Every monitored host (metrics collector) |
| **NMSTrackingAgent** | `service.py` | Managed workstations (full employee monitoring) |

---

## Admin Server — Docker (recommended)

The admin server runs as a Docker container. No Python installation needed on the server.

```bat
deploy\docker_deploy.bat
```

That's it. The script builds the image, starts PostgreSQL + app, waits for `/health`, then prints the dashboard URL.

**Requirements:** Docker Desktop installed and running.

**First run:** The script opens `.env` in Notepad if it doesn't exist. Set at minimum:
- `SECRET_KEY` — any long random string
- `TRACKING_API_KEY` — copy this into each agent's config

**Day-to-day commands:**
```bat
docker compose logs -f app        :: live logs
docker compose restart app        :: restart after config change
docker compose down               :: stop everything
docker compose pull && docker compose up -d  :: update to new image
```

---

## Agents — Windows EXEs

Agents (`NMSCoreAgent`, `NMSTrackingAgent`) must run natively on Windows because they access host hardware (CPU/RAM metrics, keyboard, camera). Build them with:

```bat
venv\Scripts\activate
pyinstaller build_agent.spec --noconfirm
pyinstaller build_tracking.spec --noconfirm
```

Then install on each target machine with:

```bat
deploy\install_services.bat
```

---

## Prerequisites (build machine only, for agents only)

| Tool | Where to get it |
|------|----------------|
| Python 3.10+ | python.org |
| PyInstaller | `pip install pyinstaller` |
| Inno Setup 6 | Already installed at `C:\Users\APL TECHNO\AppData\Local\Programs\Inno Setup 6` |
| All requirements | `pip install -r requirements.txt` |

Target machines need **no Python** — the EXEs are fully self-contained.

---

## Step 1 — Build everything

Run from the **project root** (the folder that contains `web_main.py`):

```bat
deploy\build_all.bat
```

What it does, in order:

1. `pyinstaller build_admin.spec` → `dist\NMSAdminServer\`
2. `pyinstaller build_tracking.spec` → `dist\NMSTrackingAgent\`
3. `pyinstaller build_agent.spec` → `dist\NMSCoreAgent\`
4. Compiles all three Inno Setup scripts → `installer\Output\*.exe`

If Inno Setup is not found it prints a warning and skips step 4 (EXEs are still usable).

Build time is typically 5–10 minutes (Tracking Agent is slowest — bundles cv2/numpy).

---

## Step 2 — Install services

**Run on the target machine as Administrator.**

Copy the relevant `dist\NMS*\` folder(s) to the target machine, then run:

```bat
deploy\install_services.bat
```

The script will:
1. Ask which component(s) to install (Admin Server / Core Agent / Tracking Agent / all)
2. Copy files to `C:\NMS\<component>\`
3. Open the config GUI for agents (set server URL + token)
4. Register the Windows service via NSSM (auto-start, crash-restart, admin-only stop)
5. Start the service immediately

To reconfigure after install: run the EXE with `--configure`, then `nssm restart <ServiceName>`.

---

## Admin Server — first run details

**Run on the server machine as Administrator.**

After `install_services.bat` installs the Admin Server:

   | Wizard page | What to fill in |
   |-------------|----------------|
   | Database | Choose **Docker** (auto-starts postgres:15 container) or enter an existing PostgreSQL connection |
   | Web Port | `5001` (default — open this port in firewall) |
   | Secret Key | Auto-generated — copy and keep safe |
   | Tracking API Key | Auto-generated — **copy this**, you need it for each agent install |

3. Installer will:
   - Copy files to `C:\Program Files\NMS\NMSAdminServer\`
   - Write `.env` with your DB + secret values
   - Register `NMSAdminServer` Windows service (auto-start, crash-restart, admin-only stop)
   - Start the service
   - Open the dashboard at `http://localhost:5001`

Default login: `admin` / `admin123` — **change immediately** in the dashboard.

### After install

| Action | How |
|--------|-----|
| Open dashboard | `http://<server-ip>:5001` |
| View logs | `C:\ProgramData\NMS\AdminServer\stdout.log` |
| Restart service | `nssm restart NMSAdminServer` (as admin) |
| Stop service | `nssm stop NMSAdminServer` (as admin) |
| Reconfigure `.env` | Edit `C:\Program Files\NMS\NMSAdminServer\.env` then restart |

### Docker path — what runs

```
docker compose -f C:\Program Files\NMS\NMSAdminServer\docker-compose.yml up -d
```

PostgreSQL data lives in the Docker-managed volume `pgdata`. It survives container restarts and reboots.

### No Docker path

Supply PostgreSQL credentials in the wizard. The `DATABASE_URL` is written to `.env` in `psycopg2` format:
```
postgresql+psycopg2://user:password@host:port/dbname
```

If you want to provision the PostgreSQL role/database from the current `.env` before the service starts, run:
```bat
deploy\setup_postgres_from_env.bat
```

Optional dry run:
```bat
deploy\setup_postgres_from_env.bat -DryRun
```

Optional TimescaleDB enablement:
```bat
deploy\setup_postgres_from_env.bat -EnableTimescale
```

For the simple `web_main.exe` deployment flow, start from:
[web_main.env.template](D:/device_monitoring_tactical/deploy/config.templates/web_main.env.template)

---

## Step 3 — Deploy Core Agents

**Run on each monitored host as Administrator.**

1. Copy `installer\Output\NMSCoreAgent_Setup.exe` to the target machine.
2. Double-click and follow the wizard:

   | Field | Example |
   |-------|---------|
   | Admin Server URL | `http://192.168.1.100:5001/api/agent/metrics` |
   | Agent Token | Paste the Tracking API Key from the admin server wizard (Step 2) |
   | Poll Interval | `30` seconds (default) |

3. Installer writes `C:\ProgramData\nms-agent\config.json` and registers `NMSCoreAgent` as a Windows service.

### Changing the server IP later

```bat
NMSCoreAgent.exe --configure
```

Or edit `C:\ProgramData\nms-agent\config.json` directly and restart the service:

```bat
nssm restart NMSCoreAgent
```

| Action | Command (run as admin) |
|--------|----------------------|
| View logs | `C:\ProgramData\nms-agent\stdout.log` |
| Status | `sc query NMSCoreAgent` |
| Restart | `nssm restart NMSCoreAgent` |
| Reconfigure | `NMSCoreAgent.exe --configure` |

---

## Step 4 — Deploy Tracking Agents

**Run on each managed workstation as Administrator.**

1. Copy `installer\Output\NMSTrackingAgent_Setup.exe` to the target machine.
2. Double-click and follow the wizard:

   | Field | Example |
   |-------|---------|
   | Admin Server URL | `http://192.168.1.100:5001` |
   | Tracking API Key | Same key as above |
   | Agent Port | `5002` (default — must be reachable from server) |
   | Features | Enable/disable keystroke and camera monitoring |

3. Installer writes `C:\ProgramData\NMS\TrackingAgent\config.env` and registers `NMSTrackingAgent` as a Windows service.

### Changing the server IP later

```bat
NMSTrackingAgent.exe --configure
```

Or edit `C:\ProgramData\NMS\TrackingAgent\config.env` and restart:

```bat
nssm restart NMSTrackingAgent
```

| Action | Command (run as admin) |
|--------|----------------------|
| View logs | `C:\ProgramData\NMS\TrackingAgent\stdout.log` |
| Status | `sc query NMSTrackingAgent` |
| Restart | `nssm restart NMSTrackingAgent` |
| Reconfigure | `NMSTrackingAgent.exe --configure` |

---

## Service hardening

All three services are registered with an SDDL that:
- Allows `SYSTEM` and `Administrators` to start/stop/configure
- Gives standard users **read-only** access (they can query status but cannot stop the service)

A standard user running `sc stop NMSCoreAgent` or trying to kill it in Task Manager will get **Access Denied**.

If an admin explicitly wants to stop a service:
```bat
nssm stop NMSCoreAgent      # or NMSAdminServer / NMSTrackingAgent
```

---

## Windows Defender false positives

PyInstaller executables sometimes trigger Defender. Add exclusions on each target machine:

```bat
powershell -Command "Add-MpPreference -ExclusionPath 'C:\Program Files\NMS'"
powershell -Command "Add-MpPreference -ExclusionPath 'C:\ProgramData\nms-agent'"
powershell -Command "Add-MpPreference -ExclusionPath 'C:\ProgramData\NMS'"
```

---

## Uninstalling

Use Windows **Add or Remove Programs** for each component, or from the install folder:

```bat
; Admin Server
"C:\Program Files\NMS\NMSAdminServer\nssm.exe" stop NMSAdminServer
"C:\Program Files\NMS\NMSAdminServer\nssm.exe" remove NMSAdminServer confirm

; Core Agent
"C:\Program Files\NMS\NMSCoreAgent\nssm.exe" stop NMSCoreAgent
"C:\Program Files\NMS\NMSCoreAgent\nssm.exe" remove NMSCoreAgent confirm

; Tracking Agent
"C:\Program Files\NMS\NMSTrackingAgent\nssm.exe" stop NMSTrackingAgent
"C:\Program Files\NMS\NMSTrackingAgent\nssm.exe" remove NMSTrackingAgent confirm
```

Data directories (`C:\ProgramData\nms-agent\`, `C:\ProgramData\NMS\`) are left intact — delete manually if needed.

---

## Troubleshooting

### Service won't start

```bat
nssm status NMSCoreAgent
type "C:\ProgramData\nms-agent\stderr.log"
```

Common causes:
- Missing `config.json` → run `NMSCoreAgent.exe --configure`
- Bad `DATABASE_URL` in `.env` → fix and `nssm restart NMSAdminServer`
- Port already in use → check `netstat -ano | findstr :5001`

### Agent not appearing in dashboard

1. Check `C:\ProgramData\nms-agent\stdout.log` for auth errors (HTTP 401)
2. Confirm the agent token matches: admin panel → Settings → API Keys
3. Confirm server URL is reachable: `curl http://<server-ip>:5001/health`

### Reconfiguring server IP without reinstalling

All three components support a `--configure` flag that opens the GUI and saves new settings without reinstalling:

```bat
NMSAdminServer.exe   --configure    ; sets WEB_PORT, SECRET_KEY (rarely needed)
NMSCoreAgent.exe     --configure    ; sets server URL, token, interval
NMSTrackingAgent.exe --configure    ; sets server URL, API key, features
```

After saving, restart the relevant service.

---

## File layout (post-install)

```
C:\Program Files\NMS\
├── NMSAdminServer\          ← web_main.py + Flask app
│   ├── NMSAdminServer.exe
│   ├── .env                 ← written by installer wizard
│   ├── templates\
│   ├── static\
│   └── instance\            ← SQLite (if not using PostgreSQL)
├── NMSCoreAgent\
│   └── NMSCoreAgent.exe
└── NMSTrackingAgent\
    └── NMSTrackingAgent.exe

C:\ProgramData\
├── NMS\
│   ├── AdminServer\
│   │   ├── stdout.log
│   │   └── stderr.log
│   └── TrackingAgent\
│       ├── config.env       ← written by installer wizard
│       └── stdout.log
└── nms-agent\
    ├── config.json          ← written by installer wizard
    ├── metrics_buffer.db
    └── stdout.log
```
