"""
Auto-discovery service — thin coordinator between the scheduler and DiscoveryService.

Triggered by:
  1. MonitoringScheduler.maybe_run_auto_discovery() every 1 min (fires when interval elapsed)
  2. routes/discovery_settings.py POST /api/discovery-settings/trigger-heavy (manual button)

Delegates all scanning and DB persistence to DiscoveryService.trigger_settings_subnet_scan(),
which handles:
  - ICMP probes on all configured subnets (concurrent, semaphore-bounded)
  - Device upsert via upsert_device_from_identity() with match order: MAC > hostname > IP
  - IP change detection: when MAC matches but IP changed, device_ip is updated and
    propagated to scan_history automatically
  - DiscoveryConfig telemetry update: last_heavy_scan, last_scan_duration,
    last_new_count, last_updated_count, last_error
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_instance: "AutoDiscoveryService | None" = None
_instance_lock = threading.Lock()


class AutoDiscoveryService:
    """Thin coordinator that bridges the scheduler / route layer to DiscoveryService."""

    def trigger_heavy_scan(self, app) -> None:
        """Fire a background subnet scan for all configured subnets.

        Non-blocking: spawns a daemon thread and returns immediately.
        Concurrent calls are safe — DiscoveryService manages its own per-scan state.
        """
        def _run() -> None:
            try:
                with app.app_context():
                    from models.discovery_config import get_config
                    cfg = get_config()

                    if not cfg.subnets:
                        logger.debug("[AutoDiscovery] No subnets configured — skipping scan")
                        return

                    from services.discovery_service import get_discovery_service
                    svc = get_discovery_service()
                    queued = svc.trigger_settings_subnet_scan(
                        cfg.subnets, username="system", app=app
                    )
                    logger.info("[AutoDiscovery] Scan queued for %d subnet(s)", queued)

            except Exception as exc:
                logger.error("[AutoDiscovery] trigger_heavy_scan failed: %s", exc)

        t = threading.Thread(target=_run, daemon=True, name="auto-discovery")
        t.start()


def get_auto_discovery_service() -> "AutoDiscoveryService":
    """Return the process-wide AutoDiscoveryService singleton."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = AutoDiscoveryService()
    return _instance
