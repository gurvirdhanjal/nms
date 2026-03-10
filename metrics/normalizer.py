from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# Standardized Metric Constants
DEVICE_AVAILABILITY = "device_availability"
NETWORK_LATENCY_MS = "network_latency_ms"
PACKET_LOSS_PERCENT = "packet_loss_percent"
NETWORK_JITTER_MS = "network_jitter_ms"
OPEN_PORTS_COUNT = "open_ports_count"

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
