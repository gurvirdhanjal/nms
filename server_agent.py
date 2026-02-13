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
AGENT_TOKEN = "8f42v73054r1749f8g58848be5e6502c"  # Updated to match config.py default
INTERVAL_SECONDS = 30
REQUEST_TIMEOUT = 5
TOP_PROCESSES_LIMIT = 5

# ==========================
# HELPERS
# ==========================

def get_hostname():
    return socket.gethostname()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        return ip
    except Exception:
        return None

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

# ==========================
# CPU METRICS
# ==========================

def get_cpu_metrics():
    return {
        "cpu_percent": psutil.cpu_percent(interval=1),
        "cpu_cores": psutil.cpu_count(logical=True),
        "cpu_cores_physical": psutil.cpu_count(logical=False)
    }

def get_load_average():
    """Get system load average for 1, 5, and 15 minutes"""
    try:
        load1, load5, load15 = os.getloadavg()
        return {
            "1min": round(load1, 2),
            "5min": round(load5, 2),
            "15min": round(load15, 2)
        }
    except:
        return {"1min": 0, "5min": 0, "15min": 0}

# ==========================
# MEMORY METRICS
# ==========================

def get_memory_detailed():
    """Get detailed memory and swap information"""
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "total_mb": mem.total // (1024 * 1024),
        "used_mb": mem.used // (1024 * 1024),
        "available_mb": mem.available // (1024 * 1024),
        "percent": mem.percent,
        "used_gb": round(mem.used / (1024**3), 2),
        "total_gb": round(mem.total / (1024**3), 2),
        "swap_total_mb": swap.total // (1024 * 1024),
        "swap_used_mb": swap.used // (1024 * 1024),
        "swap_percent": swap.percent
    }

# ==========================
# DISK METRICS
# ==========================

def get_disk_metrics():
    """Get disk space usage"""
    try:
        if platform.system() == "Windows":
            system_drive = os.environ.get("SystemDrive", "C:")
            disk = psutil.disk_usage(f"{system_drive}\\")
        else:
            disk = psutil.disk_usage("/")
        return {
            "total_gb": round(disk.total / (1024**3), 2),
            "used_gb": round(disk.used / (1024**3), 2),
            "free_gb": round(disk.free / (1024**3), 2),
            "percent": disk.percent
        }
    except:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0}

def get_disk_inodes():
    """Get inode usage on root filesystem"""
    try:
        stat = os.statvfs("/")
        inodes_total = stat.f_files
        inodes_free = stat.f_ffree
        inodes_used = inodes_total - inodes_free
        return {
            "total": inodes_total,
            "used": inodes_used,
            "free": inodes_free,
            "percent_used": round((inodes_used / inodes_total * 100), 2) if inodes_total > 0 else 0
        }
    except:
        return {}

def get_disk_io_metrics():
    """Get disk I/O statistics"""
    try:
        disk_io = psutil.disk_io_counters()
        return {
            "read_count": disk_io.read_count,
            "write_count": disk_io.write_count,
            "read_bytes": disk_io.read_bytes,
            "write_bytes": disk_io.write_bytes,
            "read_time_ms": disk_io.read_time,
            "write_time_ms": disk_io.write_time
        }
    except:
        return {}

# ==========================
# NETWORK METRICS
# ==========================

def get_network_metrics():
    """Get network I/O statistics"""
    try:
        net = psutil.net_io_counters()
        return {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "packets_sent": net.packets_sent,
            "packets_recv": net.packets_recv,
            "errin": net.errin,
            "errout": net.errout,
            "dropin": net.dropin,
            "dropout": net.dropout
        }
    except:
        return {}

def get_network_connections():
    """Get summary of network connections by status"""
    try:
        connections = psutil.net_connections()
        return {
            "total": len(connections),
            "established": len([c for c in connections if c.status == 'ESTABLISHED']),
            "listening": len([c for c in connections if c.status == 'LISTEN']),
            "time_wait": len([c for c in connections if c.status == 'TIME_WAIT']),
            "close_wait": len([c for c in connections if c.status == 'CLOSE_WAIT']),
            "fin_wait": len([c for c in connections if c.status == 'FIN_WAIT1' or c.status == 'FIN_WAIT2'])
        }
    except:
        return {}

def get_network_interfaces():
    """Get network interface statistics"""
    try:
        interfaces = psutil.net_if_stats()
        result = {}
        for interface, stats in interfaces.items():
            result[interface] = {
                "is_up": stats.isup,
                "speed": stats.speed,
                "mtu": stats.mtu,
                "errors": stats.errin + stats.errout,
                "dropped": stats.dropin + stats.dropout
            }
        return result
    except:
        return {}

# ==========================
# PROCESS METRICS
# ==========================

