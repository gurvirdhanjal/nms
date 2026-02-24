import psutil
import platform
import socket
import time
import requests
import os
import uuid
import sys
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

# ==========================
# CONFIGURATION
# ==========================

NMS_SERVER_URL = "http://127.0.0.1:5001/api/agent/metrics"
AGENT_TOKEN = "8f42v73054r1749f8g58848be5e6502c"  # Updated to match config.py default
INTERVAL_SECONDS = 30
REQUEST_TIMEOUT = 5
TOP_PROCESSES_LIMIT = 5
_HARDWARE_SPECS_CACHE = None
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

if platform.system() == "Windows":
    _program_data = os.environ.get("PROGRAMDATA", "C:\\ProgramData")
    LOG_FILE_PATH = os.environ.get("NMS_AGENT_LOG_FILE", os.path.join(_program_data, "nms-agent", "agent.log"))
    DEVICE_ID_PATH = os.environ.get("NMS_AGENT_DEVICE_ID_PATH", os.path.join(_program_data, "nms-agent", "device_id"))
else:
    LOG_FILE_PATH = os.environ.get("NMS_AGENT_LOG_FILE", "/var/log/nms-agent/agent.log")
    DEVICE_ID_PATH = os.environ.get("NMS_AGENT_DEVICE_ID_PATH", "/etc/nms-agent/device_id")

FALLBACK_DEVICE_ID_PATH = os.path.join(os.path.expanduser("~"), ".nms-agent", "device_id")

_SKIP_FS_TYPES = {
    "tmpfs",
    "squashfs",
    "devtmpfs",
    "overlay",
    "proc",
    "sysfs",
    "cgroup",
    "cgroup2",
    "autofs",
    "debugfs",
    "tracefs",
    "nsfs",
    "securityfs",
    "pstore",
    "mqueue",
    "hugetlbfs",
    "rpc_pipefs",
    "fusectl",
    "configfs",
}

_DEVICE_UUID = None
_PREV_NET_SNAPSHOT = None
_PREV_NET_PERNIC_SNAPSHOT = None
_PREV_DISK_SNAPSHOT = None
_PREV_CPU_STATS_SNAPSHOT = None
_PREV_PGMAJFAULT_SNAPSHOT = None
_PREV_TCP_RETRANS_SEGMENTS = None
_LOG = logging.getLogger("nms_agent")

# ==========================
# HELPERS
# ==========================

def setup_logging():
    """Log to stdout and rotating file (10MB x 5)."""
    _LOG.setLevel(logging.INFO)
    _LOG.propagate = False
    _LOG.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s [nms-agent] %(message)s")
    formatter.converter = time.gmtime

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    _LOG.addHandler(stream_handler)

    try:
        log_dir = os.path.dirname(LOG_FILE_PATH)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE_PATH,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        _LOG.addHandler(file_handler)
    except Exception as exc:
        _LOG.warning("File logging unavailable at %s: %s", LOG_FILE_PATH, exc)


