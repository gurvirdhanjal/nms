# Import all models here to make them available
from .user import User
from .device import Device
from .site import Site
from .printer import PrinterMetrics, PrintJobAudit
from .department import Department
from .subnet import Subnet
from .scan_history import DeviceScanHistory, NetworkScan, PortScanResult
from .dashboard import DashboardEvent, DailyDeviceStats, DashboardSnapshot
from .interfaces import DeviceInterface, InterfaceTrafficHistory
from .snmp_config import DeviceSnmpConfig
from .topology import SwitchTopology
from .tracked_device import (
    TrackedDevice,
    TrackedDeviceIpHistory,
    DeviceActivityLog,
    DeviceResourceLog,
    DeviceApplicationLog,
    TrackingSample,
    TrackingHistoryIntegrityAudit,
    TrackedDeviceAvailabilityEvent,
    TrackingHourlyRollup,
    TrackingDailyRollup,
)
from .restricted_site_policy import (
    RestrictedSitePolicy,
    TrackingAgentKeyBinding,
    RestrictedSiteEvent,
    RestrictedSiteAlertState,
    RestrictedSiteDomainMeta,
)
from .audit_log import AuditLog
from .server_health import ServerHealthLog
from .server_health_rollups import (
    ServerHealthHourlyRollup,
    ServerHealthDailyRollup,
    ServerHealthRollupState,
)

__all__ = [
    'User', 'Device', 'Site', 'Department', 'PrinterMetrics', 'PrintJobAudit',
    'DeviceScanHistory', 'NetworkScan', 'PortScanResult',
    'DashboardEvent', 'DailyDeviceStats', 'DashboardSnapshot', 'DeviceInterface', 'InterfaceTrafficHistory',
    'DeviceSnmpConfig', 'SwitchTopology', 'TrackedDevice',
    'TrackedDeviceIpHistory',
    'DeviceActivityLog', 'DeviceResourceLog', 'DeviceApplicationLog',
    'TrackingSample', 'TrackingHistoryIntegrityAudit', 'TrackedDeviceAvailabilityEvent',
    'TrackingHourlyRollup', 'TrackingDailyRollup',
    'RestrictedSitePolicy', 'TrackingAgentKeyBinding', 'RestrictedSiteEvent',
    'RestrictedSiteAlertState', 'RestrictedSiteDomainMeta',
    'AuditLog', 'ServerHealthLog',
    'ServerHealthHourlyRollup', 'ServerHealthDailyRollup', 'ServerHealthRollupState'
]