def get_top_processes(limit=None):
    """Get top processes by memory usage"""
    if limit is None:
        limit = TOP_PROCESSES_LIMIT
    
    try:
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'status']):
            try:
                processes.append({
                    "pid": proc.info['pid'],
                    "name": proc.info['name'],
                    "cpu_percent": round(proc.info['cpu_percent'], 2),
                    "memory_percent": round(proc.info['memory_percent'], 2),
                    "status": proc.info['status']
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        
        # Sort by memory usage
        processes.sort(key=lambda x: x['memory_percent'], reverse=True)
        return processes[:limit]
    except:
        return []

def get_process_count():
    """Get count of running processes and threads"""
    try:
        return {
            "total_processes": len(psutil.pids()),
            "total_threads": psutil.Process().num_threads(),
            "zombie_processes": len([p for p in psutil.process_iter(['status']) 
                                    if p.info['status'] == psutil.STATUS_ZOMBIE])
        }
    except:
        return {}

# ==========================
# SYSTEM HEALTH CHECKS
# ==========================

def get_system_alerts():
    """Generate alerts for critical conditions"""
    alerts = []
    
    try:
        # Memory alert
        mem = psutil.virtual_memory()
        if mem.percent > 90:
            alerts.append(f"⚠️ HIGH MEMORY: {mem.percent}% used")
        
        # Disk alert
        disk = psutil.disk_usage("/")
        if disk.percent > 90:
            alerts.append(f"⚠️ HIGH DISK: {disk.percent}% used")
        
        # CPU alert
        cpu_percent = psutil.cpu_percent(interval=1)
        if cpu_percent > 80:
            alerts.append(f"⚠️ HIGH CPU: {cpu_percent}% utilized")
        
        # Load average alert
        load1, _, _ = os.getloadavg()
        cpu_count = psutil.cpu_count(logical=True)
        if load1 > cpu_count * 1.5:
            alerts.append(f"⚠️ HIGH LOAD: {load1:.2f} (>{cpu_count * 1.5})")
        
        # Swap alert
        swap = psutil.swap_memory()
        if swap.percent > 50:
            alerts.append(f"⚠️ SWAP USAGE: {swap.percent}% used")
        
        # Inode alert
        stat = os.statvfs("/")
        inodes_total = stat.f_files
        inodes_free = stat.f_ffree
        if inodes_total > 0:
            inode_percent = ((inodes_total - inodes_free) / inodes_total) * 100
            if inode_percent > 85:
                alerts.append(f"⚠️ LOW INODES: {inode_percent:.2f}%")
    except:
        pass
    
    return alerts

# ==========================
# PAYLOAD BUILDER
# ==========================

def collect_metrics():
    """Collect all system metrics"""
    return {
        "agent_type": "core",
        "hostname": get_hostname(),
        "ip_address": get_local_ip(),
        "timestamp": datetime.now().isoformat(),
        "uptime_seconds": get_uptime_seconds(),
        "os_info": get_os_info(),
        "cpu": get_cpu_metrics(),
        "load_average": get_load_average(),
        "memory": get_memory_detailed(),
        "disk": get_disk_metrics(),
        "disk_inodes": get_disk_inodes(),
        "disk_io": get_disk_io_metrics(),
        "network": get_network_metrics(),
        "network_connections": get_network_connections(),
        "network_interfaces": get_network_interfaces(),
        "processes": get_process_count(),
        "top_processes": get_top_processes(),
        "alerts": get_system_alerts()
    }

# ==========================
# SENDER
# ==========================

def send_metrics(payload):
    """Send metrics to NMS server"""
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
    print(f"NMS Core Agent started on {get_hostname()}")
    print(f"Target: {NMS_SERVER_URL}")
    print(f"Interval: {INTERVAL_SECONDS} seconds")
    print("-" * 60)

    iteration = 0
    while True:
        try:
            iteration += 1
            metrics = collect_metrics()
            
            # Print alerts if any
            if metrics.get("alerts"):
                print(f"\n ALERTS at {datetime.now().strftime('%H:%M:%S')}:")
                for alert in metrics["alerts"]:
                    print(f"   {alert}")
            
            send_metrics(metrics)
            print(f"✅ [{iteration}] Metrics sent successfully at {datetime.now().strftime('%H:%M:%S')}")
            
        except requests.exceptions.ConnectionError:
            print(f"❌ Connection failed - NMS server unreachable at {datetime.now().strftime('%H:%M:%S')}")
        except requests.exceptions.Timeout:
            print(f"⏱️  Request timeout at {datetime.now().strftime('%H:%M:%S')}")
        except requests.exceptions.HTTPError as e:
            print(f"❌ HTTP Error: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            print(f"❌ Error: {e}")

        time.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nNMS Agent stopped by user")
    except Exception as e:
        print(f"\n\n💥 Fatal error: {e}")
