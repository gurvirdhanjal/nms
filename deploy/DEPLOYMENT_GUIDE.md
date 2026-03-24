# NMS Agent Deployment Guide

Deploy the NMS Core Agent and NMS Tracking Agent as persistent Windows services using NSSM
(Non-Sucking Service Manager). Both agents auto-restart on crash, log to rotating files, and
load their server URL from a config file — no rebuilding required when the server IP changes.

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [Step 1 — Get NSSM](#2-step-1--get-nssm)
3. [Step 2 — Build the agents](#3-step-2--build-the-agents)
4. [Step 3 — Deploy the Core Agent](#4-step-3--deploy-the-core-agent)
5. [Step 4 — Deploy the Tracking Agent](#5-step-4--deploy-the-tracking-agent)
6. [Verifying services](#6-verifying-services)
7. [Managing services after install](#7-managing-services-after-install)
8. [Changing the server URL post-install](#8-changing-the-server-url-post-install)
9. [Log files](#9-log-files)
10. [Windows Defender false positives](#10-windows-defender-false-positives)
11. [Uninstalling](#11-uninstalling)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Prerequisites

| Requirement | Notes |
|-------------|-------|
| Windows 10 / 11 or Windows Server 2016+ | Both agents are Windows-only |
| Administrator privileges | Required for service installation |
| Python 3.10+ with PyInstaller | Only needed on the **build machine** |
| NMS server running and reachable | The `--test` step verifies this before install |

> **Build machine vs target machine.**
> Build the EXE on your dev machine, then copy the `dist\` folder to each target machine.
> Python does NOT need to be installed on target machines.

---

## 2. Step 1 — Get NSSM

NSSM wraps any EXE as a proper Windows service with restart-on-crash, log rotation, and
`sc`-compatible service controls.

1. Go to **https://nssm.cc/download**
2. Download the latest release (e.g. `nssm-2.24.zip`)
3. Extract the zip
4. Copy `nssm-2.24\win64\nssm.exe` into:

```
deploy\tools\nssm.exe
```

> **Why win64?** Both agents target 64-bit Windows. Always use the `win64` build of NSSM.
> The `win32` build will fail silently with 64-bit EXEs.

Verify NSSM is in place:

```cmd
deploy\tools\nssm.exe version
```

Expected output:

```
NSSM version 2.24 (build xxxx), ...
```

---

## 3. Step 2 — Build the agents

Run from the **project root** (not inside `deploy\`):

```cmd
scripts\build_agents.bat
```

This will:
- Clean `dist\NMSCoreAgent\` and `dist\NMSTrackingAgent\`
- Run PyInstaller with `server_agent.spec` → `dist\NMSCoreAgent\`
- Run PyInstaller with `WorkstationAgent.spec` → `dist\NMSTrackingAgent\`
- Copy config templates into each dist folder as `config.json.template`

After a successful build, confirm the output is **onedir** (not a single bloated EXE):

```cmd
dir dist\NMSCoreAgent\
```

You should see:

```
NMSCoreAgent.exe
_internal\         <-- DLLs and bundled modules live here
config.json.template
```

> If you only see `NMSCoreAgent.exe` with a file size of 10–50 MB, the spec is wrong.
> A correct onedir build has `NMSCoreAgent.exe` around 1–3 MB and an `_internal\` folder.

---

## 4. Step 3 — Deploy the Core Agent

The Core Agent (`NMSCoreAgent`) collects CPU, RAM, disk, network, and process metrics and
POSTs them to the NMS server. It is safe to deploy on any machine — no AV concerns.

### 4.1 Copy the build to the target machine

On the target machine, create the install directory and copy the build:

```cmd
xcopy /E /I /Y dist\NMSCoreAgent "C:\Program Files\NMS\NMSCoreAgent"
```

Or use the automated script (from the project root, run as Administrator):

```cmd
deploy\install_core_agent.bat
```

> The script handles copying, config creation, test-mode verification, and NSSM registration
> in one pass. Follow along with the manual steps below to understand what it does.

### 4.2 Create the config directory

```cmd
mkdir "C:\ProgramData\nms-agent"
```

### 4.3 Create config.json

Copy the template:

```cmd
copy deploy\config.templates\nms-agent-config.json "C:\ProgramData\nms-agent\config.json"
```

Open `C:\ProgramData\nms-agent\config.json` in Notepad and set your server URL:

```json
{
  "nms_server_url": "http://192.168.1.50:5001/api/agent/metrics",
  "agent_token": "",
  "interval_seconds": 30,
  "request_timeout": 5,
  "top_processes_limit": 5,
  "buffer_max_records": 1000
}
```

Replace `192.168.1.50` with the actual IP of your NMS server. Port `5001` is the default
production port. Do not add a trailing slash.

> **agent_token** — Leave empty on first run. The server will issue a token automatically
> on first contact and the agent will write it back to `config.json`.

### 4.4 Run test mode — verify config and connectivity

```cmd
"C:\Program Files\NMS\NMSCoreAgent\NMSCoreAgent.exe" --test
```

Expected log output (written to `C:\ProgramData\nms-agent\agent.log`):

```
=== NMS Core Agent Starting ===
Config source: file (C:\ProgramData\nms-agent\config.json)
Server URL: http://192.168.1.50:5001/api/agent/metrics
Interval: 30s | Timeout: 5s | Buffer max: 1000
[test mode] Collecting metrics once and exiting...
[test mode] Collected. Sending...
[test mode] OK — agent can reach server. Exiting.
```

If you see a `ValueError` or connection error, fix `config.json` before proceeding.

### 4.5 Register as a Windows service with NSSM

Run each command from an **Administrator** command prompt:

```cmd
set INSTALL_DIR=C:\Program Files\NMS\NMSCoreAgent
set CONFIG_DIR=C:\ProgramData\nms-agent
set NSSM=deploy\tools\nssm.exe

:: Register the service
%NSSM% install NMSCoreAgent "%INSTALL_DIR%\NMSCoreAgent.exe"

:: Set working directory
%NSSM% set NMSCoreAgent AppDirectory "%INSTALL_DIR%"

:: Human-readable display name and description
%NSSM% set NMSCoreAgent DisplayName "NMS Core Monitoring Agent"
%NSSM% set NMSCoreAgent Description "Sends server health metrics to the NMS Dashboard"

:: Start automatically at boot
%NSSM% set NMSCoreAgent Start SERVICE_AUTO_START

:: Log stdout and stderr to rotating files
%NSSM% set NMSCoreAgent AppStdout "%CONFIG_DIR%\stdout.log"
%NSSM% set NMSCoreAgent AppStderr "%CONFIG_DIR%\stderr.log"
%NSSM% set NMSCoreAgent AppRotateFiles 1
%NSSM% set NMSCoreAgent AppRotateBytes 5242880

:: Restart automatically on any exit, with a 5-second delay
%NSSM% set NMSCoreAgent AppExit Default Restart
%NSSM% set NMSCoreAgent AppRestartDelay 5000

:: Start the service now
%NSSM% start NMSCoreAgent
```

---

## 5. Step 4 — Deploy the Tracking Agent

The Tracking Agent (`NMSTrackingAgent`) runs a local Flask server (port 5002) and provides
workstation telemetry: screen capture, process tracking, keyboard/mouse activity, and
application usage. **Expect 2–5 AV detections** even after correct packaging — see
[Section 10](#10-windows-defender-false-positives).

### 5.1 Copy the build

```cmd
xcopy /E /I /Y dist\NMSTrackingAgent "C:\Program Files\NMS\NMSTrackingAgent"
```

Or use the automated script:

```cmd
deploy\install_tracking_agent.bat
```

### 5.2 Create config.json

```cmd
mkdir "C:\ProgramData\nms-tracking-agent"
copy deploy\config.templates\nms-tracking-config.json "C:\ProgramData\nms-tracking-agent\config.json"
```

Edit `C:\ProgramData\nms-tracking-agent\config.json`:

```json
{
  "server_url": "http://192.168.1.50:5001",
  "tracking_api_key": "",
  "admin_api_key": "",
  "agent_port": 5002,
  "preferred_subnet_prefix": "192.168.1."
}
```

| Field | Description |
|-------|-------------|
| `server_url` | Base URL of your NMS server — **no trailing slash, no path** |
| `tracking_api_key` | Leave empty; populated automatically on first sync |
| `admin_api_key` | Optional secondary key for admin endpoints |
| `agent_port` | Port the tracking agent listens on (default: 5002) |
| `preferred_subnet_prefix` | The agent will prefer IPs starting with this prefix when reporting its own IP. Set this to your LAN subnet prefix. |

### 5.3 Run test mode

```cmd
"C:\Program Files\NMS\NMSTrackingAgent\NMSTrackingAgent.exe" --test
```

Expected log output (written to `service.log` in the install dir):

```
=== NMS Tracking Agent Starting ===
Config source: file
Server URL: http://192.168.1.50:5001
Agent Port: 5002
[test mode] Verifying server reachability at http://192.168.1.50:5001 ...
[test mode] Server responded: 200
```

Exit code 0 = server is reachable. Exit code 1 = fix the URL before installing the service.

### 5.4 Register as a Windows service with NSSM

```cmd
set INSTALL_DIR=C:\Program Files\NMS\NMSTrackingAgent
set CONFIG_DIR=C:\ProgramData\nms-tracking-agent
set NSSM=deploy\tools\nssm.exe

%NSSM% install NMSTrackingAgent "%INSTALL_DIR%\NMSTrackingAgent.exe"
%NSSM% set NMSTrackingAgent AppDirectory "%INSTALL_DIR%"
%NSSM% set NMSTrackingAgent DisplayName "NMS Workstation Tracking Agent"
%NSSM% set NMSTrackingAgent Description "Sends workstation telemetry to the NMS Dashboard"
%NSSM% set NMSTrackingAgent Start SERVICE_AUTO_START
%NSSM% set NMSTrackingAgent AppStdout "%CONFIG_DIR%\stdout.log"
%NSSM% set NMSTrackingAgent AppStderr "%CONFIG_DIR%\stderr.log"
%NSSM% set NMSTrackingAgent AppRotateFiles 1
%NSSM% set NMSTrackingAgent AppRotateBytes 5242880
%NSSM% set NMSTrackingAgent AppExit Default Restart
%NSSM% set NMSTrackingAgent AppRestartDelay 5000
%NSSM% start NMSTrackingAgent
```

---

## 6. Verifying services

### Check service status

```cmd
:: Using NSSM
deploy\tools\nssm.exe status NMSCoreAgent
deploy\tools\nssm.exe status NMSTrackingAgent

:: Using sc (built-in Windows tool)
sc query NMSCoreAgent
sc query NMSTrackingAgent
```

Expected output for a running service:

```
SERVICE_NAME: NMSCoreAgent
        TYPE               : 10  WIN32_OWN_PROCESS
        STATE              : 4  RUNNING
        ...
```

### Confirm NSSM restart policy is set

```cmd
deploy\tools\nssm.exe dump NMSCoreAgent
```

Look for these two lines in the output:

```
nssm set NMSCoreAgent AppExit Default Restart
nssm set NMSCoreAgent AppRestartDelay 5000
```

If they are missing, re-run the `AppExit` and `AppRestartDelay` set commands from Step 4.5.

### Check logs for a clean startup

```cmd
type "C:\ProgramData\nms-agent\stdout.log"
```

You should see the `=== NMS Core Agent Starting ===` block with no errors.

---

## 7. Managing services after install

All commands require an **Administrator** command prompt.

| Action | Command |
|--------|---------|
| Start | `nssm start NMSCoreAgent` |
| Stop | `nssm stop NMSCoreAgent` |
| Restart | `nssm restart NMSCoreAgent` |
| Pause | `nssm pause NMSCoreAgent` |
| View status | `nssm status NMSCoreAgent` |
| Edit settings (GUI) | `nssm edit NMSCoreAgent` |
| View all settings | `nssm dump NMSCoreAgent` |

NSSM services also appear in `services.msc` (Windows Services GUI) under their display name.
You can start/stop/restart from there as well, but use NSSM for configuration changes.

---

## 8. Changing the server URL post-install

You do **not** need to rebuild the EXE or touch the service registration. Just edit the
config file and restart the service.

**Core Agent:**

```cmd
notepad "C:\ProgramData\nms-agent\config.json"
:: Edit nms_server_url
nssm restart NMSCoreAgent
```

**Tracking Agent:**

```cmd
notepad "C:\ProgramData\nms-tracking-agent\config.json"
:: Edit server_url
nssm restart NMSTrackingAgent
```

Run test mode first if you want to verify connectivity before restarting the live service:

```cmd
nssm stop NMSCoreAgent
"C:\Program Files\NMS\NMSCoreAgent\NMSCoreAgent.exe" --test
nssm start NMSCoreAgent
```

---

## 9. Log files

| Agent | Log location | Written by |
|-------|-------------|------------|
| Core Agent — application log | `C:\ProgramData\nms-agent\agent.log` | Agent itself (Python RotatingFileHandler) |
| Core Agent — service stdout | `C:\ProgramData\nms-agent\stdout.log` | NSSM |
| Core Agent — service stderr | `C:\ProgramData\nms-agent\stderr.log` | NSSM |
| Tracking Agent — application log | `C:\Program Files\NMS\NMSTrackingAgent\service.log` | Agent itself |
| Tracking Agent — service stdout | `C:\ProgramData\nms-tracking-agent\stdout.log` | NSSM |
| Tracking Agent — service stderr | `C:\ProgramData\nms-tracking-agent\stderr.log` | NSSM |

Log rotation is handled at two levels:

- **NSSM** rotates `stdout.log` / `stderr.log` at 5 MB (`AppRotateBytes 5242880`)
- **Python** rotates `agent.log` at 10 MB, keeping 5 backups

If you need to tail a log file in real-time on Windows:

```cmd
powershell -Command "Get-Content 'C:\ProgramData\nms-agent\agent.log' -Wait -Tail 50"
```

---

## 10. Windows Defender false positives

### Core Agent — expected 0 detections

After building with `upx=False` and onedir mode, `NMSCoreAgent.exe` should pass Defender
cleanly. If it is quarantined, check:

- The build used `server_agent.spec` (not an old spec with `upx=True`)
- The dist folder is `dist\NMSCoreAgent\` (onedir), not a single large EXE

### Tracking Agent — expect 2–5 detections

The tracking agent permanently triggers some AV engines regardless of packaging because it
uses APIs that are indistinguishable from malware:

| Component | Why AV flags it |
|-----------|----------------|
| `pynput` | Keyboard and mouse hooks — identical to keylogger behavior |
| `PIL.ImageGrab` + `cv2` | Screen capture — identical to RAT behavior |
| `wmi` | System inventory queries — identical to spyware behavior |
| `console=False` | Windowless background process — common dropper pattern |

These are **false positives**. The agent is authorized internal monitoring software.
If Defender quarantines `NMSTrackingAgent.exe`, run:

```cmd
deploy\optional_defender_exclusion.bat
```

This adds path and process exclusions for both agents:

```
C:\Program Files\NMS\NMSCoreAgent\  (path)
NMSCoreAgent.exe                    (process)
C:\Program Files\NMS\NMSTrackingAgent\  (path)
NMSTrackingAgent.exe                    (process)
```

> **Run `optional_defender_exclusion.bat` only if actually blocked.**
> Do not add exclusions pre-emptively. Confirm the EXE is flagged before adding an exclusion.

### Submitting a false positive report

If your organization uses a third-party AV (not Defender), contact the vendor's false-positive
portal. Provide:
- The EXE file
- A description: "Internal IT monitoring agent, collects workstation telemetry for on-premise NMS"
- Your organization name and contact

Most enterprise AV vendors process false positive reports within 1–5 business days.

---

## 11. Uninstalling

### Remove both services

```cmd
deploy\uninstall.bat
```

This stops and removes both NSSM service registrations. Install directories and config files
are left in place (safe to delete manually after confirming).

### Manual removal

```cmd
nssm stop NMSCoreAgent
nssm remove NMSCoreAgent confirm

nssm stop NMSTrackingAgent
nssm remove NMSTrackingAgent confirm
```

### Clean up files (optional)

```cmd
rmdir /s /q "C:\Program Files\NMS\"
rmdir /s /q "C:\ProgramData\nms-agent\"
rmdir /s /q "C:\ProgramData\nms-tracking-agent\"
```

---

## 12. Troubleshooting

### Service installed but immediately stops

1. Check stderr log for a Python traceback:
   ```cmd
   type "C:\ProgramData\nms-agent\stderr.log"
   ```
2. The most common cause is an invalid `config.json`. Run test mode manually:
   ```cmd
   "C:\Program Files\NMS\NMSCoreAgent\NMSCoreAgent.exe" --test
   ```
3. If config is valid, check that the NMS server is reachable from this machine:
   ```cmd
   curl http://192.168.1.50:5001/health
   ```

### Service shows "RUNNING" but NMS dashboard shows device offline

- The agent is sending metrics but may be using the wrong local IP.
- Check `agent.log` for the `Device UUID` and `Server URL` lines.
- If the server URL is wrong, edit `config.json` and restart the service.
- If the IP being reported is wrong, set `preferred_subnet_prefix` in `config.json`
  (tracking agent) to match your LAN (e.g. `"192.168.1."`).

### `nssm install` fails with "Access denied"

The command prompt must be running as Administrator. Right-click → "Run as administrator".

### `nssm start NMSCoreAgent` returns error 1053

Error 1053 means the service did not respond to the start request in time. This usually means
the EXE crashed at startup. Check `stderr.log` immediately after:

```cmd
type "C:\ProgramData\nms-agent\stderr.log"
```

### Port 5002 already in use (Tracking Agent)

Change the port in `C:\ProgramData\nms-tracking-agent\config.json`:

```json
{ "agent_port": 5003 }
```

Then restart the service and update the port in the NMS server's device config for this machine.

### How to check which port the Tracking Agent is listening on

```cmd
netstat -ano | findstr :5002
```

### NSSM GUI editor

For a visual overview of all service settings, run:

```cmd
nssm edit NMSCoreAgent
```

This opens a GUI where you can view and edit all settings including the executable path,
arguments, environment variables, log files, and restart policy.

---

## Quick reference card

```
BUILD         scripts\build_agents.bat

INSTALL       deploy\install_core_agent.bat        (Core Agent)
              deploy\install_tracking_agent.bat    (Tracking Agent)

TEST          NMSCoreAgent.exe --test
              NMSTrackingAgent.exe --test

START/STOP    nssm start NMSCoreAgent
              nssm stop  NMSCoreAgent

LOGS          C:\ProgramData\nms-agent\agent.log
              C:\ProgramData\nms-tracking-agent\stdout.log

CONFIG        C:\ProgramData\nms-agent\config.json
              C:\ProgramData\nms-tracking-agent\config.json

AV FIX        deploy\optional_defender_exclusion.bat  (run only if blocked)

UNINSTALL     deploy\uninstall.bat
```
