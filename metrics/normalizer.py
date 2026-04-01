from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# Standardized Metric Constants
DEVICE_AVAILABILITY = "device_availability"
NETWORK_LATENCY_MS = "network_latency_ms"
PACKET_LOSS_PERCENT = "packet_loss_percent"
NETWORK_JITTER_MS = "network_jitter_ms"
OPEN_PORTS_COUNT = "open_ports_count"

# SNMP System Metrics
SNMP_UPTIME_SECONDS = "snmp_uptime_seconds"
SNMP_SYS_NAME = "snmp_sys_name"
SNMP_SYS_LOCATION = "snmp_sys_location"
SNMP_SYS_DESCR = "snmp_sys_descr"

# SNMP Health Metrics
SNMP_CPU_USAGE = "snmp_cpu_usage"
SNMP_MEMORY_USAGE = "snmp_memory_usage"
SNMP_DISK_USAGE = "snmp_disk_usage"

# SNMP Interface Metrics
SNMP_IF_OPER_STATUS = "snmp_if_oper_status"
SNMP_IF_IN_OCTETS = "snmp_if_in_octets"
SNMP_IF_OUT_OCTETS = "snmp_if_out_octets"
SNMP_IF_IN_ERRORS = "snmp_if_in_errors"
SNMP_IF_OUT_ERRORS = "snmp_if_out_errors"

@dataclass
class Metric:
    """
    Standardized metric object.
    Prepared for future SNMP integration.
    """
    name: str
    value: Any
    unit: str
    device_ip: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    labels: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "device_ip": self.device_ip,
            "timestamp": self.timestamp.isoformat(),
            "labels": self.labels
        }

