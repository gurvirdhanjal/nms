"""
Accurate hardware/disk/network/CPU telemetry, ported from server_agent.py so
service.py reports the same level of detail as the server agent.
"""
import os
import platform
import time

import psutil

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

_HARDWARE_SPECS_CACHE = None
_PREV_DISK_SNAPSHOT = None
_PREV_NET_SNAPSHOT = None
_PREV_NET_PERNIC_SNAPSHOT = None
_PREV_TCP_RETRANS_SEGMENTS = None


def _delta_rate(current, previous, elapsed):
    if previous is None or elapsed is None or elapsed <= 0:
        return None
    delta = current - previous
    if delta < 0:
        return None
    return delta / elapsed


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
            rate = _delta_rate(disk_io.read_bytes, prev_io.read_bytes, elapsed)
            read_bps = rate if rate is not None else 0.0
            rate = _delta_rate(disk_io.write_bytes, prev_io.write_bytes, elapsed)
            write_bps = rate if rate is not None else 0.0
            rate = _delta_rate(disk_io.read_count, prev_io.read_count, elapsed)
            read_iops = rate if rate is not None else 0.0
            rate = _delta_rate(disk_io.write_count, prev_io.write_count, elapsed)
            write_iops = rate if rate is not None else 0.0
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


def get_cpu_metrics():
    cpu_percent = psutil.cpu_percent(interval=1)
    cpu_times = psutil.cpu_times_percent(interval=None)
    iowait = getattr(cpu_times, "iowait", None)
    steal = getattr(cpu_times, "steal", None)
    per_core = psutil.cpu_percent(percpu=True)
    return {
        "cpu_percent": cpu_percent,
        "cpu_iowait_percent": round(iowait, 2) if iowait is not None else None,
        "cpu_steal_percent": round(steal, 2) if steal is not None else None,
        "cpu_cores": psutil.cpu_count(logical=True),
        "cpu_cores_physical": psutil.cpu_count(logical=False),
        "cpu_per_core": per_core,
    }


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
            rate = _delta_rate(net.bytes_sent, prev_net.bytes_sent, elapsed)
            sent_bps = rate if rate is not None else 0.0
            rate = _delta_rate(net.bytes_recv, prev_net.bytes_recv, elapsed)
            recv_bps = rate if rate is not None else 0.0
            rate = _delta_rate(net.packets_sent, prev_net.packets_sent, elapsed)
            sent_pps = rate if rate is not None else 0.0
            rate = _delta_rate(net.packets_recv, prev_net.packets_recv, elapsed)
            recv_pps = rate if rate is not None else 0.0

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