def _ensure_parent_dir(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _load_uuid_from_file(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
        if not value:
            return None
        return str(uuid.UUID(value))
    except Exception:
        return None


def _persist_uuid(path, value):
    _ensure_parent_dir(path)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(value)
    os.replace(tmp_path, path)


def get_or_create_device_uuid():
    """Return stable UUID stored on disk."""
    global _DEVICE_UUID
    if _DEVICE_UUID:
        return _DEVICE_UUID

    existing = _load_uuid_from_file(DEVICE_ID_PATH)
    if existing:
        _DEVICE_UUID = existing
        return _DEVICE_UUID

    new_value = str(uuid.uuid4())
    try:
        _persist_uuid(DEVICE_ID_PATH, new_value)
        _DEVICE_UUID = new_value
        return _DEVICE_UUID
    except Exception as exc:
        _LOG.warning("Cannot write device ID at %s: %s", DEVICE_ID_PATH, exc)

    fallback_existing = _load_uuid_from_file(FALLBACK_DEVICE_ID_PATH)
    if fallback_existing:
        _DEVICE_UUID = fallback_existing
        return _DEVICE_UUID

    try:
        _persist_uuid(FALLBACK_DEVICE_ID_PATH, new_value)
        _DEVICE_UUID = new_value
    except Exception as exc:
        _LOG.warning("Cannot write fallback device ID at %s: %s", FALLBACK_DEVICE_ID_PATH, exc)
        _DEVICE_UUID = new_value

    return _DEVICE_UUID


def _delta_rate(current, previous, elapsed):
    if previous is None or elapsed is None or elapsed <= 0:
        return None
    delta = current - previous
    if delta < 0:
        return None
    return delta / elapsed


def _read_pgmajfault_linux():
    """Read cumulative major page faults from /proc/vmstat (Linux only)."""
    if platform.system() != "Linux":
        return None
    try:
        with open("/proc/vmstat", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("pgmajfault"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except Exception:
        return None
    return None


def _read_file_nr_linux():
    """Read system-wide file descriptor usage from /proc/sys/fs/file-nr."""
    if platform.system() != "Linux":
        return (None, None)
    try:
        with open("/proc/sys/fs/file-nr", "r", encoding="utf-8") as handle:
            parts = handle.read().split()
        if len(parts) < 3:
            return (None, None)
        return (int(parts[0]), int(parts[2]))
    except Exception:
        return (None, None)


def _read_tcp_retrans_segs_linux():
    """Read cumulative TCP RetransSegs from /proc/net/snmp (Linux only)."""
    if platform.system() != "Linux":
        return None
    try:
        with open("/proc/net/snmp", "r", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.startswith("Tcp:")]
        for idx in range(len(lines) - 1):
            header = lines[idx].split()
            values = lines[idx + 1].split()
            if (
                len(header) != len(values)
                or not header
                or not values
                or header[0] != "Tcp:"
                or values[0] != "Tcp:"
                or "RetransSegs" not in header
            ):
                continue
            retrans_idx = header.index("RetransSegs")
            return int(values[retrans_idx])
    except Exception:
        return None
    return None

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


def _first_non_empty(values):
    for value in values:
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                return cleaned
    return None


def _get_cpu_model():
    model = _first_non_empty([platform.processor(), platform.uname().processor])
    if model:
        return model

    if platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass

    env_model = os.environ.get("PROCESSOR_IDENTIFIER")
    if env_model and env_model.strip():
        return env_model.strip()

    return "Unknown CPU"


def get_hardware_specs():
    """Collect mostly-static hardware details once and reuse."""
    global _HARDWARE_SPECS_CACHE
    if isinstance(_HARDWARE_SPECS_CACHE, dict):
        return _HARDWARE_SPECS_CACHE

    try:
        memory = psutil.virtual_memory()
        disk = get_disk_metrics()
        specs = {
            "cpu_model": _get_cpu_model(),
            "cpu_physical_cores": psutil.cpu_count(logical=False),
            "cpu_logical_cores": psutil.cpu_count(logical=True),
            "memory_total_gb": round(memory.total / (1024**3), 2) if memory else None,
            "disk_total_gb": disk.get("total_gb"),
            "architecture": platform.machine()
        }
    except Exception:
        specs = {
            "cpu_model": "Unknown CPU",
            "cpu_physical_cores": None,
            "cpu_logical_cores": None,
            "memory_total_gb": None,
            "disk_total_gb": None,
            "architecture": platform.machine()
        }

    _HARDWARE_SPECS_CACHE = specs
    return specs

def get_uptime_seconds():
    try:
        return int(time.time() - psutil.boot_time())
    except:
        return 0

# ==========================
# CPU METRICS
# ==========================

def get_cpu_metrics():
    cpu_percent = psutil.cpu_percent(interval=1)
    cpu_times = psutil.cpu_times_percent(interval=None)
    iowait = getattr(cpu_times, "iowait", None)
    steal = getattr(cpu_times, "steal", None)
    return {
        "cpu_percent": cpu_percent,
        "cpu_iowait_percent": round(iowait, 2) if iowait is not None else None,
        "cpu_steal_percent": round(steal, 2) if steal is not None else None,
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
    global _PREV_PGMAJFAULT_SNAPSHOT
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    page_faults_per_sec = None

    pgmajfault = _read_pgmajfault_linux()
    if pgmajfault is not None:
        now_mono = time.monotonic()
        page_faults_per_sec = 0.0
        if _PREV_PGMAJFAULT_SNAPSHOT:
            prev_faults, prev_mono = _PREV_PGMAJFAULT_SNAPSHOT
            elapsed = now_mono - prev_mono
            rate = _delta_rate(pgmajfault, prev_faults, elapsed)
            page_faults_per_sec = rate if rate is not None else 0.0
        _PREV_PGMAJFAULT_SNAPSHOT = (pgmajfault, now_mono)

    return {
        "total_mb": mem.total // (1024 * 1024),
        "used_mb": mem.used // (1024 * 1024),
        "available_mb": mem.available // (1024 * 1024),
        "percent": mem.percent,
        "used_gb": round(mem.used / (1024**3), 2),
        "total_gb": round(mem.total / (1024**3), 2),
        "swap_total_mb": swap.total // (1024 * 1024),
        "swap_used_mb": swap.used // (1024 * 1024),
        "swap_percent": swap.percent,
        "page_faults_per_sec": round(page_faults_per_sec, 2) if page_faults_per_sec is not None else None,
    }

# ==========================
# DISK METRICS
# ==========================

def _should_skip_partition(part):
    if not part:
        return True

    fstype = (getattr(part, "fstype", "") or "").lower()
    if fstype in _SKIP_FS_TYPES:
        return True

    opts = (getattr(part, "opts", "") or "").lower()
    if "cdrom" in opts:
        return True

    mountpoint = (getattr(part, "mountpoint", "") or "").strip()
    if not mountpoint:
        return True

    return False

def get_disk_metrics():
    """Get disk space usage across partitions, skipping pseudo filesystems."""
    try:
        partitions = psutil.disk_partitions(all=False)
        seen_mounts = set()
        total_bytes = 0
        used_bytes = 0
        free_bytes = 0
        partition_items = []

        for part in partitions:
            if _should_skip_partition(part):
                continue
            mountpoint = part.mountpoint
            if mountpoint in seen_mounts:
                continue
            seen_mounts.add(mountpoint)

            try:
                usage = psutil.disk_usage(mountpoint)
            except Exception:
                continue

            if usage.total <= 0:
                continue

            total_bytes += usage.total
            used_bytes += usage.used
            free_bytes += usage.free
            partition_items.append({
                "mountpoint": mountpoint,
                "fstype": part.fstype,
                "total_gb": round(usage.total / (1024**3), 2),
                "used_gb": round(usage.used / (1024**3), 2),
                "free_gb": round(usage.free / (1024**3), 2),
                "percent": usage.percent,
            })

        if total_bytes == 0:
            # Final fallback in unusual containerized/permission-constrained hosts.
            fallback = psutil.disk_usage("/")
            total_bytes = fallback.total
            used_bytes = fallback.used
            free_bytes = fallback.free

        percent = (used_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0
        return {
            "total_gb": round(total_bytes / (1024**3), 2),
            "used_gb": round(used_bytes / (1024**3), 2),
            "free_gb": round(free_bytes / (1024**3), 2),
            "percent": round(percent, 2),
            "partitions": partition_items,
        }
    except Exception:
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
    """Get disk I/O statistics with per-second rates."""
    global _PREV_DISK_SNAPSHOT
    try:
        now_mono = time.monotonic()
        disk_io = psutil.disk_io_counters()
        read_bps = 0.0
        write_bps = 0.0
        read_iops = 0.0
        write_iops = 0.0
        busy_percent = 0.0 if getattr(disk_io, "busy_time", None) is not None else None

        if _PREV_DISK_SNAPSHOT:
            prev_io, prev_mono = _PREV_DISK_SNAPSHOT
            elapsed = now_mono - prev_mono
            read_bps = _delta_rate(disk_io.read_bytes, prev_io.read_bytes, elapsed)
            write_bps = _delta_rate(disk_io.write_bytes, prev_io.write_bytes, elapsed)
            read_iops = _delta_rate(disk_io.read_count, prev_io.read_count, elapsed)
            write_iops = _delta_rate(disk_io.write_count, prev_io.write_count, elapsed)
            curr_busy_time = getattr(disk_io, "busy_time", None)
            prev_busy_time = getattr(prev_io, "busy_time", None)
            elapsed_ms = elapsed * 1000.0
            if curr_busy_time is not None and prev_busy_time is not None and elapsed_ms > 0:
                delta_busy = curr_busy_time - prev_busy_time
                if delta_busy < 0:
                    delta_busy = 0
                busy_percent = max(0.0, min(100.0, (delta_busy / elapsed_ms) * 100.0))

        _PREV_DISK_SNAPSHOT = (disk_io, now_mono)
        read_latency_ms = (disk_io.read_time / disk_io.read_count) if disk_io.read_count else 0.0
        write_latency_ms = (disk_io.write_time / disk_io.write_count) if disk_io.write_count else 0.0

        return {
            "read_count": disk_io.read_count,
            "write_count": disk_io.write_count,
            "read_bytes": disk_io.read_bytes,
            "write_bytes": disk_io.write_bytes,
            "read_time_ms": disk_io.read_time,
            "write_time_ms": disk_io.write_time,
            "read_latency_ms": round(read_latency_ms, 2),
            "write_latency_ms": round(write_latency_ms, 2),
            "busy_percent": round(busy_percent, 2) if busy_percent is not None else None,
            "rates": {
                "read_bps": round(read_bps, 2),
                "write_bps": round(write_bps, 2),
                "read_iops": round(read_iops, 2),
                "write_iops": round(write_iops, 2),
            }
        }
    except Exception:
        return {}

# ==========================
# NETWORK METRICS
# ==========================

def get_network_metrics():
    """Get network I/O statistics with per-second throughput."""
    global _PREV_NET_SNAPSHOT, _PREV_NET_PERNIC_SNAPSHOT, _PREV_TCP_RETRANS_SEGMENTS
    try:
        now_mono = time.monotonic()
        net = psutil.net_io_counters()
        pernic = psutil.net_io_counters(pernic=True)
        sent_bps = 0.0
        recv_bps = 0.0
        sent_pps = 0.0
        recv_pps = 0.0
        per_interface_throughput = {}
        tcp_retransmits_delta = None

        if _PREV_NET_SNAPSHOT:
            prev_net, prev_mono = _PREV_NET_SNAPSHOT
            elapsed = now_mono - prev_mono
            sent_bps = _delta_rate(net.bytes_sent, prev_net.bytes_sent, elapsed)
            recv_bps = _delta_rate(net.bytes_recv, prev_net.bytes_recv, elapsed)
            sent_pps = _delta_rate(net.packets_sent, prev_net.packets_sent, elapsed)
            recv_pps = _delta_rate(net.packets_recv, prev_net.packets_recv, elapsed)

        if _PREV_NET_PERNIC_SNAPSHOT:
            prev_pernic, prev_pernic_mono = _PREV_NET_PERNIC_SNAPSHOT
            pernic_elapsed = now_mono - prev_pernic_mono
        else:
            prev_pernic = {}
            pernic_elapsed = None

        for interface, counters in pernic.items():
            iface_sent_bps = 0.0
            iface_recv_bps = 0.0
            iface_sent_pps = 0.0
            iface_recv_pps = 0.0
            prev_counters = prev_pernic.get(interface)
            if prev_counters and pernic_elapsed and pernic_elapsed > 0:
                sent_rate = _delta_rate(counters.bytes_sent, prev_counters.bytes_sent, pernic_elapsed)
                recv_rate = _delta_rate(counters.bytes_recv, prev_counters.bytes_recv, pernic_elapsed)
                sent_packets = _delta_rate(counters.packets_sent, prev_counters.packets_sent, pernic_elapsed)
                recv_packets = _delta_rate(counters.packets_recv, prev_counters.packets_recv, pernic_elapsed)
                iface_sent_bps = sent_rate if sent_rate is not None else 0.0
                iface_recv_bps = recv_rate if recv_rate is not None else 0.0
                iface_sent_pps = sent_packets if sent_packets is not None else 0.0
                iface_recv_pps = recv_packets if recv_packets is not None else 0.0
            per_interface_throughput[interface] = {
                "bytes_sent": counters.bytes_sent,
                "bytes_recv": counters.bytes_recv,
                "packets_sent": counters.packets_sent,
                "packets_recv": counters.packets_recv,
                "errin": counters.errin,
                "errout": counters.errout,
                "dropin": counters.dropin,
                "dropout": counters.dropout,
                "sent_bps": round(iface_sent_bps, 2),
                "recv_bps": round(iface_recv_bps, 2),
                "sent_pps": round(iface_sent_pps, 2),
                "recv_pps": round(iface_recv_pps, 2),
            }

        retrans_total = _read_tcp_retrans_segs_linux()
        if retrans_total is not None:
            tcp_retransmits_delta = 0
            if _PREV_TCP_RETRANS_SEGMENTS is not None:
                delta = retrans_total - _PREV_TCP_RETRANS_SEGMENTS
                tcp_retransmits_delta = delta if delta >= 0 else 0
            _PREV_TCP_RETRANS_SEGMENTS = retrans_total

        _PREV_NET_SNAPSHOT = (net, now_mono)
        _PREV_NET_PERNIC_SNAPSHOT = (pernic, now_mono)

        return {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "packets_sent": net.packets_sent,
            "packets_recv": net.packets_recv,
            "errin": net.errin,
            "errout": net.errout,
            "dropin": net.dropin,
            "dropout": net.dropout,
            "tcp_retransmits_delta": tcp_retransmits_delta,
            "throughput": {
                "sent_bps": round(sent_bps, 2),
                "recv_bps": round(recv_bps, 2),
                "sent_pps": round(sent_pps, 2),
                "recv_pps": round(recv_pps, 2),
            },
            "per_interface_throughput": per_interface_throughput,
        }
    except Exception:
        return {}

def get_network_connections():
    """Get summary of network connections by status and top active remote IPs"""
    try:
        connections = psutil.net_connections(kind='inet')
        
        established = 0
        listening = 0
        time_wait = 0
        close_wait = 0
        fin_wait = 0
        
        remote_ips = {}
        
        for c in connections:
            if c.status == 'ESTABLISHED':
                established += 1
                if c.raddr and hasattr(c.raddr, 'ip'):
                    ip = c.raddr.ip
                    # Filter out localhost
                    if not ip.startswith('127.') and ip != '::1':
                        remote_ips[ip] = remote_ips.get(ip, 0) + 1
            elif c.status == 'LISTEN':
                listening += 1
            elif c.status == 'TIME_WAIT':
                time_wait += 1
            elif c.status == 'CLOSE_WAIT':
                close_wait += 1
            elif c.status in ('FIN_WAIT1', 'FIN_WAIT2'):
                fin_wait += 1
                
        # Sort and get top 20 connected IPs
        top_ips = sorted(remote_ips.items(), key=lambda x: x[1], reverse=True)[:20]
        top_remote_ips = [{"ip": ip, "count": count} for ip, count in top_ips]

        return {
            "total": len(connections),
            "established": established,
            "unique_remote_ips_count": len(remote_ips),
            "listening": listening,
            "time_wait": time_wait,
            "close_wait": close_wait,
            "fin_wait": fin_wait,
            "top_remote_ips": top_remote_ips
        }
    except Exception as e:
        _LOG.debug(f"Error fetching connections: {e}")
        return {}

def get_network_interfaces():
    """Get network interface statistics"""
    try:
        interfaces = psutil.net_if_stats()
        nic_counters = psutil.net_io_counters(pernic=True)
        result = {}
        for interface, stats in interfaces.items():
            counters = nic_counters.get(interface)
            result[interface] = {
                "is_up": stats.isup,
                "speed": stats.speed,
                "mtu": stats.mtu,
                "errors": (counters.errin + counters.errout) if counters else 0,
                "dropped": (counters.dropin + counters.dropout) if counters else 0
            }
        return result
    except Exception:
        return {}

# ==========================
# PROCESS METRICS
# ==========================

def get_top_processes(limit=None, samples=None):
    """Get top processes by memory usage"""
    if limit is None:
        limit = TOP_PROCESSES_LIMIT
    
    try:
        processes = list(samples) if isinstance(samples, list) else _collect_process_samples()
        processes.sort(key=lambda x: x['memory_percent'], reverse=True)
        return processes[:limit]
    except:
        return []


def get_top_processes_by_cpu(limit=None, samples=None):
    """Get top processes by CPU usage."""
    if limit is None:
        limit = TOP_PROCESSES_LIMIT

    try:
        processes = list(samples) if isinstance(samples, list) else _collect_process_samples()
        processes.sort(key=lambda x: x['cpu_percent'], reverse=True)
        return processes[:limit]
    except:
        return []


def _collect_process_samples():
    """Collect process samples used for top-lists."""
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'status']):
        try:
            cpu_percent = proc.info.get('cpu_percent')
            memory_percent = proc.info.get('memory_percent')
            processes.append({
                "pid": proc.info['pid'],
                "name": proc.info.get('name'),
                "cpu_percent": round(float(cpu_percent), 2) if cpu_percent is not None else 0.0,
                "memory_percent": round(float(memory_percent), 2) if memory_percent is not None else 0.0,
                "status": proc.info.get('status')
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, TypeError, ValueError):
            continue
    return processes


def get_fd_metrics():
    """Get system-wide file descriptor pressure (Linux only)."""
    open_fds, fd_limit = _read_file_nr_linux()
    fd_percent = None
    if open_fds is not None and fd_limit and fd_limit > 0:
        fd_percent = (open_fds / fd_limit) * 100.0
    return {
        "open_fds": open_fds,
        "fd_limit": fd_limit,
        "fd_percent": round(fd_percent, 2) if fd_percent is not None else None,
    }

def get_process_count():
    """Get count of running processes and threads"""
    global _PREV_CPU_STATS_SNAPSHOT
    try:
        now_mono = time.monotonic()
        cpu_stats = psutil.cpu_stats()
        context_switches_per_sec = 0.0

        if _PREV_CPU_STATS_SNAPSHOT:
            prev_stats, prev_mono = _PREV_CPU_STATS_SNAPSHOT
            elapsed = now_mono - prev_mono
            rate = _delta_rate(cpu_stats.ctx_switches, prev_stats.ctx_switches, elapsed)
            context_switches_per_sec = rate if rate is not None else 0.0

        _PREV_CPU_STATS_SNAPSHOT = (cpu_stats, now_mono)
        fd_metrics = get_fd_metrics()

        return {
            "total_processes": len(psutil.pids()),
            "total_threads": psutil.Process().num_threads(),
            "zombie_processes": len([p for p in psutil.process_iter(['status']) 
                                    if p.info['status'] == psutil.STATUS_ZOMBIE]),
            "context_switches_per_sec": round(context_switches_per_sec, 2),
            "open_fds": fd_metrics.get("open_fds"),
            "fd_limit": fd_metrics.get("fd_limit"),
            "fd_percent": fd_metrics.get("fd_percent"),
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
        mem = psutil.virtual_memory()
        if mem.percent > 90:
            alerts.append(f"HIGH MEMORY: {mem.percent}% used")
    except Exception:
        pass

    try:
        disk = get_disk_metrics()
        if disk.get("percent", 0) > 90:
            alerts.append(f"HIGH DISK: {disk.get('percent')}% used")
    except Exception:
        pass

    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        if cpu_percent > 80:
            alerts.append(f"HIGH CPU: {cpu_percent}% utilized")
    except Exception:
        pass

    try:
        if hasattr(os, "getloadavg"):
            load1, _, _ = os.getloadavg()
            cpu_count = psutil.cpu_count(logical=True) or 1
            if load1 > cpu_count * 1.5:
                alerts.append(f"HIGH LOAD: {load1:.2f} (>{cpu_count * 1.5})")
    except Exception:
        pass

    try:
        swap = psutil.swap_memory()
        if swap.percent > 50:
            alerts.append(f"SWAP USAGE: {swap.percent}% used")
    except Exception:
        pass

    try:
        if os.name != "nt":
            stat = os.statvfs("/")
            inodes_total = stat.f_files
            inodes_free = stat.f_ffree
            if inodes_total > 0:
                inode_percent = ((inodes_total - inodes_free) / inodes_total) * 100
                if inode_percent > 85:
                    alerts.append(f"LOW INODES: {inode_percent:.2f}%")
    except Exception:
        pass

    return alerts

# ==========================
# PAYLOAD BUILDER
# ==========================

def collect_metrics():
    """Collect all system metrics"""
    device_uuid = get_or_create_device_uuid()
    process_samples = _collect_process_samples()
    top_processes_by_memory = get_top_processes(samples=process_samples)
    top_processes_by_cpu = get_top_processes_by_cpu(samples=process_samples)
    return {
        "agent_type": "core",
        "device_uuid": device_uuid,
        "device_id": device_uuid,
        "hostname": get_hostname(),
        "ip_address": get_local_ip(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": get_uptime_seconds(),
        "os_info": get_os_info(),
        "hardware_specs": get_hardware_specs(),
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
        "top_processes": top_processes_by_memory,
        "top_processes_cpu": top_processes_by_cpu,
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
    setup_logging()
    device_uuid = get_or_create_device_uuid()
    _LOG.info("NMS Core Agent started on %s", get_hostname())
    _LOG.info("Target: %s", NMS_SERVER_URL)
    _LOG.info("Interval: %s seconds", INTERVAL_SECONDS)
    _LOG.info("Device UUID: %s", device_uuid)
    _LOG.info("-" * 60)

    iteration = 0
    while True:
        try:
            # --- SAFEGUARDS ---
            try:
                # 1. Memory Leak Protection (Auto-exit if > 150MB) 
                # The OS service manager will instantly restart this clean process
                process = psutil.Process(os.getpid())
                mem_mb = process.memory_info().rss / (1024 * 1024)
                if mem_mb > 150.0:
                    _LOG.error(f"Memory safeguard triggered! Agent consuming {mem_mb:.1f}MB (Limit 150MB). Auto-restarting...")
                    sys.exit(1)
                    
                # 2. CPU Shedding (Throttle if host is under extreme load > 95%)
                host_cpu = psutil.cpu_percent(interval=None)
                current_interval = INTERVAL_SECONDS
                if host_cpu > 95.0:
                    _LOG.warning(f"Host CPU critically high ({host_cpu}%). Throttling next scan interval to {INTERVAL_SECONDS * 2}s to save resources.")
                    current_interval = INTERVAL_SECONDS * 2
            except Exception as e:
                _LOG.debug(f"Safeguard check failed: {e}")
                current_interval = INTERVAL_SECONDS
            # ------------------

            iteration += 1
            metrics = collect_metrics()

            if metrics.get("alerts"):
                _LOG.warning("ALERTS at %s:", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
                for alert in metrics["alerts"]:
                    _LOG.warning("  %s", alert)

            send_metrics(metrics)
            _LOG.info(
                "[%s] Metrics sent successfully at %s",
                iteration,
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            )
        except requests.exceptions.ConnectionError:
            _LOG.error(
                "Connection failed - NMS server unreachable at %s",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            )
        except requests.exceptions.Timeout:
            _LOG.error("Request timeout at %s", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        except requests.exceptions.HTTPError as e:
            _LOG.error("HTTP Error: %s - %s", e.response.status_code, e.response.text)
        except Exception as e:
            _LOG.exception("Unhandled error: %s", e)

        time.sleep(current_interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        if not _LOG.handlers:
            setup_logging()
        _LOG.info("NMS Agent stopped by user")
    except Exception as e:
        if not _LOG.handlers:
            setup_logging()
        _LOG.exception("Fatal error: %s", e)

