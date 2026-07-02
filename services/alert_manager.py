import logging
import time
import threading
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

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
    OFFLINE_COOLDOWN_S = 1800  # 30-min post-resolution cooldown (anti-flapping)

    # Class-level RLock guards all in-memory recovery/cooldown dicts.
    # RLock (re-entrant) allows the same thread to acquire it multiple times,
    # which is needed because process_scan_result calls _trigger_alert/_resolve_alert.
    _lock: threading.RLock = threading.RLock()

    _status_recovery = {}
    _offline_cooldown: dict = {}  # device_id → unix timestamp of cooldown expiry
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

        with cls._lock:
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
                icmp = cls.get_icmp_thresholds(device)
                if latency_ms is not None and latency_ms >= icmp['latency_warning_ms']:
                    device.latency_strikes = (getattr(device, "latency_strikes", None) or 0) + 1
                    cls._latency_recovery.pop((device.device_id, "latency"), None)
                    if device.latency_strikes >= cls.STRIKES_REQUIRED:
                        cls._trigger_alert(
                            device,
                            event_type="PING",
                            severity="WARNING",
                            metric="latency",
                            message=f"Sustained high latency: {latency_ms:.1f}ms ({cls.STRIKES_REQUIRED} consecutive scans >= {icmp['latency_warning_ms']}ms)",
                            value=latency_ms,
                            commit=commit,
                            send_email=False,
                        )
                else:
                    if getattr(device, "latency_strikes", 0) > 0:
                        device.latency_strikes = 0
                    cls._handle_icmp_recovery(device, "latency", commit=commit)

                if packet_loss_pct is not None and packet_loss_pct >= icmp['packet_loss_warning_pct']:
                    device.packet_loss_strikes = (getattr(device, "packet_loss_strikes", None) or 0) + 1
                    cls._packet_loss_recovery.pop((device.device_id, "packet_loss"), None)
                    if device.packet_loss_strikes >= cls.STRIKES_REQUIRED:
                        cls._trigger_alert(
                            device,
                            event_type="PING",
                            severity="WARNING",
                            metric="packet_loss",
                            message=f"Sustained packet loss: {packet_loss_pct:.1f}% ({cls.STRIKES_REQUIRED} consecutive scans >= {icmp['packet_loss_warning_pct']}%)",
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
    def get_icmp_thresholds(cls, device) -> dict:
        """Return effective ICMP thresholds for a device.

        Priority chain (highest → lowest):
          1. Per-device override columns (icmp_latency_warning_ms etc.) if not None
          2. Compliance profile rules_json ICMP keys (if profile assigned)
          3. Class-level constants (LATENCY_THRESHOLD_MS / PACKET_LOSS_THRESHOLD_PCT)

        Exception-safe: any failure falls back to class constants.
        No additional DB queries in the hot path — reads from already-loaded device attrs.
        """
        latency_warn   = cls.LATENCY_THRESHOLD_MS
        latency_crit   = cls.LATENCY_THRESHOLD_MS      # no separate critical constant yet — same
        loss_warn      = cls.PACKET_LOSS_THRESHOLD_PCT
        loss_crit      = cls.PACKET_LOSS_THRESHOLD_PCT  # same

        try:
            # Layer 2: compliance profile ICMP keys
            profile_id = getattr(device, 'compliance_profile_id', None)
            if profile_id:
                from models.compliance_profile import ComplianceProfile
                profile = ComplianceProfile.query.get(profile_id)
                if profile and isinstance(getattr(profile, 'rules_json', None), dict):
                    rj = profile.rules_json
                    if rj.get('latency_warning_ms') is not None:
                        latency_warn = int(rj['latency_warning_ms'])
                    if rj.get('latency_critical_ms') is not None:
                        latency_crit = int(rj['latency_critical_ms'])
                    if rj.get('packet_loss_warning_pct') is not None:
                        loss_warn = float(rj['packet_loss_warning_pct'])
                    if rj.get('packet_loss_critical_pct') is not None:
                        loss_crit = float(rj['packet_loss_critical_pct'])

            # Layer 1: per-device override (wins over profile)
            if getattr(device, 'icmp_latency_warning_ms', None) is not None:
                latency_warn = device.icmp_latency_warning_ms
            if getattr(device, 'icmp_latency_critical_ms', None) is not None:
                latency_crit = device.icmp_latency_critical_ms
            if getattr(device, 'icmp_packet_loss_warning_pct', None) is not None:
                loss_warn = device.icmp_packet_loss_warning_pct
            if getattr(device, 'icmp_packet_loss_critical_pct', None) is not None:
                loss_crit = device.icmp_packet_loss_critical_pct
        except Exception:
            pass  # Always fall back to class constants — never break alerting

        return {
            'latency_warning_ms':       latency_warn,
            'latency_critical_ms':      latency_crit,
            'packet_loss_warning_pct':  loss_warn,
            'packet_loss_critical_pct': loss_crit,
        }

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
            try:
                db.session.commit()
            except Exception as exc:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                device_id = device.__dict__.get('device_id', '?')
                logger.error(
                    "[AlertManager] check_server_health commit failed device=%s: %s",
                    device_id, exc,
                )

    @classmethod
    def _trigger_alert(cls, device, event_type, severity, metric, message, value, commit=True, send_email=False):
        # Check in-memory cooldown only (narrow lock — no DB work here)
        if metric == "status":
            with cls._lock:
                cooldown_until = cls._offline_cooldown.get(device.device_id, 0)
                if time.time() < cooldown_until:
                    return  # Suppressed within 30-min anti-flapping window

        # DB operations run outside the class lock — each thread has its own
        # SQLAlchemy scoped session; the DB handles concurrency via MVCC.
        # All writes are wrapped so a DB error (lock_timeout, constraint, stale row)
        # never propagates up and contaminates the caller's session.
        #
        # no_autoflush: the caller (process_scan_result) may have modified device fields
        # (e.g. latency_strikes) making the ORM object dirty. Without this guard,
        # SQLAlchemy's autoflush fires before the DashboardEvent query, tries to UPDATE
        # the device row, and races with the SNMP worker's SELECT FOR UPDATE lock —
        # causing a lock_timeout that poisons the entire session.
        try:
            with db.session.no_autoflush:
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
                    site_id=getattr(device, 'site_id', None),
                    department_id=getattr(device, 'department_id', None),
                )
                db.session.add(event)
                if commit:
                    db.session.commit()
        except Exception as exc:
            # Rollback before any ORM attribute access — the session may be in
            # PendingRollbackError state, and accessing device attributes triggers
            # lazy loading which raises a second exception before rollback can run.
            try:
                db.session.rollback()
            except Exception:
                pass
            # Use __dict__ to read already-loaded identity fields without touching
            # the (potentially poisoned) SQLAlchemy lazy-load machinery.
            device_id = device.__dict__.get('device_id', '?')
            logger.error(
                "[AlertManager] _trigger_alert write failed device=%s metric=%s: %s",
                device_id, metric, exc,
            )
            return  # Skip notification — no event was persisted

        if send_email and severity in ("CRITICAL", "WARNING"):
            try:
                from services import alert_routing_service
                alert_routing_service.route_alert(device, metric, value, message, severity)
            except Exception as exc:
                logger.error("[AlertManager] Failed to route alert: %s", exc)

    @classmethod
    def _resolve_alert(cls, device, metric, commit=True):
        # DB operations run outside the class lock — each thread has its own
        # SQLAlchemy scoped session; the DB handles concurrency via MVCC.
        # Wrapped so a DB error never propagates up and contaminates the caller's session.
        try:
            with db.session.no_autoflush:
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
                # Anti-flap: narrow lock only for in-memory cooldown dict write
                if metric == "status":
                    with cls._lock:
                        cls._offline_cooldown[device.device_id] = time.time() + cls.OFFLINE_COOLDOWN_S
        except Exception as exc:
            try:
                db.session.rollback()
            except Exception:
                pass
            device_id = device.__dict__.get('device_id', '?')
            logger.error(
                "[AlertManager] _resolve_alert write failed device=%s metric=%s: %s",
                device_id, metric, exc,
            )

    @classmethod
    def _has_active_alert(cls, device, metric):
        with db.session.no_autoflush:
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

    @classmethod
    def auto_resolve_stale_alerts(cls):
        """DB-driven sweep: resolve open STATUS/LATENCY/PACKET_LOSS alerts whose device
        has 2+ consecutive healthy scans in device_scan_history.

        Runs every 2 minutes from the scheduler and once ~60s after startup.
        Restart-safe — does not rely on in-memory strike counters. Complements the
        per-scan _status_recovery / _handle_icmp_recovery logic as a fallback for
        stale alerts left by container restarts, DB hiccups, or monitoring gaps.
        """
        from sqlalchemy import text as _text
        from collections import defaultdict

        try:
            open_alerts = (
                DashboardEvent.query
                .filter(
                    DashboardEvent.resolved == False,
                    DashboardEvent.metric_name.in_(["status", "latency", "packet_loss"]),
                )
                .all()
            )
            if not open_alerts:
                return 0

            device_ids = list({a.device_id for a in open_alerts})

            # Fetch the 2 most-recent scan statuses per device via LATERAL index lookup.
            stmt = _text("""
                SELECT d.device_id, l.status, l.ping_time_ms, l.packet_loss
                FROM (SELECT unnest(:ids) AS device_id) AS d_ids
                JOIN device d ON d.device_id = d_ids.device_id
                CROSS JOIN LATERAL (
                    SELECT dsh.status, dsh.ping_time_ms, dsh.packet_loss
                    FROM device_scan_history dsh
                    WHERE dsh.device_ip = d.device_ip
                    ORDER BY dsh.scan_timestamp DESC
                    LIMIT 2
                ) AS l
            """)
            rows = db.session.execute(stmt, {"ids": device_ids}).fetchall()

            scans_by_device = defaultdict(list)
            for row in rows:
                scans_by_device[row.device_id].append(row)

            now = datetime.utcnow()
            resolved_count = 0

            for alert in open_alerts:
                scans = scans_by_device.get(alert.device_id, [])
                if len(scans) < 2:
                    continue

                all_online = all(str(s.status or "").lower() == "online" for s in scans)
                if not all_online:
                    continue

                should_resolve = False
                if alert.metric_name == "status":
                    should_resolve = True
                elif alert.metric_name == "latency":
                    # Resolve if latency is within the class-level default threshold.
                    # Per-device threshold accuracy is handled by the live in-memory counter;
                    # this sweep is a fallback for stale alerts only.
                    should_resolve = all(
                        s.ping_time_ms is None or s.ping_time_ms < cls.LATENCY_THRESHOLD_MS
                        for s in scans
                    )
                elif alert.metric_name == "packet_loss":
                    should_resolve = all(
                        s.packet_loss is None or s.packet_loss < cls.PACKET_LOSS_THRESHOLD_PCT
                        for s in scans
                    )

                if should_resolve:
                    alert.resolved = True
                    alert.resolved_at = now
                    alert.message = alert.message.rstrip() + " [AUTO-RESOLVED]"
                    resolved_count += 1
                    if alert.metric_name == "status":
                        with cls._lock:
                            cls._offline_cooldown[alert.device_id] = (
                                time.time() + cls.OFFLINE_COOLDOWN_S
                            )

            if resolved_count > 0:
                db.session.commit()
                logger.info(
                    "[AlertManager] auto_resolve_stale_alerts: resolved %d alert(s)",
                    resolved_count,
                )

            return resolved_count

        except Exception as exc:
            try:
                db.session.rollback()
            except Exception:
                pass
            logger.error("[AlertManager] auto_resolve_stale_alerts failed: %s", exc)
            return 0
