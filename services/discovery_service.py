import asyncio
import threading
import uuid
import json
import logging
import time
from datetime import datetime
from services.network_scanner import NetworkScanner

try:
    from flask import current_app
except ImportError:
    current_app = None

# Global instance fallback
_discovery_service = None
logger = logging.getLogger(__name__)

def get_discovery_service():
    # Try to use current_app if available (Flask context)
    if current_app:
        try:
            if not hasattr(current_app, 'discovery_service'):
                current_app.discovery_service = DiscoveryService()
            return current_app.discovery_service
        except RuntimeError:
            # Working outside of application context
            pass
            
    global _discovery_service
    if _discovery_service is None:
        _discovery_service = DiscoveryService()
    return _discovery_service

class DiscoveryService:
    STATUS_SCANNING = 'scanning'
    STATUS_STOPPED = 'stopped'
    STATUS_COMPLETED = 'completed'
    STATUS_ERROR = 'error'

    ALLOWED_STATUS_TRANSITIONS = {
        STATUS_SCANNING: {STATUS_STOPPED, STATUS_COMPLETED, STATUS_ERROR},
        STATUS_STOPPED: set(),
        STATUS_COMPLETED: set(),
        STATUS_ERROR: set(),
    }

    def __init__(self):
        logger.debug("DiscoveryService Initialized: %d", id(self))
        self.scanner = NetworkScanner()
        self.active_scans = {}
        self.active_scans_lock = threading.Lock()
        
    def start_scan(self, ip_range, username='system', scan_mode='heavy'):
        """
        Start a new background scan.
        Returns the scan_id.
        """
        if (scan_mode or '').strip().lower() != 'heavy':
            scan_mode = 'heavy'

        scan_id = str(uuid.uuid4())
        app_obj = None
        if current_app:
            try:
                app_obj = current_app._get_current_object()
            except RuntimeError:
                app_obj = None
        
        # Calculate approximate host count for initial stats
        try:
            import ipaddress
            net = ipaddress.IPv4Network(ip_range, strict=False)
            total_hosts = net.num_addresses
        except:
            total_hosts = 0

        with self.active_scans_lock:
            self.active_scans[scan_id] = {
                'id': scan_id,
                'devices': [],       # List of all found devices (accumulated)
                'new_devices': [],   # Buffer for polling (cleared on read)
                'status': self.STATUS_SCANNING,
                'progress': 0,
                'total_found': 0,
                'scanned_hosts': 0,
                'total_hosts': total_hosts,
                'start_time': datetime.utcnow().isoformat(),
                'username': username,
                'ip_range': ip_range,
                'scan_mode': scan_mode,
                'stop': False,
                'error': None,
                'saved': False
            }
        logger.info("Scan started: id=%s user=%s range=%s mode=%s", scan_id, username, ip_range, scan_mode)

        # Start background thread
        t = threading.Thread(
            target=self._run_async_scan_wrapper,
            args=(scan_id, ip_range, scan_mode, app_obj),
            daemon=True
        )
        t.start()
        
        return scan_id

    def stop_scan(self, scan_id):
        """Stop a running scan with idempotent behavior."""
        with self.active_scans_lock:
            scan = self.active_scans.get(scan_id)
            if not scan:
                return {'ok': False, 'state': 'not_found', 'message': 'scan not found'}

            current_status = (scan.get('status') or '').strip().lower()
            if current_status == self.STATUS_STOPPED:
                return {'ok': True, 'state': self.STATUS_STOPPED, 'message': 'already stopped', 'already': True}

            if current_status in (self.STATUS_COMPLETED, self.STATUS_ERROR):
                return {
                    'ok': True,
                    'state': current_status,
                    'message': f'scan already {current_status}; no transition applied',
                    'already': True
                }

            scan['stop'] = True
            transitioned = self._transition_scan_status(scan, self.STATUS_STOPPED)
            if transitioned:
                logger.info("Scan stopped: id=%s", scan_id)
                return {'ok': True, 'state': self.STATUS_STOPPED, 'message': 'stop requested'}

            logger.warning(
                "Scan stop transition blocked: id=%s current=%s requested=%s",
                scan_id,
                current_status or 'unknown',
                self.STATUS_STOPPED
            )
            return {
                'ok': False,
                'state': current_status or 'unknown',
                'message': f'invalid transition {current_status} -> {self.STATUS_STOPPED}'
            }

    def get_scan_status(self, scan_id):
        """
        Get status and *newly discovered* devices since last call.
        Clears the 'new_devices' buffer.
        """
        with self.active_scans_lock:
            scan = self.active_scans.get(scan_id)
            if not scan:
                return None

            # Pop new devices for the UI
            new_devices = list(scan['new_devices'])
            scan['new_devices'] = []  # Clear buffer

            return {
                'id': scan_id,
                'status': scan['status'],
                'progress': scan['progress'],
                'total_found': scan['total_found'],
                'scanned_hosts': scan['scanned_hosts'],
                'total_hosts': scan['total_hosts'],
                'scan_mode': scan.get('scan_mode'),
                'new_devices': new_devices,
                'error': scan.get('error'),
                'saved': scan.get('saved', False)
            }

    def get_scan_results(self, scan_id):
        """
        Get ALL discovered devices for a scan (not just new ones).
        """
        with self.active_scans_lock:
            scan = self.active_scans.get(scan_id)
            if not scan:
                return None
            # Return a copy of the list
            return list(scan['devices'])

    def get_active_scan_id(self, username='system'):
        """
        Find an active scan ID for the user.
        """
        with self.active_scans_lock:
            for scan_id, scan in self.active_scans.items():
                if scan['status'] == self.STATUS_SCANNING and scan.get('username') == username:
                    return scan_id
        return None

    def trigger_settings_subnet_scan(self, subnets, username='system', app=None):
        """Run a background scan for configured subnets and persist classified results."""
        if not isinstance(subnets, list):
            return 0

        normalized_subnets = []
        for raw in subnets:
            subnet = str(raw or '').strip()
            if not subnet:
                continue
            if subnet not in normalized_subnets:
                normalized_subnets.append(subnet)

        if not normalized_subnets:
            return 0

        worker = threading.Thread(
            target=self._run_settings_subnet_scan_worker,
            args=(normalized_subnets, username, app),
            daemon=True,
        )
        worker.start()
        return len(normalized_subnets)

    def _run_settings_subnet_scan_worker(self, subnets, username='system', app=None):
        """Scan each configured subnet with classifier-enabled scanner and upsert results."""
        started = time.time()
        total_added = 0
        total_updated = 0
        errors = []

        for subnet in subnets:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                logger.info("[DiscoverySettings] Auto scan started: user=%s subnet=%s", username, subnet)
                devices = loop.run_until_complete(
                    self.scanner.scan_network_range_incremental(
                        subnet,
                        scan_id=None,
                        active_scans=None,
                        active_scans_lock=None,
                        scan_mode='heavy',
                    )
                )
                save_result = self._save_scan_results(scan_id=None, devices=devices, app=app)
                total_added += int(save_result.get('added', 0))
                total_updated += int(save_result.get('updated', 0))
                logger.info(
                    "[DiscoverySettings] Auto scan finished: subnet=%s added=%s updated=%s",
                    subnet,
                    int(save_result.get('added', 0)),
                    int(save_result.get('updated', 0)),
                )
            except Exception as scan_error:
                logger.exception("[DiscoverySettings] Auto scan failed: subnet=%s", subnet)
                errors.append(f"{subnet}: {scan_error}")
            finally:
                loop.close()

        if app is None:
            return

        try:
            with app.app_context():
                from models.discovery_config import get_config
                from extensions import db

                cfg = get_config()
                cfg.last_heavy_scan = datetime.utcnow()
                cfg.last_scan_duration = round(time.time() - started, 2)
                cfg.last_new_count = total_added
                cfg.last_updated_count = total_updated
                cfg.last_error = "; ".join(errors)[:1000] if errors else None
                db.session.commit()
        except Exception:
            logger.exception("[DiscoverySettings] Failed to persist post-scan metadata")

    def _run_async_scan_wrapper(self, scan_id, ip_range, scan_mode='heavy', app=None):
        """
        Wrapper to run async scanner in a thread.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # We pass self.active_scans and lock so the scanner can update progress/check stop
            # The NetworkScanner.scan_network_range_incremental expects these
            devices = loop.run_until_complete(
                self.scanner.scan_network_range_incremental(
                    ip_range,
                    scan_id,
                    self.active_scans,
                    self.active_scans_lock,
                    scan_mode=scan_mode
                )
            )

            # Mark complete
            with self.active_scans_lock:
                if scan_id in self.active_scans:
                    scan = self.active_scans[scan_id]
                    if scan['status'] != self.STATUS_ERROR:  # Don't overwrite error status
                        # If stopped, keep stopped; otherwise mark as completed.
                        if scan['stop']:
                            self._transition_scan_status(scan, self.STATUS_STOPPED)
                        else:
                            self._transition_scan_status(scan, self.STATUS_COMPLETED)
                            scan['progress'] = 100
                        # Final sync of devices just in case
                        scan['devices'] = devices
                    logger.info("Scan finished: id=%s state=%s total=%s", scan_id, scan.get('status'), len(devices or []))
        except Exception as e:
            import traceback
            traceback.print_exc()
            with self.active_scans_lock:
                if scan_id in self.active_scans:
                    self._transition_scan_status(self.active_scans[scan_id], self.STATUS_ERROR)
                    self.active_scans[scan_id]['error'] = str(e)
            logger.exception("Scan failed: id=%s", scan_id)
        finally:
            loop.close()

    def _transition_scan_status(self, scan, new_status):
        """Apply scan status transition if allowed by state machine."""
        current_status = (scan.get('status') or '').strip().lower()
        if current_status == new_status:
            return True
        allowed = self.ALLOWED_STATUS_TRANSITIONS.get(current_status, set())
        if new_status not in allowed:
            return False
        scan['status'] = new_status
        if new_status in (self.STATUS_STOPPED, self.STATUS_COMPLETED, self.STATUS_ERROR):
            scan['end_time'] = datetime.utcnow().isoformat()
        return True

    def _save_scan_results(self, scan_id, devices, app=None):
        if not devices:
            return {'added': 0, 'updated': 0}

        def _persist():
            from extensions import db
            from services.device_identity import upsert_device_from_identity
            from services.device_classifier import DeviceClassifier
            from models.discovery_config import get_config
            from models.subnet import Subnet

            cfg = get_config()
            approval_mode = (cfg.auto_add_policy or 'auto') == 'approval'
            monitor_new = bool(cfg.auto_monitor_new)

            count_added = 0
            count_updated = 0

            from models.device import Device as _Device
            from services.device_identity import find_device_by_mac, find_device_by_hostname

            for device_data in devices:
                try:
                    ip = device_data.get('ip')
                    if not ip:
                        continue

                    device_type_raw = (device_data.get('device_type') or device_data.get('type') or '').strip()
                    device_type = DeviceClassifier.normalize_device_type(device_type_raw)
                    confidence_score = device_data.get('confidence_score')
                    classification_confidence = (device_data.get('classification_confidence') or '').strip()
                    classification_details = device_data.get('classification_details')

                    # no_autoflush: a previous iteration may have left a device object dirty
                    # in the session (modified classification_confidence / confidence_score).
                    # Without this guard, the lookup queries below trigger SQLAlchemy's
                    # autoflush, which tries to UPDATE that device row — racing with the SNMP
                    # worker's row lock and causing a LockNotAvailable error that aborts the
                    # whole batch. Dirty state is flushed cleanly at db.session.commit() below.
                    with db.session.no_autoflush:
                        # Resolve site_id from subnet mapping for new devices
                        best_subnet = Subnet.get_best_match(ip)
                        site_id = best_subnet.site_id if best_subnet else None

                        # In approval mode, skip creating new devices (only update existing)
                        mac = device_data.get('mac')
                        hostname = device_data.get('hostname') or ''
                        is_existing = bool(
                            (mac and find_device_by_mac(mac))
                            or find_device_by_hostname(hostname)
                            or _Device.query.filter_by(device_ip=ip).first()
                        )
                        if approval_mode and not is_existing:
                            logger.debug("[Discovery] Approval mode: skipping new device %s", ip)
                            continue

                        device, action, _prev_ip = upsert_device_from_identity(
                            ip=ip,
                            mac=mac,
                            hostname=hostname or 'Unknown',
                            manufacturer=device_data.get('manufacturer') or 'Unknown',
                            device_type=device_type or 'unknown',
                            is_monitored=monitor_new,
                            is_active=True,
                            site_id=site_id,
                        )

                    if device and (classification_confidence or confidence_score is not None or classification_details):
                        if (device.classification_confidence or '').strip().lower() != 'manual':
                            if classification_confidence:
                                device.classification_confidence = classification_confidence
                            if confidence_score is not None:
                                device.confidence_score = confidence_score
                            if classification_details is not None:
                                if not isinstance(classification_details, str):
                                    classification_details = json.dumps(classification_details)
                                device.classification_details = classification_details

                    if action == "created":
                        count_added += 1
                    elif action == "updated":
                        count_updated += 1
                except Exception as e:
                    # Rollback to prevent session pollution from affecting subsequent devices
                    db.session.rollback()
                    logger.error("[Discovery] Failed to add device %s: %s", device_data.get('ip', 'unknown'), e)
                    continue

            if count_added > 0 or count_updated > 0:
                try:
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    logger.error("[Discovery] Failed to commit scan results: %s", e)

            with self.active_scans_lock:
                if scan_id and scan_id in self.active_scans:
                    self.active_scans[scan_id]['saved'] = True
            return {'added': count_added, 'updated': count_updated}

        if app is None:
            return {'added': 0, 'updated': 0}

        try:
            with app.app_context():
                return _persist()
        except Exception as e:
            logger.error("[Discovery] Failed to save scan results: %s", e)
            return {'added': 0, 'updated': 0}
    def start_recursive_discovery(self, seed_ip):
        """
        Start a recursive discovery process starting from a seed IP.
        (Phase 1 implementation: Single-depth neighbor scan)
        """
        from services.ssh_service import SSHService
        from models import Device, db
        
        logger.info("[Discovery] Starting recursive scan from %s", seed_ip)

        # 1. Ensure seed device exists
        seed_device = Device.query.filter_by(device_ip=seed_ip).first()
        if not seed_device:
            logger.info("[Discovery] Seed device %s not found in inventory. Adding placeholder.", seed_ip)
            seed_device = Device(
                device_ip=seed_ip, 
                device_name=f"Seed-Core-{seed_ip}",
                device_type='switch',
                is_monitored=True
            )
            db.session.add(seed_device)
            db.session.commit()
            
        # 2. Get Neighbors via SSH
        ssh_svc = SSHService()
        neighbors = ssh_svc.get_lldp_neighbors(seed_device)
        logger.info("[Discovery] Found %d neighbors for %s", len(neighbors), seed_ip)
        
        # 3. Process Neighbors
        for n in neighbors:
            remote_ip = n.get('remote_ip')
            if not remote_ip: continue
            
            # Check if exists
            neighbor = Device.query.filter_by(device_ip=remote_ip).first()
            if not neighbor:
                neighbor = Device(
                    device_ip=remote_ip,
                    device_name=n.get('remote_hostname', f"Discovered-{remote_ip}"),
                    device_type='switch', # Assume switch for now
                    parent_switch_id=seed_device.device_id, # Temporary direct link
                    last_discovery_method='LLDP'
                )
                db.session.add(neighbor)
                logger.info("[Discovery] Added new neighbor %s", remote_ip)
            else:
                # Update topology info
                neighbor.parent_switch_id = seed_device.device_id
                neighbor.last_discovery_method = 'LLDP'
                
            db.session.commit()

    def map_devices_to_ports(self):
        """
        Analyze topology table (if populated) and update parent/child relationships.
        For now, this is a placeholder or basic implementation.
        """
        logger.info("[Discovery] Mapping devices to ports...")
        # In a real implementation effectively use SwitchTopology table
        # to set parent_port_id on devices.
        pass
