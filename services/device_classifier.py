import re
import json
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any

class DeviceType(Enum):
    FIREWALL = "Firewall"
    ROUTER = "Router"
    SWITCH = "Switch"
    ACCESS_POINT = "Access Point"
    SERVER = "Server"
    WORKSTATION = "Workstation"
    PRINTER = "Printer"
    CAMERA_IOT = "Camera/IoT"
    MOBILE = "Mobile Device"
    UNKNOWN = "Unknown"

class ConfidenceLevel(Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"

@dataclass
class DeviceSignals:
    """Input signals for classification"""
    ip_address: str
    mac_address: Optional[str] = None
    hostname: Optional[str] = None
    open_ports: List[int] = field(default_factory=list)
    snmp_sys_descr: Optional[str] = None
    snmp_sys_object_id: Optional[str] = None
    detected_services: List[str] = field(default_factory=list)
    manufacturer: Optional[str] = None # Added convenient field from existing MacLookup

@dataclass
class ClassificationResult:
    """Classification output"""
    device_type: DeviceType
    confidence: ConfidenceLevel
    score: int
    signals_used: List[Dict]
    reasoning: str
    alternative_types: List[Tuple[str, int]] = None

    def to_dict(self):
        return {
            "device_type": self.device_type.value,
            "confidence": self.confidence.value,
            "score": self.score,
            "signals_used": self.signals_used,
            "reasoning": self.reasoning,
            "alternative_types": self.alternative_types
        }

class DeviceClassifier:
    """
    Enterprise device classification engine using weighted multi-signal analysis.
    
    Signal Weights (evidence points):
    - MAC OUI: 45
    - Hostname: 35
    - Ports: 10
    - SNMP: +10 bonus (optional)
    - Behavior: 8 (Reserved for future traffic analysis)
    """
    
    # Weight constants
    WEIGHT_SNMP = 10
    WEIGHT_MAC = 45
    WEIGHT_PORT = 10
    WEIGHT_HOSTNAME = 35

    # Score normalization (maps evidence to a 50–100 confidence band)
    SCORE_BASE = 50
    SCORE_SCALE = 0.6
    
    # Confidence thresholds
    THRESHOLD_HIGH = 85
    THRESHOLD_MEDIUM = 70
    
    # MAC OUI / Manufacturer Database (Partial - relies more on scanner's MacLookup)
    # This map is used if MacLookup returns a raw vendor string we recognize
    VENDOR_MAP = {
        "Palo Alto": DeviceType.FIREWALL,
        "Fortinet": DeviceType.FIREWALL,
        "SonicWall": DeviceType.FIREWALL,
        "Cisco Systems, Inc": DeviceType.SWITCH, # Default bias, refined by ports/SNMP
        "Cisco": DeviceType.SWITCH, 
        "Juniper": DeviceType.ROUTER,
        "MikroTik": DeviceType.ROUTER,
        "Ubiquiti": DeviceType.ACCESS_POINT,
        "Ruckus": DeviceType.ACCESS_POINT,
        "Aruba": DeviceType.ACCESS_POINT,
        "HP": DeviceType.PRINTER, # Default bias for simple HP, contextual
        "Hewlett-Packard": DeviceType.PRINTER,
        "Canon": DeviceType.PRINTER,
        "Epson": DeviceType.PRINTER,
        "Brother": DeviceType.PRINTER,
        "Xerox": DeviceType.PRINTER,
        "Hikvision": DeviceType.CAMERA_IOT,
        "Dahua": DeviceType.CAMERA_IOT,
        "Axis": DeviceType.CAMERA_IOT,
        "Apple": DeviceType.MOBILE, # Bias towards mobile/likely laptop, can be overridden
        "Samsung": DeviceType.MOBILE,
        "Dell": DeviceType.WORKSTATION,
        "Lenovo": DeviceType.WORKSTATION,
        # NOTE: Synology, QNAP, VMware removed — servers must be manually classified
    }
    
    # SNMP Pattern Database (sysDescr)
    SNMP_PATTERNS = {
        DeviceType.FIREWALL: [
            r"cisco.*asa", r"palo alto", r"fortinet", r"fortigate", 
            r"pfsense", r"opnsense", r"checkpoint", r"sonicwall"
        ],
        DeviceType.ROUTER: [
            r"cisco.*ios", r"juniper.*junos", r"mikrotik.*routeros", r"router"
        ],
        DeviceType.SWITCH: [
            r"cisco.*catalyst", r"cisco.*nexus", r"hp.*switch", r"aruba.*switch",
            r"juniper.*ex", r"procurve", r"switch"
        ],
        DeviceType.ACCESS_POINT: [
            r"ubiquiti", r"unifi", r"cisco.*aironet", r"aruba.*ap", 
            r"access point", r"lap11", r"lap12"
        ],
        # NOTE: SERVER SNMP patterns removed — servers must be manually classified
        DeviceType.PRINTER: [
            r"printer", r"laserjet", r"inkjet", r"canon", r"epson", r"xerox"
        ],
    }
    
    # Port Fingerprints (Require specific combinations or individual high-value ports)
    PORT_FINGERPRINTS = {
        DeviceType.FIREWALL: [22, 443, 8443], # Generic, needs strengthening
        DeviceType.ROUTER: [179, 520], # BGP, RIP
        DeviceType.SWITCH: [161], # SNMP is key for switches usually
        DeviceType.ACCESS_POINT: [8080, 8443], # Unifi inform/admin
        # NOTE: SERVER port fingerprints removed — servers must be manually classified
        DeviceType.WORKSTATION: [445, 139], # SMB
        DeviceType.PRINTER: [9100, 631, 515],
        DeviceType.CAMERA_IOT: [554], # RTSP
    }
    
    # Hostname Patterns
    HOSTNAME_PATTERNS = {
        DeviceType.FIREWALL: [r"^(fw|firewall|asa|palo|fortinet)[\-_]?"],
        DeviceType.ROUTER: [r"^(router|rtr|gw|gateway)[\-_]?"],
        DeviceType.SWITCH: [r"^(switch|sw|core|dist|access)[\-_]?"],
        DeviceType.ACCESS_POINT: [r"^(ap|wifi|wlan)[\-_]?"],
        # NOTE: SERVER hostname patterns removed — servers must be manually classified
        DeviceType.WORKSTATION: [r"^(pc|ws|desktop|laptop)[\-_]?"],
        DeviceType.PRINTER: [r"^(printer|print|hp|canon|epson)[\-_]?"],
        DeviceType.CAMERA_IOT: [r"^(cam|camera|ipc|dvr|nvr)[\-_]?"],
        DeviceType.MOBILE: [r"^(iphone|ipad|android|galaxy)"]
    }

    CANONICAL_MAP = {
        "firewall": "firewall",
        "router": "router",
        "switch": "switch",
        "access point": "access_point",
        "access_point": "access_point",
        "server": "server",
        "workstation": "workstation",
        "printer": "printer",
        "camera/iot": "camera",
        "camera": "camera",
        "iot": "iot",
        "mobile device": "mobile",
        "mobile": "mobile",
        "unknown": "unknown",
        "network device": "unknown",
        "network-device": "unknown",
        "network_device": "unknown",
    }

    @staticmethod
    def normalize_device_type(value) -> str:
        if value is None:
            return "unknown"
        if isinstance(value, DeviceType):
            value = value.value
        raw = str(value).strip()
        if not raw:
            return "unknown"
        key = raw.lower().replace("-", " ").replace("_", " ").strip()
        return DeviceClassifier.CANONICAL_MAP.get(key, key.replace(" ", "_"))
    
    def classify(self, signals: DeviceSignals) -> ClassificationResult:
        """
        Classify device using multi-signal weighted scoring.
        """
        scores: Dict[DeviceType, int] = {}
        reasoning_map: Dict[DeviceType, List[str]] = {}
        
        # Helper to add score
        def add_score(dtype, amount, reason):
            if dtype not in scores:
                scores[dtype] = 0
                reasoning_map[dtype] = []
            scores[dtype] += amount
            reasoning_map[dtype].append(reason)

        # 1. SNMP Analysis (Weight: 60)
        # ----------------------------
        if signals.snmp_sys_descr:
            descr_lower = signals.snmp_sys_descr.lower()
            for dtype, patterns in self.SNMP_PATTERNS.items():
                for pattern in patterns:
                    if re.search(pattern, descr_lower):
                        add_score(dtype, self.WEIGHT_SNMP, f"SNMP sysDescr match: '{pattern}'")
                        break # One match per type is enough

        # 2. MAC Vendor Analysis (Weight: 25)
        # ----------------------------
        vendor = signals.manufacturer or ""
        # Look for partial matches in our mapped vendors
        # e.g. "Cisco Systems" matches "Cisco"
        for mapped_vendor, dtype in self.VENDOR_MAP.items():
            if mapped_vendor.lower() in vendor.lower():
                # Contextual tweaks
                if dtype == DeviceType.SWITCH and (22 in signals.open_ports and 161 not in signals.open_ports):
                    # Cisco without SNMP might be a router or AP if ports match, but default to switch if vendor is Cisco
                    pass
                add_score(dtype, self.WEIGHT_MAC, f"Manufacturer match: {mapped_vendor}")
                break

        # 3. Port Fingerprinting (Weight: 15)
        # ----------------------------
        ports = set(signals.open_ports)
        if ports:
            # NOTE: Database port → Server scoring removed — servers must be manually classified
            
            # Printer ports
            if any(p in ports for p in self.PORT_FINGERPRINTS[DeviceType.PRINTER]):
                add_score(DeviceType.PRINTER, self.WEIGHT_PORT, "Open printing ports")
            
            # RTSP -> Camera
            if 554 in ports:
                add_score(DeviceType.CAMERA_IOT, self.WEIGHT_PORT, "RTSP port 554 open")
            
            # Windows SMB -> Workstation only (server scoring removed)
            if 445 in ports:
                add_score(DeviceType.WORKSTATION, 10, "SMB port 445 open")
            
            # Routing protocols -> Router
            if 179 in ports or 520 in ports:
                add_score(DeviceType.ROUTER, self.WEIGHT_PORT, "Routing protocol ports open")

        # 4. Hostname Analysis (Weight: 10)
        # ----------------------------
        if signals.hostname and signals.hostname != "Unknown":
            name_lower = signals.hostname.lower()
            for dtype, patterns in self.HOSTNAME_PATTERNS.items():
                for pattern in patterns:
                    if re.search(pattern, name_lower):
                        add_score(dtype, self.WEIGHT_HOSTNAME, f"Hostname pattern match: '{pattern}'")
                        break

        # 5. Specialized Logic / Tie Breakers
        # ----------------------------
        # No ports open and Mobile vendor? High confidence mobile.
        if (not ports) and (DeviceType.MOBILE in scores) and (scores[DeviceType.MOBILE] >= self.WEIGHT_MAC):
             add_score(DeviceType.MOBILE, 10, "No open ports typical for mobile")
        
        # If no scores yet, return Unknown (baseline confidence)
        if not scores:
            return ClassificationResult(
                device_type=DeviceType.UNKNOWN,
                confidence=ConfidenceLevel.LOW,
                score=self.SCORE_BASE,
                signals_used=[],
                reasoning="Insufficient signals for classification.",
                alternative_types=[]
            )

        # Get best match
        sorted_types = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        best_type, best_score = sorted_types[0]
        # Normalize evidence score to 50–100 range; SNMP is a bonus only.
        final_score = int(round(min(100, self.SCORE_BASE + (best_score * self.SCORE_SCALE))))
        
        # Determine confidence
        if final_score >= self.THRESHOLD_HIGH:
            confidence = ConfidenceLevel.HIGH
        elif final_score >= self.THRESHOLD_MEDIUM:
            confidence = ConfidenceLevel.MEDIUM
        else:
            confidence = ConfidenceLevel.LOW
        
        # Format reasoning
        reasoning_list = reasoning_map.get(best_type, [])
        reasoning_str = "; ".join(reasoning_list)
        
        # Alternatives
        alternatives = []
        if len(sorted_types) > 1:
            alternatives = [(t.value, s) for t, s in sorted_types[1:3]]

        signals_summary = []
        if signals.manufacturer: signals_summary.append({"source": "Vendor", "value": signals.manufacturer})
        if signals.open_ports: signals_summary.append({"source": "Ports", "value": str(signals.open_ports)})
        if signals.snmp_sys_descr: signals_summary.append({"source": "SNMP", "value": "sysDescr matched"})
        if signals.hostname: signals_summary.append({"source": "Hostname", "value": signals.hostname})

        return ClassificationResult(
            device_type=best_type,
            confidence=confidence,
            score=final_score,
            signals_used=signals_summary,
            reasoning=reasoning_str,
            alternative_types=alternatives
        )
