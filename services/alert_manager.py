import uuid
from datetime import datetime
from extensions import db
from models.dashboard import DashboardEvent

class AlertManager:
    """
    Handles alert generation based on device metrics and server health.

    Alert Priority:
        1. Server Health (RAM, CPU, Disk) - PRIMARY, triggers email
        2. ICMP Ping (latency / packet loss) - informational only

    Anti-Spike System:
        Uses a "strikes" counter to avoid alerting on momentary spikes.
        A health breach must occur N consecutive times before an alert fires.

    Maintenance Mode:
        Devices with maintenance_mode=True are silently skipped.
    """

    # ICMP Thresholds (low priority - informational only)
    LATENCY_THRESHOLD_MS = 100
    PACKET_LOSS_THRESHOLD_PCT = 5.0

    # Server Health Thresholds
    RAM_WARNING_PCT = 60.0
    CPU_WARNING_PCT = 80.0
    DISK_WARNING_PCT = 90.0
    RAM_CRITICAL_PCT = 85.0
    CPU_CRITICAL_PCT = 90.0
    DISK_CRITICAL_PCT = 95.0

    # Strikes required before alert fires (prevents spike false positives)
    STRIKES_REQUIRED = 3

    # Recovery strikes required before resolving warning/critical alerts
    RESOLVE_STRIKES_REQUIRED = 2

    # In-memory recovery tracking to prevent alert flapping
    _status_recovery = {}
    _health_recovery = {}

    # ─────────────────────────────────────────────
    # Public: ICMP Ping Scan Results
    # ─────────────────────────────────────────────
    @classmethod
    def process_scan_result(cls, device, is_online, latency_ms, packet_loss_pct, commit=True):
        """
        Process ICMP ping scan results.
        
        CHANGES (2026-02-11):
        - REMOVED all immediate ICMP alerts (latency/packet loss).
        - ENABLED 3-strike rule for 'Offline' status on SERVERS only.
        - Maintenance mode devices are skipped.
        """
        # Skip devices in maintenance window
        if getattr(device, 'maintenance_mode', False):
            return

        # Initialize strikes if needed (handled by DB default, but safe keeping)
        if getattr(device, 'offline_strikes', None) is None:
            device.offline_strikes = 0

        # --- SERVER AVAILABILITY CHECK (3-Strike Rule) ---
        is_server = (str(device.device_type).lower() == 'server')
        should_monitor = device.is_monitored and is_server

        if should_monitor:
            status_key = (device.device_id, 'status')
            if not is_online:
                # Device is Offline -> Increment Strike
                device.offline_strikes += 1
                cls._status_recovery.pop(status_key, None)
                
                if device.offline_strikes >= cls.STRIKES_REQUIRED:
                    # Nth Consecutive Failure -> Trigger CRITICAL Alert
                    # (Only invoke if not already alerted to avoid spam, though _trigger_alert handles updates)
                    cls._trigger_alert(
                        device,
                        event_type='STATUS',
                        severity='CRITICAL',
                        metric='status',
                        message=f"Server {device.device_name} ({device.device_ip}) is OFFLINE ({cls.STRIKES_REQUIRED} consecutive failures)",
                        value=0,
                        commit=commit,
                        send_email=True
                    )
                else:
                    # Strike 1 or 2 -> Log but NO Alert
                    print(f"[ALERT MANAGER] Server {device.device_ip} offline strike {device.offline_strikes}/{cls.STRIKES_REQUIRED}. Suppressing alert.")
            
            else:
                # Device is Online -> Reset Strikes & Resolve Alert (with recovery strikes)
                if device.offline_strikes > 0:
                    print(f"[ALERT MANAGER] Server {device.device_ip} back online. Resetting strikes.")
                    device.offline_strikes = 0
                
                if cls._has_active_alert(device, metric='status'):
                    recovery = cls._status_recovery.get(status_key, 0) + 1
                    cls._status_recovery[status_key] = recovery
                    if recovery >= cls.RESOLVE_STRIKES_REQUIRED:
                        cls._resolve_alert(device, metric='status', commit=commit)
                        cls._status_recovery.pop(status_key, None)
                else:
                    cls._status_recovery.pop(status_key, None)

        # --- OTHER DEVICES (Visual Status Only) ---
        # For non-servers, we do NOT trigger alerts, but backend status is updated by scanner itself.
        # Ensure we clear any stale alerts if they exist from before.
        if not should_monitor:
             cls._status_recovery.pop((device.device_id, 'status'), None)
             cls._resolve_alert(device, metric='status', commit=commit)

        # --- ICMP PERFORMANCE ALERTS (Informational) ---
        if is_online:
            if latency_ms is not None and latency_ms >= cls.LATENCY_THRESHOLD_MS:
                cls._trigger_alert(
                    device,
                    event_type='PING',
                    severity='INFO',
                    metric='latency',
                    message=f"Ping latency high: {latency_ms:.1f}ms (Threshold >= {cls.LATENCY_THRESHOLD_MS}ms)",
                    value=latency_ms,
                    commit=commit,
                    send_email=False
                )
            else:
                cls._resolve_alert(device, metric='latency', commit=commit)

            if packet_loss_pct is not None and packet_loss_pct >= cls.PACKET_LOSS_THRESHOLD_PCT:
                cls._trigger_alert(
                    device,
                    event_type='PING',
                    severity='INFO',
                    metric='packet_loss',
                    message=f"Packet loss high: {packet_loss_pct:.1f}% (Threshold >= {cls.PACKET_LOSS_THRESHOLD_PCT}%)",
                    value=packet_loss_pct,
                    commit=commit,
                    send_email=False
                )
            else:
                cls._resolve_alert(device, metric='packet_loss', commit=commit)
        else:
            cls._resolve_alert(device, metric='latency', commit=commit)
            cls._resolve_alert(device, metric='packet_loss', commit=commit)


    # ─────────────────────────────────────────────
    # Public: Server Health (from Agent metrics)
    # ─────────────────────────────────────────────
    @classmethod
    def check_server_health(cls, device, log, commit=True):
        """
        Evaluate server health from a ServerHealthLog entry.
        Uses strikes logic to avoid alerting on momentary spikes.
        This is the PRIMARY alert source — emails are sent for WARNING/CRITICAL.

        Args:
            device: Device model instance
            log: ServerHealthLog instance with cpu_usage, memory_usage, disk_usage
            commit: Whether to commit DB changes
        """
        # Skip devices in maintenance window
        if getattr(device, 'maintenance_mode', False):
            return

        cpu = log.cpu_usage
        ram = log.memory_usage
        disk = log.disk_usage

        # Determine if any metric is breaching
        breaches = []
        severity = 'WARNING'

        # RAM checks
        if ram is not None:
            if ram >= cls.RAM_CRITICAL_PCT:
                breaches.append(('ram', ram, 'CRITICAL', f"RAM at {ram:.1f}% (Critical ≥{cls.RAM_CRITICAL_PCT}%)"))
                severity = 'CRITICAL'
            elif ram >= cls.RAM_WARNING_PCT:
                breaches.append(('ram', ram, 'WARNING', f"RAM at {ram:.1f}% (Warning ≥{cls.RAM_WARNING_PCT}%)"))

        # CPU checks
        if cpu is not None:
            if cpu >= cls.CPU_CRITICAL_PCT:
                breaches.append(('cpu', cpu, 'CRITICAL', f"CPU at {cpu:.1f}% (Critical ≥{cls.CPU_CRITICAL_PCT}%)"))
                severity = 'CRITICAL'
            elif cpu >= cls.CPU_WARNING_PCT:
                breaches.append(('cpu', cpu, 'WARNING', f"CPU at {cpu:.1f}% (Warning ≥{cls.CPU_WARNING_PCT}%)"))

        # Disk checks
        if disk is not None:
            if disk >= cls.DISK_CRITICAL_PCT:
                breaches.append(('disk', disk, 'CRITICAL', f"Disk at {disk:.1f}% (Critical ≥{cls.DISK_CRITICAL_PCT}%)"))
                severity = 'CRITICAL'
            elif disk >= cls.DISK_WARNING_PCT:
                breaches.append(('disk', disk, 'WARNING', f"Disk at {disk:.1f}% (Warning ≥{cls.DISK_WARNING_PCT}%)"))

        if breaches:
            # Increment strikes
            device.health_alert_strikes = (device.health_alert_strikes or 0) + 1

            if device.health_alert_strikes >= cls.STRIKES_REQUIRED:
                # Enough consecutive breaches — fire alert
                for metric_name, value, breach_severity, msg in breaches:
                    cls._trigger_alert(
                        device,
                        event_type='server_health',
                        severity=breach_severity,
                        metric=f'health_{metric_name}',
                        message=f"[Server Health] {device.device_name}: {msg}",
                        value=value,
                        commit=False,
                        send_email=(breach_severity in ('CRITICAL', 'WARNING'))
                    )
                print(f"[HEALTH] Alert fired for {device.device_ip} after {device.health_alert_strikes} strikes")
            else:
                print(f"[HEALTH] Strike {device.health_alert_strikes}/{cls.STRIKES_REQUIRED} for {device.device_ip}")
        else:
            # All clear — reset strikes and resolve existing alerts
            if device.health_alert_strikes and device.health_alert_strikes > 0:
                print(f"[HEALTH] Strikes reset for {device.device_ip} (metrics normal)")
            device.health_alert_strikes = 0

        breached_metrics = {f'health_{m}' for m, *_rest in breaches}
        for metric_name in ('health_ram', 'health_cpu', 'health_disk'):
            if metric_name in breached_metrics:
                cls._health_recovery.pop((device.device_id, metric_name), None)
                continue
            cls._handle_recovery(device, metric_name, commit=False)

        if commit:
            db.session.commit()

    # ─────────────────────────────────────────────
    # Internal: Alert CRUD
    # ─────────────────────────────────────────────
    @classmethod
    def _trigger_alert(cls, device, event_type, severity, metric, message, value, commit=True, send_email=False):
        """
        Creates an alert if one doesn't already exist for this device+metric.
        """
        existing = DashboardEvent.query.filter_by(
            device_id=device.device_id,
            metric_name=metric,
            resolved=False
        ).first()

        if existing:
            existing.value = value
            existing.message = message
            existing.severity = severity
            existing.timestamp = datetime.utcnow()
            if commit:
                db.session.commit()
        else:
            event = DashboardEvent(
                event_id=str(uuid.uuid4()),
                device_id=device.device_id,
                device_ip=device.device_ip,
                event_type=event_type,
                severity=severity,
                metric_name=metric,
                message=message,
                value=value,
                timestamp=datetime.utcnow(),
                resolved=False
            )
            db.session.add(event)
            if commit:
                db.session.commit()
            print(f"[ALERT] Triggered {severity} alert for {device.device_ip}: {metric}")

            # Email for WARNING and CRITICAL alerts (mock)
            if send_email and severity in ('CRITICAL', 'WARNING'):
                try:
                    from services.notification_service import NotificationService
                    NotificationService.send_alert(device, metric, value, message, severity=severity)
                except Exception as e:
                    print(f"[ERROR] Failed to send notification: {e}")

    @classmethod
    def _resolve_alert(cls, device, metric, commit=True):
        """
        Resolves active alerts for this metric if they exist.
        """
        existing = DashboardEvent.query.filter_by(
            device_id=device.device_id,
            metric_name=metric,
            resolved=False
        ).first()

        if existing:
            existing.resolved = True
            existing.resolved_at = datetime.utcnow()
            existing.message += " [RESOLVED]"
            if commit:
                db.session.commit()
            print(f"[ALERT] Resolved alert for {device.device_ip}: {metric}")

    @classmethod
    def _has_active_alert(cls, device, metric):
        return DashboardEvent.query.filter_by(
            device_id=device.device_id,
            metric_name=metric,
            resolved=False
        ).first() is not None

    @classmethod
    def _handle_recovery(cls, device, metric, commit=True):
        key = (device.device_id, metric)
        if not cls._has_active_alert(device, metric):
            cls._health_recovery.pop(key, None)
            return

        recovery = cls._health_recovery.get(key, 0) + 1
        cls._health_recovery[key] = recovery
        if recovery >= cls.RESOLVE_STRIKES_REQUIRED:
            cls._resolve_alert(device, metric=metric, commit=commit)
            cls._health_recovery.pop(key, None)

# Singleton not needed, using class methods
