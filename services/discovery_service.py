import asyncio
import threading
import uuid
import json
import logging
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
        print(f"[DEBUG] DiscoveryService Initialized: {id(self)}")
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
            return

        def _persist():
            from extensions import db
            from services.device_identity import upsert_device_from_identity
            from services.device_classifier import DeviceClassifier

            count_added = 0
            count_updated = 0

            for device_data in devices:
                ip = device_data.get('ip')
                if not ip:
                    continue

                device_type_raw = (device_data.get('device_type') or device_data.get('type') or '').strip()
                device_type = DeviceClassifier.normalize_device_type(device_type_raw)
                confidence_score = device_data.get('confidence_score')
                classification_confidence = (device_data.get('classification_confidence') or '').strip()
                classification_details = device_data.get('classification_details')

                device, action, _prev_ip = upsert_device_from_identity(
                    ip=ip,
                    mac=device_data.get('mac'),
                    hostname=device_data.get('hostname') or 'Unknown',
                    manufacturer=device_data.get('manufacturer') or 'Unknown',
                    device_type=device_type or 'unknown',
                    is_monitored=True,
                    is_active=True
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

            if count_added > 0 or count_updated > 0:
                db.session.commit()

            with self.active_scans_lock:
                if scan_id in self.active_scans:
                    self.active_scans[scan_id]['saved'] = True

        if app is None:
            return

        try:
            with app.app_context():
                _persist()
        except Exception as e:
            print(f"[Discovery] Failed to save scan results: {e}")
    def start_recursive_discovery(self, seed_ip):
        """
        Start a recursive discovery process starting from a seed IP.
        (Phase 1 implementation: Single-depth neighbor scan)
        """
        from services.ssh_service import SSHService
        from models import Device, db
        
        print(f"[Discovery] Starting recursive scan from {seed_ip}")
        
        # 1. Ensure seed device exists
        seed_device = Device.query.filter_by(device_ip=seed_ip).first()
        if not seed_device:
            print(f"[Discovery] Seed device {seed_ip} not found in inventory. Adding placeholder.")
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
        print(f"[Discovery] Found {len(neighbors)} neighbors for {seed_ip}")
        
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
                print(f"[Discovery] Added new neighbor {remote_ip}")
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
        print("[Discovery] Mapping devices to ports...")
        # In a real implementation effectively use SwitchTopology table
        # to set parent_port_id on devices.
        pass
