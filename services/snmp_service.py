"""
SNMP Polling Service for Network Monitoring System.
Uses pysnmp to query SNMP-enabled devices for system info and interface statistics.
"""
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor

# pysnmp imports
try:
    from pysnmp.hlapi import (
        SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
        ObjectType, ObjectIdentity, getCmd, nextCmd, bulkCmd
    )
    PYSNMP_AVAILABLE = True
except ImportError:
    PYSNMP_AVAILABLE = False
    print("WARNING: pysnmp not installed. SNMP polling disabled.")


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


class SnmpService:
    """
    Service for polling SNMP-enabled devices.
    Provides methods to retrieve system info, interface list, and traffic counters.
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
    
    def get_system_info(self, host: str, community: str = 'public', 
                        version: str = '2c', port: int = 161) -> Dict[str, Any]:
        """
        Get basic system information from a device.
        Returns dict with sysDescr, sysName, sysUpTime, sysLocation, sysContact.
        """
        if not PYSNMP_AVAILABLE:
            return {'error': 'pysnmp not installed'}
        
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
            
            if error_indication:
                return {'error': str(error_indication)}
            elif error_status:
                return {'error': f'{error_status.prettyPrint()} at {error_index}'}
            else:
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
            return {'error': str(e)}
    
    def get_interfaces(self, host: str, community: str = 'public',
                       version: str = '2c', port: int = 161) -> List[Dict[str, Any]]:
        """
        Get list of interfaces with their properties.
        Uses SNMP walk on ifTable and ifXTable.
        """
        if not PYSNMP_AVAILABLE:
            return []
        
        interfaces = {}
        
        # Walk ifTable for basic interface info
        try:
            for (error_indication, error_status, error_index, var_binds) in nextCmd(
                self._engine,
                self._get_community_data(community, version),
                self._get_transport_target(host, port),
                ContextData(),
                ObjectType(ObjectIdentity(SnmpOids.IF_INDEX)),
                ObjectType(ObjectIdentity(SnmpOids.IF_DESCR)),
                ObjectType(ObjectIdentity(SnmpOids.IF_TYPE)),
                ObjectType(ObjectIdentity(SnmpOids.IF_SPEED)),
                ObjectType(ObjectIdentity(SnmpOids.IF_PHYS_ADDRESS)),
                ObjectType(ObjectIdentity(SnmpOids.IF_ADMIN_STATUS)),
                ObjectType(ObjectIdentity(SnmpOids.IF_OPER_STATUS)),
                lexicographicMode=False
            ):
                if error_indication or error_status:
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
            print(f"SNMP interface walk error: {e}")
        
        return list(interfaces.values())
    
    def get_interface_counters(self, host: str, community: str = 'public',
                                version: str = '2c', port: int = 161) -> List[Dict[str, Any]]:
        """
        Get traffic counters for all interfaces.
        Returns list of dicts with in_octets, out_octets, errors.
        """
        if not PYSNMP_AVAILABLE:
            return []
        
        counters = {}
        
        try:
            for (error_indication, error_status, error_index, var_binds) in nextCmd(
                self._engine,
                self._get_community_data(community, version),
                self._get_transport_target(host, port),
                ContextData(),
                ObjectType(ObjectIdentity(SnmpOids.IF_INDEX)),
                ObjectType(ObjectIdentity(SnmpOids.IF_IN_OCTETS)),
                ObjectType(ObjectIdentity(SnmpOids.IF_OUT_OCTETS)),
                ObjectType(ObjectIdentity(SnmpOids.IF_IN_ERRORS)),
                ObjectType(ObjectIdentity(SnmpOids.IF_OUT_ERRORS)),
                lexicographicMode=False
            ):
                if error_indication or error_status:
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
            print(f"SNMP counter walk error: {e}")
        
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

    def get_server_health_snmp(self, host: str, community: str = 'public',
                               version: str = '2c', port: int = 161) -> Dict[str, Any]:
        """
        Get server health metrics (CPU, RAM, Disk) via HOST-RESOURCES-MIB.
        Returns dict with cpu_usage, memory_usage, disk_usage.
        """
        if not PYSNMP_AVAILABLE:
            return {}

        metrics = {}
        
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
            for (error_indication, error_status, error_index, var_binds) in nextCmd(
                self._engine,
                self._get_community_data(community, version),
                self._get_transport_target(host, port),
                ContextData(),
                ObjectType(ObjectIdentity(HR_PROCESSOR_LOAD)),
                lexicographicMode=False
            ):
                if error_indication or error_status: break
                for var_bind in var_binds:
                    cpu_loads.append(int(var_bind[1]))
            
            if cpu_loads:
                metrics['cpu_usage'] = round(sum(cpu_loads) / len(cpu_loads), 1)

            # 2. Storage (RAM & Disk)
            # We need to walk the implementation to find indices for RAM and Disk
            # Using bulk walk might be better but nextCmd is safer for compatibility
            
            ram_total = 0
            ram_used = 0
            disk_stats = []
            
            for (error_indication, error_status, error_index, var_binds) in nextCmd(
                self._engine,
                self._get_community_data(community, version),
                self._get_transport_target(host, port),
                ContextData(),
                ObjectType(ObjectIdentity(HR_STORAGE_TYPE)),
                ObjectType(ObjectIdentity(HR_STORAGE_DESCR)),
                ObjectType(ObjectIdentity(HR_STORAGE_UNITS)),
                ObjectType(ObjectIdentity(HR_STORAGE_SIZE)),
                ObjectType(ObjectIdentity(HR_STORAGE_USED)),
                lexicographicMode=False
            ):
                if error_indication or error_status: break
                
                for var_bind in var_binds:
                    # pysnmp returns varBinds as a list of tuples? No, nextCmd returns list of varBinds, each is a tuple?
                    # Actually nextCmd yields a row of varBinds.
                    # We requested 5 columns, so var_binds should have 5 elements.
                    pass 
                
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
                # Average of all disks or max? Let's take max to be safe/alerting
                metrics['disk_usage'] = max(disk_stats)
                
            return metrics

        except Exception as e:
            print(f"SNMP Health Check Error: {e}")
            return metrics


# Singleton instance
snmp_service = SnmpService()
