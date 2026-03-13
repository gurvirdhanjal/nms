import uuid
from datetime import datetime

from extensions import db
from models.dashboard import DashboardEvent
from models.server_metric_threshold_state import ServerMetricThresholdState
from services.server_thresholds import (
    alert_metric_name,
    evaluate_metrics_for_log,
    get_merged_thresholds,
    metric_display_name,
)


class AlertManager:
    """
    Handles alert generation based on device metrics and server health.

    Alert Priority:
        1. Server Health thresholds
        2. ICMP Ping status/latency/packet loss

    Anti-spike system:
        Uses strike counters so alerts require consecutive breaches.

    Maintenance mode:
        Devices with maintenance_mode=True are skipped.
    """

    LATENCY_THRESHOLD_MS = 200
    PACKET_LOSS_THRESHOLD_PCT = 10.0

    STRIKES_REQUIRED = 3
    RESOLVE_STRIKES_REQUIRED = 2

    _status_recovery = {}
    _health_recovery = {}
    _latency_recovery = {}
    _packet_loss_recovery = {}
    _legacy_server_metric_aliases = {
        "health_cpu_usage_pct": ("health_cpu",),
        "health_memory_usage_pct": ("health_ram",),
        "health_disk_usage_pct": ("health_disk",),
    }

    # Maps rules_json flat keys → (METRIC_CATALOG key, threshold field)
    # Add entries here if more metrics are exposed to compliance profiles.
    _RULES_JSON_MAP: dict[str, tuple[str, str]] = {
        "cpu_warning":     ("cpu_usage_pct",    "warning"),
        "cpu_critical":    ("cpu_usage_pct",    "critical"),
        "memory_warning":  ("memory_usage_pct", "warning"),
        "memory_critical": ("memory_usage_pct", "critical"),
        "disk_warning":    ("disk_usage_pct",   "warning"),
        "disk_critical":   ("disk_usage_pct",   "critical"),
    }

    @classmethod
    def process_scan_result(cls, device, is_online, latency_ms, packet_loss_pct, commit=True):
        if getattr(device, "maintenance_mode", False):
            return

        if getattr(device, "offline_strikes", None) is None:
            device.offline_strikes = 0

        is_server = str(device.device_type).lower() == "server"
        should_monitor = device.is_monitored

        if should_monitor:
            status_key = (device.device_id, "status")
            if not is_online:
                device.offline_strikes += 1
                cls._status_recovery.pop(status_key, None)
                if device.offline_strikes >= cls.STRIKES_REQUIRED:
                    cls._trigger_alert(
                        device,
                        event_type="STATUS",
                        severity="CRITICAL",
                        metric="status",
                        message=f"{'Server' if is_server else 'Device'} {device.device_name} ({device.device_ip}) is OFFLINE ({cls.STRIKES_REQUIRED} consecutive failures)",
                        value=0,
                        commit=commit,
                        send_email=is_server,
                    )
            else:
                if device.offline_strikes > 0:
                    device.offline_strikes = 0

                if cls._has_active_alert(device, metric="status"):
                    recovery = cls._status_recovery.get(status_key, 0) + 1
                    cls._status_recovery[status_key] = recovery
                    if recovery >= cls.RESOLVE_STRIKES_REQUIRED:
                        cls._resolve_alert(device, metric="status", commit=commit)
                        cls._status_recovery.pop(status_key, None)
                else:
                    cls._status_recovery.pop(status_key, None)

        if not should_monitor:
            cls._status_recovery.pop((device.device_id, "status"), None)
            cls._resolve_alert(device, metric="status", commit=commit)

        if is_online:
            if latency_ms is not None and latency_ms >= cls.LATENCY_THRESHOLD_MS:
                device.latency_strikes = (getattr(device, "latency_strikes", None) or 0) + 1
                cls._latency_recovery.pop((device.device_id, "latency"), None)
                if device.latency_strikes >= cls.STRIKES_REQUIRED:
                    cls._trigger_alert(
                        device,
                        event_type="PING",
                        severity="WARNING",
                        metric="latency",
                        message=f"Sustained high latency: {latency_ms:.1f}ms ({cls.STRIKES_REQUIRED} consecutive scans >= {cls.LATENCY_THRESHOLD_MS}ms)",
                        value=latency_ms,
                        commit=commit,
                        send_email=False,
                    )
            else:
                if getattr(device, "latency_strikes", 0) > 0:
                    device.latency_strikes = 0
                cls._handle_icmp_recovery(device, "latency", commit=commit)

            if packet_loss_pct is not None and packet_loss_pct >= cls.PACKET_LOSS_THRESHOLD_PCT:
                device.packet_loss_strikes = (getattr(device, "packet_loss_strikes", None) or 0) + 1
                cls._packet_loss_recovery.pop((device.device_id, "packet_loss"), None)
                if device.packet_loss_strikes >= cls.STRIKES_REQUIRED:
                    cls._trigger_alert(
                        device,
                        event_type="PING",
                        severity="WARNING",
                        metric="packet_loss",
                        message=f"Sustained packet loss: {packet_loss_pct:.1f}% ({cls.STRIKES_REQUIRED} consecutive scans >= {cls.PACKET_LOSS_THRESHOLD_PCT}%)",
                        value=packet_loss_pct,
                        commit=commit,
                        send_email=False,
                    )
            else:
                if getattr(device, "packet_loss_strikes", 0) > 0:
                    device.packet_loss_strikes = 0
                cls._handle_icmp_recovery(device, "packet_loss", commit=commit)
        else:
            if getattr(device, "latency_strikes", 0) > 0:
                device.latency_strikes = 0
            if getattr(device, "packet_loss_strikes", 0) > 0:
                device.packet_loss_strikes = 0
            cls._handle_icmp_recovery(device, "latency", commit=commit)
            cls._handle_icmp_recovery(device, "packet_loss", commit=commit)

    @classmethod
    def _get_thresholds(cls, device) -> dict:
        """
        Return the effective thresholds for a device.

        If the device has a compliance_profile_id, the profile's rules_json
        overrides matching global threshold values.  Unknown keys in rules_json
        are silently ignored so a badly formed profile never breaks alerting.

        Falls back to global defaults if:
          - no profile is assigned
          - the profile row is missing
          - any error occurs during profile loading
        """
        thresholds = get_merged_thresholds()
        profile_id = getattr(device, 'compliance_profile_id', None)
        if not profile_id:
            return thresholds

        try:
            from models.compliance_profile import ComplianceProfile
            profile = ComplianceProfile.query.get(profile_id)
            if not profile or not isinstance(profile.rules_json, dict):
                return thresholds

            metrics = thresholds["metrics"]
            for rule_key, value in profile.rules_json.items():
                mapping = cls._RULES_JSON_MAP.get(rule_key)
                if mapping is None or value is None:
                    continue
                metric_key, field = mapping
                if metric_key in metrics:
                    metrics[metric_key][field] = float(value)
        except Exception:
            pass  # Never let profile loading break alert evaluation

        return thresholds

    @classmethod
    def check_server_health(cls, device, log, commit=True):
        if getattr(device, "maintenance_mode", False) or log is None:
            return

        thresholds = cls._get_thresholds(device)
        evaluations = evaluate_metrics_for_log(log, thresholds)
        evaluation_time = getattr(log, "timestamp", None) or datetime.utcnow()

        if getattr(device, "health_alert_strikes", None) is None:
            device.health_alert_strikes = 0

        active_breaches = 0
        for metric_key, evaluation in evaluations.items():
            threshold = evaluation.threshold
            if not threshold.get("enabled"):
                continue

            state_row = ServerMetricThresholdState.query.filter_by(
                device_id=device.device_id,
                metric_key=metric_key,
            ).first()
            if state_row is None:
                state_row = ServerMetricThresholdState(
                    device_id=device.device_id,
                    metric_key=metric_key,
                )
                db.session.add(state_row)

            state_row.last_value = evaluation.value
            state_row.last_evaluated_at = evaluation_time
            state_row.updated_at = datetime.utcnow()
            state_row.last_state = evaluation.state

            metric_name = alert_metric_name(metric_key)
            legacy_names = cls._legacy_server_metric_aliases.get(metric_name, ())

            if evaluation.state in ("warning", "critical"):
                active_breaches += 1
                state_row.breach_streak = int(state_row.breach_streak or 0) + 1
                state_row.recovery_streak = 0
                if state_row.breach_streak >= cls.STRIKES_REQUIRED:
                    unit = threshold.get("unit") or ""
                    threshold_value = threshold["critical"] if evaluation.state == "critical" else threshold["warning"]
                    value_text = f"{evaluation.value:.1f}{unit}" if evaluation.value is not None else "N/A"
                    threshold_text = f"{float(threshold_value):.1f}{unit}"
                    cls._trigger_alert(
                        device,
                        event_type="server_health",
                        severity=evaluation.state.upper(),
                        metric=metric_name,
                        message=(
                            f"[Server Health] {device.device_name}: {metric_display_name(metric_key)} "
                            f"at {value_text} ({evaluation.state.title()} >= {threshold_text})"
                        ),
                        value=evaluation.value,
                        commit=False,
                        send_email=True,
                    )
                cls._health_recovery.pop((device.device_id, metric_name), None)
                continue

            state_row.breach_streak = 0
            if evaluation.state == "healthy":
                recovery = int(state_row.recovery_streak or 0) + 1
                state_row.recovery_streak = recovery
                if recovery >= cls.RESOLVE_STRIKES_REQUIRED:
                    cls._resolve_alert(device, metric=metric_name, commit=False)
                    for legacy_metric in legacy_names:
                        cls._resolve_alert(device, metric=legacy_metric, commit=False)
                    state_row.recovery_streak = 0
                    cls._health_recovery.pop((device.device_id, metric_name), None)
            else:
                state_row.recovery_streak = 0

        device.health_alert_strikes = active_breaches

        if commit:
            db.session.commit()

    @classmethod
    def _trigger_alert(cls, device, event_type, severity, metric, message, value, commit=True, send_email=False):
        existing = DashboardEvent.query.filter_by(
            device_id=device.device_id,
            metric_name=metric,
            resolved=False,
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
                resolved=False,
            )
            db.session.add(event)
            if commit:
                db.session.commit()

            if send_email and severity in ("CRITICAL", "WARNING"):
                try:
                    from services.notification_service import NotificationService

                    NotificationService.send_alert(device, metric, value, message, severity=severity)
                except Exception as exc:
                    print(f"[ERROR] Failed to send notification: {exc}")

    @classmethod
    def _resolve_alert(cls, device, metric, commit=True):
        existing = DashboardEvent.query.filter_by(
            device_id=device.device_id,
            metric_name=metric,
            resolved=False,
        ).first()

        if existing:
            existing.resolved = True
            existing.resolved_at = datetime.utcnow()
            existing.message += " [RESOLVED]"
            if commit:
                db.session.commit()

    @classmethod
    def _has_active_alert(cls, device, metric):
        return DashboardEvent.query.filter_by(
            device_id=device.device_id,
            metric_name=metric,
            resolved=False,
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

    @classmethod
    def _handle_icmp_recovery(cls, device, metric, commit=True):
        recovery_dict = cls._latency_recovery if metric == "latency" else cls._packet_loss_recovery
        key = (device.device_id, metric)
        if not cls._has_active_alert(device, metric):
            recovery_dict.pop(key, None)
            return

        recovery = recovery_dict.get(key, 0) + 1
        recovery_dict[key] = recovery
        if recovery >= cls.RESOLVE_STRIKES_REQUIRED:
            cls._resolve_alert(device, metric=metric, commit=commit)
            recovery_dict.pop(key, None)
