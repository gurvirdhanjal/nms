import psutil
import platform
import socket
import time
import json
import requests
import os
from datetime import datetime

# ==========================
# CONFIGURATION
# ==========================

NMS_SERVER_URL = "http://127.0.0.1:5001/api/agent/metrics"
AGENT_TOKEN = "8f42v73054r1749f8g58848be5e6502c" # Updated to match config.py default
INTERVAL_SECONDS = 30
REQUEST_TIMEOUT = 5

# ==========================
# HELPERS
# ==========================

def get_hostname():
    return socket.gethostname()

def get_os_info():
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "architecture": platform.machine()
    }

def get_uptime_seconds():
    try:
        return int(time.time() - psutil.boot_time())
    except:
        return 0

def get_cpu_metrics():
    return {
        "cpu_percent": psutil.cpu_percent(interval=1),
        "cpu_cores": psutil.cpu_count(logical=True)
    }

def get_memory_metrics():
    mem = psutil.virtual_memory()
    return {
        "total_mb": mem.total // (1024 * 1024),
        "used_mb": mem.used // (1024 * 1024),
        "percent": mem.percent
    }

def get_disk_metrics():
    try:
        disk = psutil.disk_usage("/")
        return {
            "total_gb": round(disk.total / (1024**3), 2),
            "used_gb": round(disk.used / (1024**3), 2),
            "percent": disk.percent
        }
    except:
        return {"total_gb": 0, "used_gb": 0, "percent": 0}

def get_network_metrics():
    try:
        net = psutil.net_io_counters()
        return {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv
        }
    except:
        return {"bytes_sent": 0, "bytes_recv": 0}

# ==========================
# PAYLOAD BUILDER
# ==========================

def collect_metrics():
    return {
        "agent_type": "core",
        "hostname": get_hostname(),
        "timestamp": datetime.now().isoformat(), # Use local time equivalent or utcnow
        "uptime_seconds": get_uptime_seconds(),
        "os_info": get_os_info(),
        "cpu": get_cpu_metrics(),
        "memory": get_memory_metrics(),
        "disk": get_disk_metrics(),
        "network": get_network_metrics()
    }

# ==========================
# SENDER
# ==========================

def send_metrics(payload):
    headers = {
        "Authorization": f"Bearer {AGENT_TOKEN}",
        "Content-Type": "application/json"
    }

    response = requests.post(
        NMS_SERVER_URL,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

# ==========================
# MAIN LOOP
# ==========================

def main():
    print(f"🟢 NMS Core Agent started on {get_hostname()}")
    print(f"Target: {NMS_SERVER_URL}")

    while True:
        try:
            metrics = collect_metrics()
            send_metrics(metrics)
            print(f"✔ Metrics sent successfully at {datetime.now().strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"⚠ Error sending metrics: {e}")

        time.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
