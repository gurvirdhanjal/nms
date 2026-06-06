import logging
import threading
from datetime import datetime
from extensions import db
from models.device import Device
from sqlalchemy.exc import OperationalError

log = logging.getLogger(__name__)

# 32-bit counter wrap ceiling
_COUNTER32_MAX = 2 ** 32


def _counter32_delta(prev: int, curr: int) -> int:
    """Return delta between two 32-bit SNMP counters, handling wrap-around."""
    delta = curr - prev
    if delta < 0:
        delta += _COUNTER32_MAX
    return delta


def _build_interface_threshold_payload(
    device_id: int,
    iface_name: str,
    if_index: int,
    rx_util: float | None,
    tx_util: float | None,
    threshold_pct: float,
) -> dict:
    rx_breach = rx_util is not None and rx_util >= threshold_pct
    tx_breach = tx_util is not None and tx_util >= threshold_pct
    if rx_breach and tx_breach:
        direction = 'both'
    elif rx_breach:
        direction = 'rx'
    elif tx_breach:
        direction = 'tx'
    else:
        direction = None
    return {
        'device_id': device_id,
        'interface_name': iface_name,
        'if_index': if_index,
        'rx_util_pct': rx_util,
        'tx_util_pct': tx_util,
        'threshold_pct': threshold_pct,
        'direction': direction,
    }


