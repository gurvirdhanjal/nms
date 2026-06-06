"""
SNMP Polling Service for Network Monitoring System.
Uses pysnmp to query SNMP-enabled devices for system info and interface statistics.

Phase 1 Enhancements:
  - bulkCmd for table walks (10x fewer round-trips than nextCmd)
  - Typed error classification (timeout, auth, OID, generic)
  - Structured error returns with error_code field
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

# pysnmp imports
try:
    from pysnmp.hlapi import (
        SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
        ObjectType, ObjectIdentity, getCmd, nextCmd, bulkCmd
    )
    PYSNMP_AVAILABLE = True
except ImportError:
    PYSNMP_AVAILABLE = False
    log.warning("pysnmp not installed. SNMP polling disabled.")


# ─────────────────────────────────────────────
# Typed SNMP Error Classes
# ─────────────────────────────────────────────
class SnmpError(Exception):
    """Base class for all SNMP polling errors."""
    error_code = 'SNMP_ERROR'

class SnmpTimeoutError(SnmpError):
    """Device did not respond within the configured timeout."""
    error_code = 'SNMP_TIMEOUT'

class SnmpAuthError(SnmpError):
    """Authentication failure (wrong community string or USM credentials)."""
    error_code = 'SNMP_AUTH_FAILURE'

class SnmpOidNotFoundError(SnmpError):
    """Requested OID is not available on this device."""
    error_code = 'SNMP_OID_NOT_FOUND'

class SnmpVersionMismatchError(SnmpError):
    """Device rejected the SNMP version used."""
    error_code = 'SNMP_VERSION_MISMATCH'


# ─────────────────────────────────────────────
# Error Classifier
# ─────────────────────────────────────────────
def classify_snmp_error(error_indication, error_status=None, error_index=None):
    """
    Convert pysnmp error indicators into typed SnmpError exceptions.
    
    Args:
        error_indication: pysnmp errorIndication (transport-level)
        error_status: pysnmp errorStatus (protocol-level)
        error_index: pysnmp errorIndex
    
    Returns:
        SnmpError subclass instance
    """
    if error_indication:
        err_str = str(error_indication).lower()
        if 'timeout' in err_str or 'request timed out' in err_str:
            return SnmpTimeoutError(f"SNMP timeout: {error_indication}")
        elif 'unknown' in err_str and 'name' in err_str:
            return SnmpAuthError(f"SNMP auth failure: {error_indication}")
        elif 'unsupported' in err_str and 'version' in err_str:
            return SnmpVersionMismatchError(f"SNMP version mismatch: {error_indication}")
        else:
            return SnmpError(f"SNMP error: {error_indication}")
    
    if error_status:
        status_val = int(error_status)
        status_str = str(error_status.prettyPrint()).lower() if hasattr(error_status, 'prettyPrint') else str(error_status).lower()
        
        # noSuchName (2), noSuchObject, noSuchInstance
        if status_val == 2 or 'nosuch' in status_str:
            return SnmpOidNotFoundError(
                f"OID not found: {status_str} at index {error_index}"
            )
        # authorizationError (16)
        elif status_val == 16 or 'authorization' in status_str:
            return SnmpAuthError(f"SNMP authorization error: {status_str}")
        else:
            return SnmpError(f"SNMP protocol error: {status_str} at index {error_index}")
    
    return None


# Common SNMP OIDs
class SnmpOids:
    # System MIB (RFC 1213)
    SYS_DESCR = '1.3.6.1.2.1.1.1.0'
    SYS_OBJECT_ID = '1.3.6.1.2.1.1.2.0'
    SYS_UPTIME = '1.3.6.1.2.1.1.3.0'
    SYS_CONTACT = '1.3.6.1.2.1.1.4.0'
    SYS_NAME = '1.3.6.1.2.1.1.5.0'
    SYS_LOCATION = '1.3.6.1.2.1.1.6.0'
    
    # Interface MIB (RFC 2863)
    IF_NUMBER = '1.3.6.1.2.1.2.1.0'
    IF_TABLE = '1.3.6.1.2.1.2.2'
    IF_INDEX = '1.3.6.1.2.1.2.2.1.1'
    IF_DESCR = '1.3.6.1.2.1.2.2.1.2'
    IF_TYPE = '1.3.6.1.2.1.2.2.1.3'
    IF_SPEED = '1.3.6.1.2.1.2.2.1.5'
    IF_PHYS_ADDRESS = '1.3.6.1.2.1.2.2.1.6'
    IF_ADMIN_STATUS = '1.3.6.1.2.1.2.2.1.7'
    IF_OPER_STATUS = '1.3.6.1.2.1.2.2.1.8'
    IF_IN_OCTETS = '1.3.6.1.2.1.2.2.1.10'
    IF_OUT_OCTETS = '1.3.6.1.2.1.2.2.1.16'
    IF_IN_ERRORS = '1.3.6.1.2.1.2.2.1.14'
    IF_OUT_ERRORS = '1.3.6.1.2.1.2.2.1.20'
    
    # IF-MIB extensions (for high-speed interfaces)
    IF_X_TABLE = '1.3.6.1.2.1.31.1.1'
    IF_NAME = '1.3.6.1.2.1.31.1.1.1.1'
    IF_HIGH_SPEED = '1.3.6.1.2.1.31.1.1.1.15'
    IF_ALIAS = '1.3.6.1.2.1.31.1.1.1.18'
    IF_HC_IN_OCTETS = '1.3.6.1.2.1.31.1.1.1.6'
    IF_HC_OUT_OCTETS = '1.3.6.1.2.1.31.1.1.1.10'

    # Printer MIB (RFC 3805) — Consumables & Status
    HR_PRINTER_STATUS = '1.3.6.1.2.1.25.3.5.1.1'          # hrPrinterStatus table
    PRT_MARKER_SUPPLIES_DESC = '1.3.6.1.2.1.43.11.1.1.6'  # prtMarkerSuppliesDescription
    PRT_MARKER_SUPPLIES_MAX = '1.3.6.1.2.1.43.11.1.1.8'   # prtMarkerSuppliesMaxCapacity
    PRT_MARKER_SUPPLIES_LEVEL = '1.3.6.1.2.1.43.11.1.1.9' # prtMarkerSuppliesLevel
    PRT_MARKER_LIFE_COUNT = '1.3.6.1.2.1.43.10.2.1.4'     # prtMarkerLifeCount (total pages)
    PRT_INPUT_DESC = '1.3.6.1.2.1.43.8.2.1.13'             # prtInputDescription (tray name)
    PRT_INPUT_STATUS = '1.3.6.1.2.1.43.8.2.1.11'           # prtInputStatus
    PRT_INPUT_MAX_CAPACITY = '1.3.6.1.2.1.43.8.2.1.9'      # prtInputMaxCapacity
    PRT_INPUT_CURRENT_LEVEL = '1.3.6.1.2.1.43.8.2.1.10'    # prtInputCurrentLevel


# Default GETBULK max-repetitions (rows per PDU response)
BULK_MAX_REPETITIONS = 25


class SnmpService:
    """
    Service for polling SNMP-enabled devices.
    Provides methods to retrieve system info, interface list, and traffic counters.
    
    Uses bulkCmd (GETBULK) for table walks to minimize round-trips.
    Uses getCmd (GET) for scalar OIDs.
    Raises typed SnmpError subclasses for classified error handling.
    """
    
    def __init__(self, timeout: int = 2, retries: int = 1):
        self.timeout = timeout
        self.retries = retries
        self._executor = ThreadPoolExecutor(max_workers=10)
        self._engine = SnmpEngine() if PYSNMP_AVAILABLE else None
    
    def _get_community_data(self, community: str, version: str = '2c') -> Any:
        """Create CommunityData object for SNMP v1/v2c."""
        if version == '1':
            return CommunityData(community, mpModel=0)
        else:  # v2c
            return CommunityData(community, mpModel=1)
    
    def _get_transport_target(self, host: str, port: int = 161) -> Any:
        """Create UDP transport target."""
        return UdpTransportTarget(
            (host, port),
            timeout=self.timeout,
            retries=self.retries
        )

    def _use_bulk(self, version: str) -> bool:
        """Check if GETBULK is available (v2c+ only, v1 doesn't support it)."""
        return version != '1'

    # ─────────────────────────────────────────────
    # Scalar: System Info (uses GET — unchanged)
    # ─────────────────────────────────────────────
    def get_system_info(self, host: str, community: str = 'public', 
                        version: str = '2c', port: int = 161) -> Dict[str, Any]:
        """
        Get basic system information from a device.
        Returns dict with sysDescr, sysName, sysUpTime, sysLocation, sysContact.
        
        Raises:
            SnmpTimeoutError: Device did not respond
            SnmpAuthError: Wrong community string
            SnmpError: Other SNMP errors
        """
        if not PYSNMP_AVAILABLE:
            return {'error': 'pysnmp not installed', 'error_code': 'SNMP_NOT_INSTALLED'}
        
        oids = [
            ObjectType(ObjectIdentity(SnmpOids.SYS_DESCR)),
            ObjectType(ObjectIdentity(SnmpOids.SYS_NAME)),
            ObjectType(ObjectIdentity(SnmpOids.SYS_UPTIME)),
            ObjectType(ObjectIdentity(SnmpOids.SYS_LOCATION)),
            ObjectType(ObjectIdentity(SnmpOids.SYS_CONTACT)),
        ]
        
        try:
            error_indication, error_status, error_index, var_binds = next(
                getCmd(
                    self._engine,
                    self._get_community_data(community, version),
                    self._get_transport_target(host, port),
                    ContextData(),
                    *oids
                )
            )
            
            # Classify and raise typed errors
            err = classify_snmp_error(error_indication, error_status, error_index)
            if err:
                return {
                    'error': str(err),
                    'error_code': err.error_code,
                    'host': host
                }
            
            result = {}
            for var_bind in var_binds:
                oid, value = var_bind
                oid_str = str(oid)
                
                if SnmpOids.SYS_DESCR in oid_str:
                    result['sys_descr'] = str(value)
                elif SnmpOids.SYS_NAME in oid_str:
                    result['sys_name'] = str(value)
                elif SnmpOids.SYS_UPTIME in oid_str:
                    # Convert timeticks (1/100 sec) to seconds
                    result['sys_uptime_seconds'] = int(value) / 100
                elif SnmpOids.SYS_LOCATION in oid_str:
                    result['sys_location'] = str(value)
                elif SnmpOids.SYS_CONTACT in oid_str:
                    result['sys_contact'] = str(value)
            
            result['polled_at'] = datetime.utcnow().isoformat()
            return result
                
        except Exception as e:
            log.error(f"[SNMP] System info error for {host}: {e}")
            return {'error': str(e), 'error_code': 'SNMP_ERROR', 'host': host}

    # ─────────────────────────────────────────────
    # Table Walk: Interfaces (now uses GETBULK)
    # ─────────────────────────────────────────────
    def get_interfaces(self, host: str, community: str = 'public',
                       version: str = '2c', port: int = 161) -> List[Dict[str, Any]]:
        """
        Get list of interfaces with their properties.
        Uses GETBULK (bulkCmd) for v2c+ to minimize round-trips.
        Falls back to nextCmd for v1.
        """
        if not PYSNMP_AVAILABLE:
            return []
        
        interfaces = {}
        use_bulk = self._use_bulk(version)
        
        oid_objects = [
            ObjectType(ObjectIdentity(SnmpOids.IF_INDEX)),
            ObjectType(ObjectIdentity(SnmpOids.IF_DESCR)),
            ObjectType(ObjectIdentity(SnmpOids.IF_TYPE)),
            ObjectType(ObjectIdentity(SnmpOids.IF_SPEED)),
            ObjectType(ObjectIdentity(SnmpOids.IF_PHYS_ADDRESS)),
            ObjectType(ObjectIdentity(SnmpOids.IF_ADMIN_STATUS)),
            ObjectType(ObjectIdentity(SnmpOids.IF_OPER_STATUS)),
        ]
        
        try:
            if use_bulk:
                walk_iter = bulkCmd(
                    self._engine,
                    self._get_community_data(community, version),
                    self._get_transport_target(host, port),
                    ContextData(),
                    0,  # nonRepeaters (scalar OIDs to GET first — none)
                    BULK_MAX_REPETITIONS,  # maxRepetitions per column
                    *oid_objects,
                    lexicographicMode=False
                )
            else:
                walk_iter = nextCmd(
                    self._engine,
                    self._get_community_data(community, version),
                    self._get_transport_target(host, port),
                    ContextData(),
                    *oid_objects,
                    lexicographicMode=False
                )
            
            for (error_indication, error_status, error_index, var_binds) in walk_iter:
                err = classify_snmp_error(error_indication, error_status, error_index)
                if err:
                    log.warning(f"[SNMP] Interface walk error for {host}: {err}")
                    break
                
                if_data = {}
                if_index = None
                
                for var_bind in var_binds:
                    oid, value = var_bind
                    oid_str = str(oid)
                    
                    if '.1.3.6.1.2.1.2.2.1.1.' in oid_str:  # ifIndex
                        if_index = int(value)
                        if_data['if_index'] = if_index
                    elif '.1.3.6.1.2.1.2.2.1.2.' in oid_str:  # ifDescr
                        if_data['name'] = str(value)
                    elif '.1.3.6.1.2.1.2.2.1.3.' in oid_str:  # ifType
                        if_data['if_type'] = int(value)
                    elif '.1.3.6.1.2.1.2.2.1.5.' in oid_str:  # ifSpeed
                        if_data['speed_bps'] = int(value)
                    elif '.1.3.6.1.2.1.2.2.1.6.' in oid_str:  # ifPhysAddress
                        # Convert to MAC address string
                        mac = value.prettyPrint() if hasattr(value, 'prettyPrint') else str(value)
                        if_data['mac_address'] = mac
                    elif '.1.3.6.1.2.1.2.2.1.7.' in oid_str:  # ifAdminStatus
                        status_map = {1: 'up', 2: 'down', 3: 'testing'}
                        if_data['admin_status'] = status_map.get(int(value), 'unknown')
                    elif '.1.3.6.1.2.1.2.2.1.8.' in oid_str:  # ifOperStatus
                        status_map = {1: 'up', 2: 'down', 3: 'testing', 4: 'unknown', 5: 'dormant'}
                        if_data['oper_status'] = status_map.get(int(value), 'unknown')
                
                if if_index is not None:
                    interfaces[if_index] = if_data
                    
        except Exception as e:
            log.error(f"[SNMP] Interface walk error for {host}: {e}")
        
        return list(interfaces.values())

    # ─────────────────────────────────────────────
    # Table Walk: Counters (now uses GETBULK)
    # ─────────────────────────────────────────────
    def get_interface_counters(self, host: str, community: str = 'public',
                                version: str = '2c', port: int = 161) -> List[Dict[str, Any]]:
        """
        Get traffic counters for all interfaces.
        Uses GETBULK (bulkCmd) for v2c+ to minimize round-trips.
        Falls back to nextCmd for v1.
        """
        if not PYSNMP_AVAILABLE:
            return []
        
        counters = {}
        use_bulk = self._use_bulk(version)
        
        oid_objects = [
            ObjectType(ObjectIdentity(SnmpOids.IF_INDEX)),
            ObjectType(ObjectIdentity(SnmpOids.IF_IN_OCTETS)),
            ObjectType(ObjectIdentity(SnmpOids.IF_OUT_OCTETS)),
            ObjectType(ObjectIdentity(SnmpOids.IF_IN_ERRORS)),
            ObjectType(ObjectIdentity(SnmpOids.IF_OUT_ERRORS)),
        ]
        
        try:
            if use_bulk:
                walk_iter = bulkCmd(
                    self._engine,
                    self._get_community_data(community, version),
                    self._get_transport_target(host, port),
                    ContextData(),
                    0,  # nonRepeaters
                    BULK_MAX_REPETITIONS,
                    *oid_objects,
                    lexicographicMode=False
                )
            else:
                walk_iter = nextCmd(
                    self._engine,
                    self._get_community_data(community, version),
                    self._get_transport_target(host, port),
                    ContextData(),
                    *oid_objects,
                    lexicographicMode=False
                )
            
            for (error_indication, error_status, error_index, var_binds) in walk_iter:
                err = classify_snmp_error(error_indication, error_status, error_index)
                if err:
                    log.warning(f"[SNMP] Counter walk error for {host}: {err}")
                    break
                
                counter_data = {'timestamp': datetime.utcnow().isoformat()}
                if_index = None
                
                for var_bind in var_binds:
                    oid, value = var_bind
                    oid_str = str(oid)
                    
                    if '.1.3.6.1.2.1.2.2.1.1.' in oid_str:
                        if_index = int(value)
                        counter_data['if_index'] = if_index
                    elif '.1.3.6.1.2.1.2.2.1.10.' in oid_str:
                        counter_data['in_octets'] = int(value)
                    elif '.1.3.6.1.2.1.2.2.1.16.' in oid_str:
                        counter_data['out_octets'] = int(value)
                    elif '.1.3.6.1.2.1.2.2.1.14.' in oid_str:
                        counter_data['in_errors'] = int(value)
                    elif '.1.3.6.1.2.1.2.2.1.20.' in oid_str:
                        counter_data['out_errors'] = int(value)
                
                if if_index is not None:
                    counters[if_index] = counter_data
                    
        except Exception as e:
            log.error(f"[SNMP] Counter walk error for {host}: {e}")
        
        return list(counters.values())
    
    async def poll_device_async(self, host: str, community: str = 'public',
                                 version: str = '2c', port: int = 161) -> Dict[str, Any]:
        """
        Async wrapper to poll a device for all SNMP data.
        Runs blocking SNMP calls in thread pool.
        """
        loop = asyncio.get_event_loop()
        
        # Run blocking calls in executor
        system_info = await loop.run_in_executor(
            self._executor,
            lambda: self.get_system_info(host, community, version, port)
        )
        
        interfaces = await loop.run_in_executor(
            self._executor,
            lambda: self.get_interfaces(host, community, version, port)
        )
        
        counters = await loop.run_in_executor(
            self._executor,
            lambda: self.get_interface_counters(host, community, version, port)
        )
        
        return {
            'host': host,
            'system': system_info,
            'interfaces': interfaces,
            'counters': counters,
            'polled_at': datetime.utcnow().isoformat()
        }

    # ─────────────────────────────────────────────
    # Server Health via HOST-RESOURCES-MIB (GETBULK)
    # ─────────────────────────────────────────────
    def get_server_health_snmp(self, host: str, community: str = 'public',
                               version: str = '2c', port: int = 161) -> Dict[str, Any]:
        """
        Get server health metrics (CPU, RAM, Disk) via HOST-RESOURCES-MIB.
        Returns dict with cpu_usage, memory_usage, disk_usage.
        
        Uses bulkCmd for table walks (CPU cores, storage entries).
        """
        if not PYSNMP_AVAILABLE:
            return {}

        metrics = {}
        use_bulk = self._use_bulk(version)
        
        # OIDs
        HR_PROCESSOR_LOAD = '1.3.6.1.2.1.25.3.3.1.2' # Table of load per core
        
        HR_STORAGE_TYPE = '1.3.6.1.2.1.25.2.3.1.2'
        HR_STORAGE_DESCR = '1.3.6.1.2.1.25.2.3.1.3'
        HR_STORAGE_SIZE = '1.3.6.1.2.1.25.2.3.1.5'
        HR_STORAGE_USED = '1.3.6.1.2.1.25.2.3.1.6'
        HR_STORAGE_UNITS = '1.3.6.1.2.1.25.2.3.1.4'
        
        # Storage Types
        OID_RAM = '1.3.6.1.2.1.25.2.1.2'
        OID_FIXED_DISK = '1.3.6.1.2.1.25.2.1.4'
        
        try:
            # 1. CPU Load
            cpu_loads = []
            cpu_oids = [ObjectType(ObjectIdentity(HR_PROCESSOR_LOAD))]
            
            if use_bulk:
                cpu_iter = bulkCmd(
                    self._engine,
                    self._get_community_data(community, version),
                    self._get_transport_target(host, port),
                    ContextData(),
                    0, BULK_MAX_REPETITIONS,
                    *cpu_oids,
                    lexicographicMode=False
                )
            else:
                cpu_iter = nextCmd(
                    self._engine,
                    self._get_community_data(community, version),
                    self._get_transport_target(host, port),
                    ContextData(),
                    *cpu_oids,
                    lexicographicMode=False
                )
            
            for (error_indication, error_status, error_index, var_binds) in cpu_iter:
                err = classify_snmp_error(error_indication, error_status, error_index)
                if err:
                    log.debug(f"[SNMP] CPU walk ended for {host}: {err}")
                    break
                for var_bind in var_binds:
                    cpu_loads.append(int(var_bind[1]))
            
            if cpu_loads:
                metrics['cpu_usage'] = round(sum(cpu_loads) / len(cpu_loads), 1)

            # 2. Storage (RAM & Disk)
            storage_oids = [
                ObjectType(ObjectIdentity(HR_STORAGE_TYPE)),
                ObjectType(ObjectIdentity(HR_STORAGE_DESCR)),
                ObjectType(ObjectIdentity(HR_STORAGE_UNITS)),
                ObjectType(ObjectIdentity(HR_STORAGE_SIZE)),
                ObjectType(ObjectIdentity(HR_STORAGE_USED)),
            ]
            
            ram_total = 0
            ram_used = 0
            disk_stats = []
            
            if use_bulk:
                storage_iter = bulkCmd(
                    self._engine,
                    self._get_community_data(community, version),
                    self._get_transport_target(host, port),
                    ContextData(),
                    0, BULK_MAX_REPETITIONS,
                    *storage_oids,
                    lexicographicMode=False
                )
            else:
                storage_iter = nextCmd(
                    self._engine,
                    self._get_community_data(community, version),
                    self._get_transport_target(host, port),
                    ContextData(),
                    *storage_oids,
                    lexicographicMode=False
                )
            
            for (error_indication, error_status, error_index, var_binds) in storage_iter:
                err = classify_snmp_error(error_indication, error_status, error_index)
                if err:
                    log.debug(f"[SNMP] Storage walk ended for {host}: {err}")
                    break
                
                # Unwrap the 5 columns
                if len(var_binds) < 5: continue
                
                s_type = str(var_binds[0][1])
                s_descr = str(var_binds[1][1])
                s_units = int(var_binds[2][1])
                s_size = int(var_binds[3][1])
                s_used = int(var_binds[4][1])
                
                if s_size <= 0: continue
                
                if OID_RAM in s_type: # RAM
                    ram_total += s_size * s_units
                    ram_used += s_used * s_units
                elif OID_FIXED_DISK in s_type: # Disk
                    d_total = s_size * s_units
                    d_used = s_used * s_units
                    p = round((d_used / d_total) * 100, 1) if d_total > 0 else 0
                    disk_stats.append(p)
            
            if ram_total > 0:
                metrics['memory_usage'] = round((ram_used / ram_total) * 100, 1)
            
            if disk_stats:
                # Take max across all disks (conservative for alerting)
                metrics['disk_usage'] = max(disk_stats)
                
            return metrics

        except Exception as e:
            log.error(f"[SNMP] Health check error for {host}: {e}")
            return metrics

    # ─────────────────────────────────────────────
    # Printer Metrics via Printer-MIB (Phase 1 MVP)
    # ─────────────────────────────────────────────
    def get_printer_metrics(self, host: str, community: str = 'public',
                            version: str = '2c', port: int = 161) -> Dict[str, Any]:
        """
        Get printer health metrics: supplies (toner/ink), page counts, tray status.
        Uses GETBULK for table walks.  Returns structured dict for PrinterMetrics model.
        """
        if not PYSNMP_AVAILABLE:
            return {'error': 'pysnmp not installed', 'error_code': 'SNMP_NOT_INSTALLED'}

        result = {
            'status': None, 'status_code': None,
            'toner_black': None, 'toner_cyan': None,
            'toner_magenta': None, 'toner_yellow': None,
            'page_count_total': None, 'page_count_color': None, 'page_count_bw': None,
            'paper_tray_status': [], 'job_queue_length': None,
        }
        use_bulk = self._use_bulk(version)

        # ── 1. Printer Status (scalar GET) ──
        try:
            err_i, err_s, err_idx, var_binds = next(
                getCmd(
                    self._engine,
                    self._get_community_data(community, version),
                    self._get_transport_target(host, port),
                    ContextData(),
                    ObjectType(ObjectIdentity(SnmpOids.HR_PRINTER_STATUS)),
                )
            )
            if not classify_snmp_error(err_i, err_s, err_idx):
                status_val = int(var_binds[0][1])
                STATUS_MAP = {1: 'other', 2: 'unknown', 3: 'idle', 4: 'printing', 5: 'warmup'}
                result['status_code'] = status_val
                result['status'] = STATUS_MAP.get(status_val, 'unknown')
        except Exception as e:
            log.debug(f"[SNMP] Printer status unavailable for {host}: {e}")

        # ── 2. Supplies (toner / ink) ──
        try:
            supply_oids = [
                ObjectType(ObjectIdentity(SnmpOids.PRT_MARKER_SUPPLIES_DESC)),
                ObjectType(ObjectIdentity(SnmpOids.PRT_MARKER_SUPPLIES_MAX)),
                ObjectType(ObjectIdentity(SnmpOids.PRT_MARKER_SUPPLIES_LEVEL)),
            ]
            if use_bulk:
                supply_iter = bulkCmd(
                    self._engine, self._get_community_data(community, version),
                    self._get_transport_target(host, port), ContextData(),
                    0, BULK_MAX_REPETITIONS, *supply_oids, lexicographicMode=False
                )
            else:
                supply_iter = nextCmd(
                    self._engine, self._get_community_data(community, version),
                    self._get_transport_target(host, port), ContextData(),
                    *supply_oids, lexicographicMode=False
                )

            for (err_i, err_s, err_idx, var_binds) in supply_iter:
                if classify_snmp_error(err_i, err_s, err_idx):
                    break
                if len(var_binds) < 3:
                    continue
                desc = str(var_binds[0][1]).lower()
                max_cap = int(var_binds[1][1])
                cur_level = int(var_binds[2][1])
                pct = round((cur_level / max_cap) * 100) if max_cap > 0 else (0 if cur_level == 0 else -1)

                if 'black' in desc or 'bk' in desc:
                    result['toner_black'] = pct
                elif 'cyan' in desc:
                    result['toner_cyan'] = pct
                elif 'magenta' in desc:
                    result['toner_magenta'] = pct
                elif 'yellow' in desc:
                    result['toner_yellow'] = pct
        except Exception as e:
            log.debug(f"[SNMP] Supplies walk failed for {host}: {e}")

        # ── 3. Page Counts ──
        try:
            page_oids = [ObjectType(ObjectIdentity(SnmpOids.PRT_MARKER_LIFE_COUNT))]
            if use_bulk:
                page_iter = bulkCmd(
                    self._engine, self._get_community_data(community, version),
                    self._get_transport_target(host, port), ContextData(),
                    0, BULK_MAX_REPETITIONS, *page_oids, lexicographicMode=False
                )
            else:
                page_iter = nextCmd(
                    self._engine, self._get_community_data(community, version),
                    self._get_transport_target(host, port), ContextData(),
                    *page_oids, lexicographicMode=False
                )
            counts = []
            for (err_i, err_s, err_idx, var_binds) in page_iter:
                if classify_snmp_error(err_i, err_s, err_idx):
                    break
                counts.append(int(var_binds[0][1]))
            if counts:
                result['page_count_total'] = counts[0]  # First marker is usually total
        except Exception as e:
            log.debug(f"[SNMP] Page count walk failed for {host}: {e}")

        # ── 4. Paper Tray Status ──
        try:
            tray_oids = [
                ObjectType(ObjectIdentity(SnmpOids.PRT_INPUT_DESC)),
                ObjectType(ObjectIdentity(SnmpOids.PRT_INPUT_MAX_CAPACITY)),
                ObjectType(ObjectIdentity(SnmpOids.PRT_INPUT_CURRENT_LEVEL)),
            ]
            if use_bulk:
                tray_iter = bulkCmd(
                    self._engine, self._get_community_data(community, version),
                    self._get_transport_target(host, port), ContextData(),
                    0, BULK_MAX_REPETITIONS, *tray_oids, lexicographicMode=False
                )
            else:
                tray_iter = nextCmd(
                    self._engine, self._get_community_data(community, version),
                    self._get_transport_target(host, port), ContextData(),
                    *tray_oids, lexicographicMode=False
                )
            trays = []
            for (err_i, err_s, err_idx, var_binds) in tray_iter:
                if classify_snmp_error(err_i, err_s, err_idx):
                    break
                if len(var_binds) < 3:
                    continue
                t_desc = str(var_binds[0][1])
                t_max = int(var_binds[1][1])
                t_cur = int(var_binds[2][1])
                t_pct = round((t_cur / t_max) * 100) if t_max > 0 else None
                trays.append({'name': t_desc, 'max_capacity': t_max, 'current_level': t_cur, 'capacity_pct': t_pct})
            result['paper_tray_status'] = trays
        except Exception as e:
            log.debug(f"[SNMP] Tray walk failed for {host}: {e}")

        result['polled_at'] = datetime.utcnow().isoformat()
        return result


    # ─────────────────────────────────────────────
    # Printer MIB (RFC 3805) - Supplies
    # ─────────────────────────────────────────────
    def get_printer_supplies(self, host: str, community: str = 'public',
                             version: str = '2c', port: int = 161) -> List[Dict[str, Any]]:
        """
        Get printer supply levels (toner, ink, drums, waste) via Printer-MIB.
        Returns list of dicts with: name, type, level, max_capacity, unit, percentage.
        """
        if not PYSNMP_AVAILABLE:
            return []
            
        supplies = {}
        use_bulk = self._use_bulk(version)
        
        # OIDs from Printer-MIB (1.3.6.1.2.1.43)
        # prtMarkerSuppliesTable = 1.3.6.1.2.1.43.11.1
        OID_SUPPLY_INDEX = '1.3.6.1.2.1.43.11.1.1.1'
        OID_SUPPLY_DESCR = '1.3.6.1.2.1.43.11.1.1.6'
        OID_SUPPLY_LEVEL = '1.3.6.1.2.1.43.11.1.1.9'
        OID_SUPPLY_MAX = '1.3.6.1.2.1.43.11.1.1.8'
        OID_SUPPLY_UNIT = '1.3.6.1.2.1.43.11.1.1.7' # 19=percent, others=units
        OID_SUPPLY_TYPE = '1.3.6.1.2.1.43.11.1.1.5' # 3=toner, 4=wasteToner...
        
        oids = [
            ObjectType(ObjectIdentity(OID_SUPPLY_INDEX)),
            ObjectType(ObjectIdentity(OID_SUPPLY_DESCR)),
            ObjectType(ObjectIdentity(OID_SUPPLY_LEVEL)),
            ObjectType(ObjectIdentity(OID_SUPPLY_MAX)),
            ObjectType(ObjectIdentity(OID_SUPPLY_UNIT)),
            ObjectType(ObjectIdentity(OID_SUPPLY_TYPE)),
        ]
        
        try:
            if use_bulk:
                walk_iter = bulkCmd(
                    self._engine,
                    self._get_community_data(community, version),
                    self._get_transport_target(host, port),
                    ContextData(),
                    0, BULK_MAX_REPETITIONS,
                    *oids,
                    lexicographicMode=False
                )
            else:
                walk_iter = nextCmd(
                    self._engine,
                    self._get_community_data(community, version),
                    self._get_transport_target(host, port),
                    ContextData(),
                    *oids,
                    lexicographicMode=False
                )
                
            for (error_indication, error_status, error_index, var_binds) in walk_iter:
                err = classify_snmp_error(error_indication, error_status, error_index)
                if err:
                    log.debug(f"[SNMP] Printer supply walk error for {host}: {err}")
                    break
                    
                # We expect rows of data. 
                # Note: Not all printers return all columns in one go if max-repetitions is small, 
                # but with bulkCmd and small row width it usually works. 
                # However, pysnmp aligns varBinds.
                
                # Parse row
                idx = None
                data = {}
                
                for var_bind in var_binds:
                    oid_str = str(var_bind[0])
                    val = var_bind[1]
                    
                    if OID_SUPPLY_INDEX in oid_str:
                        idx = int(val)
                        data['id'] = idx
                    elif OID_SUPPLY_DESCR in oid_str:
                        data['name'] = str(val)
                    elif OID_SUPPLY_LEVEL in oid_str:
                        data['level'] = int(val)
                    elif OID_SUPPLY_MAX in oid_str:
                        data['max'] = int(val)
                    elif OID_SUPPLY_UNIT in oid_str:
                        data['unit_code'] = int(val)
                    elif OID_SUPPLY_TYPE in oid_str:
                        type_map = {
                            3: 'toner', 4: 'waste_toner', 21: 'ink', 
                            15: 'fuser', 18: 'drum'
                        }
                        data['type'] = type_map.get(int(val), 'other')

                if idx is not None:
                    # Merge if we already have partial data for this index (unlikely with this logic but safe)
                    if idx in supplies:
                        supplies[idx].update(data)
                    else:
                        supplies[idx] = data

            # Calculate percentages
            results = []
            for item in supplies.values():
                # Filter out junk (some printers report undefined supplies)
                if not item.get('name'): continue
                
                level = item.get('level', 0)
                max_cap = item.get('max', 0)
                
                # RFC 3805: 
                # level = -1 means "some remaining" (unknown amount)
                # level = -2 means "unknown"
                # level = -3 means "some remaining" (at least one unit)
                
                if max_cap > 0 and level >= 0:
                    item['percentage'] = round((level / max_cap) * 100)
                elif level == -3:
                    item['percentage'] = 10  # Arbitrary "Low but OK"
                    item['status'] = 'OK (Low)'
                elif level == -2:
                    item['percentage'] = 0
                    item['status'] = 'Unknown'
                elif level == -1:
                    item['percentage'] = 50 # Arbitrary "Has Supply"
                    item['status'] = 'Available'
                else:
                    item['percentage'] = 0
                
                # Normalize type
                if not item.get('type'):
                    item['type'] = 'supply'
                    
                results.append(item)
                
            return results

        except Exception as e:
            log.error(f"[SNMP] Printer supply error for {host}: {e}")
            return []



def classify_connection_type_from_interfaces(ifaces: list[dict]) -> str:
    """Derive 'wifi' | 'lan' | 'unknown' from a list of SNMP interface dicts.

    Each dict is expected to have 'if_type' (int, IANAifType) and 'name' (str).
    Priority: IANAifType 71 (ieee80211) → wifi;
              IANAifType in {6, 117, 161, 166} (ethernet variants) → lan;
              Fallback: interface name heuristics matching agent-side tokens.
    Returns 'unknown' when no interface is conclusive.
    """
    WIFI_IANA = {71}
    LAN_IANA = {6, 117, 161, 166}
    WIFI_TOKENS = ("wi-fi", "wifi", "wireless", "wlan", "wlp", "802.11", "wl")
    LAN_TOKENS = ("eth", "ethernet", "lan", "enp", "eno", "ens", "gig")

    best = "unknown"
    for iface in ifaces:
        it = iface.get("if_type")
        name = (iface.get("name") or "").lower()
        if it in WIFI_IANA or any(t in name for t in WIFI_TOKENS):
            return "wifi"
        if it in LAN_IANA or any(t in name for t in LAN_TOKENS):
            best = "lan"
    return best


# Singleton instance
snmp_service = SnmpService()