class MetricNormalizer:
    """
    Converts raw system data into standardized Metric objects.
    """
    
    @staticmethod
    def normalize_ping(
        device_ip: str, 
        status: str, 
        latency_ms: Optional[float], 
        timestamp: Optional[datetime] = None,
        packet_loss: Optional[float] = None,
        jitter: Optional[float] = None
    ) -> List[Metric]:
        """
        Convert ping results into metrics.
        
        Args:
            device_ip: IP address of the device
            status: "Online" or "Offline"
            latency_ms: Ping latency in milliseconds (can be None if offline)
            timestamp: Optional override for metric timestamp
            packet_loss: Packet loss percentage (0-100)
            
        Returns:
            List of Metric objects
        """
        metrics = []
        
        # 1. Availability Metric
        is_online = 1 if status == "Online" else 0
        
        # Helper to create metric with optional timestamp
        def create_metric(name, value, unit):
            m = Metric(
                name=name,
                value=value,
                unit=unit,
                device_ip=device_ip
            )
            if timestamp:
                m.timestamp = timestamp
            return m

        metrics.append(create_metric(DEVICE_AVAILABILITY, is_online, "boolean"))
        
        # 2. Latency Metric (only if available)
        if latency_ms is not None:
            metrics.append(create_metric(NETWORK_LATENCY_MS, float(latency_ms), "ms"))
        
        # 3. Packet Loss Metric
        if packet_loss is not None:
            metrics.append(create_metric(PACKET_LOSS_PERCENT, float(packet_loss), "percent"))
            
        # 4. Jitter Metric
        if jitter is not None:
            metrics.append(create_metric(NETWORK_JITTER_MS, float(jitter), "ms"))
            
        return metrics

    @staticmethod
    def normalize_port_scan(device_ip: str, open_ports: List[Dict]) -> List[Metric]:
        """
        Convert port scan results into metrics.
        
        Args:
            device_ip: IP address of the device
            open_ports: List of open port details
            
        Returns:
            List of Metric objects
        """
        metrics = []
        
        # Open Ports Count
        count = len(open_ports)
        metrics.append(Metric(
            name=OPEN_PORTS_COUNT,
            value=count,
            unit="count",
            device_ip=device_ip
        ))
        
        return metrics

    @staticmethod
    def normalize_snmp_system(device_ip: str, system_info: dict) -> List[Metric]:
        """
        Convert SNMP system information into metrics.

        Emits:
            - snmp_uptime_seconds
            - snmp_sys_name
            - snmp_sys_location
            - snmp_sys_descr
        """
        if not isinstance(system_info, dict) or "error" in system_info:
            return []

        metrics = []
        source_labels = {"source": "snmp"}

        uptime = system_info.get("sys_uptime_seconds")
        if uptime is not None:
            try:
                uptime_value = float(uptime)
            except (TypeError, ValueError):
                uptime_value = uptime
            metrics.append(Metric(
                name=SNMP_UPTIME_SECONDS,
                value=uptime_value,
                unit="seconds",
                device_ip=device_ip,
                labels=source_labels.copy()
            ))

        sys_name = system_info.get("sys_name")
        if sys_name is not None:
            metrics.append(Metric(
                name=SNMP_SYS_NAME,
                value=str(sys_name),
                unit="string",
                device_ip=device_ip,
                labels=source_labels.copy()
            ))

        sys_location = system_info.get("sys_location")
        if sys_location is not None:
            metrics.append(Metric(
                name=SNMP_SYS_LOCATION,
                value=str(sys_location),
                unit="string",
                device_ip=device_ip,
                labels=source_labels.copy()
            ))

        sys_descr = system_info.get("sys_descr")
        if sys_descr is not None:
            metrics.append(Metric(
                name=SNMP_SYS_DESCR,
                value=str(sys_descr),
                unit="string",
                device_ip=device_ip,
                labels=source_labels.copy()
            ))

        return metrics

    @staticmethod
    def normalize_snmp_health(device_ip: str, health: dict) -> List[Metric]:
        """
        Convert SNMP health payload into CPU/RAM/Disk percentage metrics.
        """
        if not isinstance(health, dict):
            return []

        metrics = []
        source_labels = {"source": "snmp"}

        metric_map = (
            ("cpu_usage", SNMP_CPU_USAGE),
            ("memory_usage", SNMP_MEMORY_USAGE),
            ("disk_usage", SNMP_DISK_USAGE),
        )

        for payload_key, metric_name in metric_map:
            value = health.get(payload_key)
            if value is None:
                continue
            try:
                metric_value = float(value)
            except (TypeError, ValueError):
                continue
            metrics.append(Metric(
                name=metric_name,
                value=metric_value,
                unit="percent",
                device_ip=device_ip,
                labels=source_labels.copy()
            ))

        return metrics

    @staticmethod
    def normalize_snmp_interfaces(device_ip: str, interfaces: list,
                                  counters: list) -> List[Metric]:
        """
        Normalize SNMP interface status + counter payload into metrics.

        Merge strategy:
            - Join interfaces and counters by if_index
            - Emit oper status for each interface
            - Emit in/out octets and in/out errors when present in counters
        """
        if not isinstance(interfaces, list):
            interfaces = []
        if not isinstance(counters, list):
            counters = []

        metrics = []
        counters_by_index = {}

        for counter in counters:
            if not isinstance(counter, dict):
                continue
            if_index = counter.get("if_index")
            if if_index is None:
                continue
            try:
                normalized_if_index = int(if_index)
            except (TypeError, ValueError):
                continue
            counters_by_index[normalized_if_index] = counter

        for iface in interfaces:
            if not isinstance(iface, dict):
                continue
            if_index = iface.get("if_index")
            if if_index is None:
                continue

            try:
                normalized_if_index = int(if_index)
            except (TypeError, ValueError):
                continue

            labels = {
                "source": "snmp",
                "if_index": str(normalized_if_index)
            }
            if iface.get("name") is not None:
                labels["if_name"] = str(iface.get("name"))

            oper_status = str(iface.get("oper_status", "")).strip().lower()
            oper_value = 1 if oper_status == "up" else 0
            metrics.append(Metric(
                name=SNMP_IF_OPER_STATUS,
                value=oper_value,
                unit="boolean",
                device_ip=device_ip,
                labels=labels.copy()
            ))

            counter = counters_by_index.get(normalized_if_index, {})

            def _append_counter(counter_key: str, metric_name: str, unit: str):
                value = counter.get(counter_key) if isinstance(counter, dict) else None
                if value is None:
                    return
                try:
                    metric_value = int(value)
                except (TypeError, ValueError):
                    return
                metrics.append(Metric(
                    name=metric_name,
                    value=metric_value,
                    unit=unit,
                    device_ip=device_ip,
                    labels=labels.copy()
                ))

            _append_counter("in_octets", SNMP_IF_IN_OCTETS, "bytes")
            _append_counter("out_octets", SNMP_IF_OUT_OCTETS, "bytes")
            _append_counter("in_errors", SNMP_IF_IN_ERRORS, "count")
            _append_counter("out_errors", SNMP_IF_OUT_ERRORS, "count")

        return metrics