class InterfacePoller:
    """
    Service to poll interface statistics from devices and store history.
    Simulation disabled: only real backend data should be stored.
    """

    INTERFACE_UTIL_THRESHOLD_PCT: int = 80

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread = None
        self._app = None

    def start_polling(self, app):
        """Start the background polling thread"""
        self._app = app
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="interface-poller",
            )
            self._thread.start()
            log.info("Interface Poller Service started.")

    def stop_polling(self, timeout=2.0):
        """Stop the background polling thread"""
        self._stop_event.set()
        thread = self._thread
        if not thread:
            return

        try:
            thread.join(timeout=max(0.0, float(timeout)))
        except KeyboardInterrupt:
            pass

        if thread.is_alive():
            log.warning("Interface Poller stop requested; thread still exiting.")
        else:
            log.info("Interface Poller Service stopped.")
            self._thread = None

    def _run_loop(self):
        """Main polling loop"""
        while not self._stop_event.is_set():
            try:
                with self._app.app_context():
                    try:
                        self._poll_all_devices()
                    finally:
                        db.session.remove()
            except Exception as e:
                log.exception("Error in interface poller loop: %s", e)

            interval = 10
            if self._app:
                interval = self._app.config.get('INTERFACE_POLL_INTERVAL', 10)
            try:
                wait_seconds = max(0.1, float(interval))
            except (TypeError, ValueError):
                wait_seconds = 10.0
            self._stop_event.wait(wait_seconds)

    def _poll_all_devices(self):
        """Polls only devices that have SNMP enabled (no simulation fallback)."""
        from models.snmp_config import DeviceSnmpConfig
        # Join to DeviceSnmpConfig so we only iterate devices with an active
        # SNMP config — avoids a per-device warning log for every non-SNMP device.
        device_ids = [
            row[0]
            for row in (
                db.session.query(Device.device_id)
                .join(DeviceSnmpConfig, DeviceSnmpConfig.device_id == Device.device_id)
                .filter(Device.is_monitored == True, DeviceSnmpConfig.is_enabled == True)
                .all()
            )
        ]

        for device_id in device_ids:
            if self._stop_event.is_set():
                break
            for attempt in range(3):
                if self._stop_event.is_set():
                    break
                try:
                    self.poll_device_interfaces(device_id)
                    db.session.commit()
                    self._stop_event.wait(0.05)
                    break
                except OperationalError as e:
                    db.session.rollback()
                    if "database is locked" in str(e).lower() and attempt < 2:
                        self._stop_event.wait(0.2 * (attempt + 1))
                        continue
                    log.error(f"[InterfacePoller] DB error for device {device_id}: {e}")
                    break
                except Exception as e:
                    db.session.rollback()
                    log.error(f"[InterfacePoller] Error polling device {device_id}: {e}")
                    break
            db.session.remove()

    # ──────────────────────────────────────────────────────────────────────────
    # Public interface — also called by snmp_worker._execute_interface_poll()
    # ──────────────────────────────────────────────────────────────────────────

    def poll_device_interfaces(self, device_id: int) -> dict:
        """
        Poll IF-MIB data for a single device and persist results.

        Steps:
          1. Load DeviceSnmpConfig (is_enabled=True required).
          2. Walk ifDescr, ifOperStatus, ifSpeed, ifInOctets, ifOutOctets via
             existing snmp_service helpers.
          3. Upsert each interface into device_interfaces (key: device_id + if_index).
          4. Compute byte-delta rates and write an InterfaceTrafficHistory row
             for interfaces with a prior counter snapshot.

        Returns:
          {'success': True}                        on full or partial success
          {'success': False, 'error': <str>}       on early-exit failures
        """
        try:
            from models.snmp_config import DeviceSnmpConfig
            from models.interfaces import DeviceInterface, InterfaceTrafficHistory
            from services.snmp_service import snmp_service, classify_connection_type_from_interfaces

            # ── 1. Credential lookup ────────────────────────────────────────
            config = DeviceSnmpConfig.query.filter_by(
                device_id=device_id, is_enabled=True
            ).first()
            if not config:
                log.debug(
                    f"[InterfacePoller] device_id={device_id}: no enabled SNMP config — skipping"
                )
                return {'success': False, 'error': 'SNMP_NOT_CONFIGURED'}

            device = Device.query.get(device_id)
            if not device:
                log.warning(f"[InterfacePoller] device_id={device_id}: device row not found")
                return {'success': False, 'error': 'DEVICE_NOT_FOUND'}

            host = device.device_ip
            community = config.community_string or 'public'
            version = config.snmp_version or '2c'
            port = config.snmp_port or 161

            log.debug(
                f"[InterfacePoller] Polling {host} (device_id={device_id}) "
                f"SNMP v{version} community='{community}'"
            )

            # ── 2. SNMP walks ───────────────────────────────────────────────
            # get_interfaces: ifDescr, ifType, ifSpeed, ifPhysAddress,
            #                 ifAdminStatus, ifOperStatus
            # get_interface_counters: ifInOctets, ifOutOctets, ifInErrors,
            #                         ifOutErrors
            iface_list = snmp_service.get_interfaces(host, community, version, port)
            counter_list = snmp_service.get_interface_counters(host, community, version, port)

            if not iface_list and not counter_list:
                err_msg = f"No SNMP data returned from {host}"
                log.warning(f"[InterfacePoller] device_id={device_id}: {err_msg}")
                config.last_poll_error = err_msg
                db.session.commit()
                return {'success': False, 'error': 'SNMP_NO_DATA'}

            # Index counters by if_index for O(1) merge
            counters_by_index: dict[int, dict] = {
                c['if_index']: c for c in counter_list if 'if_index' in c
            }

            now = datetime.utcnow()
            rows_written = 0

            # ── 3 & 4. Upsert interfaces + write traffic history ────────────
            for iface_data in iface_list:
                if_index = iface_data.get('if_index')
                if if_index is None:
                    continue

                # Fetch current counter snapshot for this interface
                counters = counters_by_index.get(if_index, {})
                curr_in = counters.get('in_octets')
                curr_out = counters.get('out_octets')

                # Upsert: find existing row or create new one
                iface = DeviceInterface.query.filter_by(
                    device_id=device_id, if_index=if_index
                ).first()

                is_new = iface is None
                if is_new:
                    iface = DeviceInterface(device_id=device_id, if_index=if_index)
                    db.session.add(iface)

                # Update descriptor fields unconditionally (they may change)
                iface.name = iface_data.get('name', iface.name)
                iface.if_type = iface_data.get('if_type', iface.if_type)
                iface.speed_bps = iface_data.get('speed_bps', iface.speed_bps)
                iface.mac_address = iface_data.get('mac_address', iface.mac_address)
                iface.admin_status = iface_data.get('admin_status', iface.admin_status)
                iface.oper_status = iface_data.get('oper_status', iface.oper_status)

                # ── Traffic delta (only when we have a prior snapshot) ───────
                if (
                    not is_new
                    and iface.last_poll_time is not None
                    and iface.last_in_octets is not None
                    and iface.last_out_octets is not None
                    and curr_in is not None
                    and curr_out is not None
                ):
                    elapsed = (now - iface.last_poll_time).total_seconds()
                    if elapsed > 0:
                        in_delta = _counter32_delta(iface.last_in_octets, curr_in)
                        out_delta = _counter32_delta(iface.last_out_octets, curr_out)

                        rx_bps = (in_delta * 8) / elapsed
                        tx_bps = (out_delta * 8) / elapsed

                        speed = iface.speed_bps or 0
                        rx_util = round((rx_bps / speed) * 100, 2) if speed > 0 else None
                        tx_util = round((tx_bps / speed) * 100, 2) if speed > 0 else None

                        history = InterfaceTrafficHistory(
                            interface_id=iface.interface_id,
                            timestamp=now,
                            rx_bps=round(rx_bps, 2),
                            tx_bps=round(tx_bps, 2),
                            rx_utilization_pct=rx_util,
                            tx_utilization_pct=tx_util,
                        )
                        db.session.add(history)
                        rows_written += 1

                        # Broadcast interface_threshold SSE event when utilisation breaches threshold.
                        # SNMP polling is currently paused — this fires when a managed switch is enabled.
                        _threshold = (
                            self._app.config.get(
                                'INTERFACE_UTIL_THRESHOLD_PCT',
                                self.INTERFACE_UTIL_THRESHOLD_PCT,
                            )
                            if self._app else self.INTERFACE_UTIL_THRESHOLD_PCT
                        )
                        rx_breach = rx_util is not None and rx_util >= _threshold
                        tx_breach = tx_util is not None and tx_util >= _threshold
                        if rx_breach or tx_breach:
                            try:
                                from services.sse_broadcaster import broadcast_event
                                broadcast_event(
                                    'interface_threshold',
                                    _build_interface_threshold_payload(
                                        device_id,
                                        iface.name or f'if{if_index}',
                                        if_index,
                                        rx_util,
                                        tx_util,
                                        _threshold,
                                    ),
                                )
                            except Exception as _sse_err:
                                log.warning(
                                    "[InterfacePoller] interface_threshold broadcast error "
                                    "device=%s if=%s: %s",
                                    device_id, if_index, _sse_err,
                                )

                # Update snapshot for next poll cycle
                if curr_in is not None:
                    iface.last_in_octets = curr_in
                if curr_out is not None:
                    iface.last_out_octets = curr_out
                iface.last_poll_time = now

            # ── Update config tracking ──────────────────────────────────────
            config.last_successful_poll = now
            config.last_poll_error = None

            # Derive connection type from SNMP interface data for devices without an agent.
            # Agent-reported values ('wifi'/'lan') are never overwritten.
            if iface_list:
                _dev = db.session.get(Device, device_id)
                if _dev and _dev.connection_type in (None, 'unknown'):
                    _snmp_ct = classify_connection_type_from_interfaces(iface_list)
                    if _snmp_ct != 'unknown':
                        _dev.connection_type = _snmp_ct

            db.session.flush()  # assign interface_id to new rows before history FK

            log.info(
                f"[InterfacePoller] device_id={device_id} ({host}): "
                f"{len(iface_list)} interfaces upserted, "
                f"{rows_written} traffic rows written"
            )
            return {'success': True}

        except Exception as e:
            log.error(f"[InterfacePoller] Unhandled error for device_id={device_id}: {e}")
            try:
                db.session.rollback()
            except Exception:
                pass
            return {'success': False, 'error': str(e)[:500]}


# Singleton
interface_poller = InterfacePoller()
