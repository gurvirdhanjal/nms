# Import all models here to make them available
from .user import User
from .device import Device
from .scan_history import DeviceScanHistory, NetworkScan, PortScanResult
from .dashboard import DashboardEvent, DailyDeviceStats
from .interfaces import DeviceInterface, InterfaceTrafficHistory
from .snmp_config import DeviceSnmpConfig
from .topology import SwitchTopology
from .tracked_device import TrackedDevice
from .server_health import ServerHealthLog
from .server_health_rollups import (
    ServerHealthHourlyRollup,
    ServerHealthDailyRollup,
    ServerHealthRollupState,
)

__all__ = [
    'User', 'Device', 'DeviceScanHistory', 'NetworkScan', 'PortScanResult',
    'DashboardEvent', 'DailyDeviceStats', 'DeviceInterface', 'InterfaceTrafficHistory',
    'DeviceSnmpConfig', 'SwitchTopology', 'TrackedDevice', 'ServerHealthLog',
    'ServerHealthHourlyRollup', 'ServerHealthDailyRollup', 'ServerHealthRollupState'
]
