from flask import Blueprint, render_template, request, jsonify, session, current_app
from services.discovery_service import get_discovery_service
from services.device_identity import upsert_device_from_identity
from extensions import db
import json
import ipaddress
import logging
from middleware.rbac import require_login

# ===============================
# CONFIG (SAFETY FIRST)
# ===============================

MAX_HOSTS_PER_SCAN = 4096      # prevents system freeze
ALLOW_PUBLIC_NETWORKS = False # LAN only
PING_TIMEOUT = 2

# ===============================
# BLUEPRINT SETUP
# ===============================

scanning_bp = Blueprint('scanning_bp', __name__, url_prefix='')
logger = logging.getLogger(__name__)


def _normalize_snmp_version(version):
    normalized = (version or "2c").strip().lower().replace("v", "")
    if normalized in ("1", "2c", "3"):
        return normalized
    return "2c"


def _upsert_snmp_config_for_device(device, data):
    from models.snmp_config import DeviceSnmpConfig

    snmp_working = bool(data.get('snmp_working'))
    snmp_community = (data.get('snmp_community') or '').strip()
    if not (snmp_working and snmp_community):
        return

    snmp_version = _normalize_snmp_version(data.get('snmp_version', '2c'))
    snmp_port = int(data.get('snmp_port') or 161)

    config = DeviceSnmpConfig.query.filter_by(device_id=device.device_id).first()
    if not config:
        config = DeviceSnmpConfig(device_id=device.device_id)

    config.community_string = snmp_community
    config.snmp_version = snmp_version
    config.snmp_port = snmp_port
    config.is_enabled = bool(device.is_monitored)
    db.session.add(config)


@scanning_bp.before_request
@require_login
def _scanning_auth_guard():
    return None

# ===============================
# NETWORK DETECTION
# ===============================

# Function detect_local_network_cidr removed (replaced by service.scanner.get_local_ip_range)

def validate_network(cidr):
    if not cidr:
        return False, "No network CIDR provided"
    
    try:
        net = ipaddress.IPv4Network(cidr, strict=False)
    except Exception as e:
        return False, f"Invalid CIDR format: {str(e)}"

    if net.num_addresses > MAX_HOSTS_PER_SCAN:
        return False, f"Network too large ({net.num_addresses} hosts)"

    if not ALLOW_PUBLIC_NETWORKS and not net.is_private:
        return False, "Public networks not allowed"

    return True, net


# ===============================
# ROUTES
# ===============================

@scanning_bp.route('/scanner')
def scanner_page():
    return render_template('scanning.html')

@scanning_bp.route('/api/get_local_ip_range')
def get_local_ip_range():
    try:
        service = get_discovery_service()
        network = service.scanner.get_local_ip_range()
        return jsonify({'ip_range': network})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@scanning_bp.route('/api/scan_network', methods=['POST'])
def scan_network():
    # Auth handled by middleware

    data = request.get_json(silent=True) or {}
    ip_range = data.get('ip_range')
    requested_scan_mode = (data.get('scan_mode') or 'heavy').strip().lower()
    scan_mode = 'heavy'
    if requested_scan_mode != 'heavy':
        print(f"[DEBUG] Unsupported scan mode '{requested_scan_mode}' requested. Falling back to heavy mode.")
    
    print(f"[DEBUG] scan_network called. Mode: {scan_mode}")
    service = get_discovery_service()
    print(f"[DEBUG] Service instance in scan_network: {id(service)}")

    # If no IP range provided, try to detect automatically
    if not ip_range:
        try:
            ip_range = service.scanner.get_local_ip_range()
        except Exception:
             return jsonify({'error': 'Unable to detect network. Please enter IP range manually.'}), 400

    ok, result = validate_network(ip_range)
    if not ok:
        return jsonify({'error': result}), 400

    username = session.get('username', 'system')
    
    try:
        service = get_discovery_service()
        scan_id = service.start_scan(str(result), username, scan_mode=scan_mode)
        logger.info("Scan API start: id=%s user=%s range=%s", scan_id, username, str(result))
        return jsonify({'scan_id': scan_id, 'status': 'started', 'scan_mode': scan_mode}), 202
    except Exception as e:
        return jsonify({'error': str(e)}), 500
@scanning_bp.route('/api/scan_progress/<scan_id>')
def scan_progress(scan_id):
    service = get_discovery_service()
    print(f"[DEBUG] scan_progress called for {scan_id}. Service: {id(service)}")
    status = service.get_scan_status(scan_id)
    print(f"[DEBUG] Status for {scan_id}: {status is not None}")
    
    if not status:
        return jsonify({'error': 'Scan not found'}), 404
        
    return jsonify(status)

@scanning_bp.route('/api/active_scan')
def active_scan():
    # Auth handled by middleware
    
    username = session.get('username', 'system')
    service = get_discovery_service()
    scan_id = service.get_active_scan_id(username)
    
    if scan_id:
        # Get current progress immediately
        status = service.get_scan_status(scan_id)
        # Also return all accumulated devices so we can repopulate the table!
        devices = service.get_scan_results(scan_id)
        
        return jsonify({
            'scan_id': scan_id,
            'status': status['status'],
            'progress': status['progress'],
            'scanned_hosts': status['scanned_hosts'],
            'total_hosts': status['total_hosts'],
            'scan_mode': status.get('scan_mode'),
            'devices': devices # Send ALL devices to restore table
        })
    
    return jsonify({'scan_id': None})

@scanning_bp.route('/api/stop_scan/<scan_id>', methods=['POST'])
def stop_scan(scan_id):
    service = get_discovery_service()
    result = service.stop_scan(scan_id)
    if result.get('ok'):
        logger.info("Scan API stop: id=%s state=%s message=%s", scan_id, result.get('state'), result.get('message'))
        return jsonify({
            'status': result.get('state') or 'stopped',
            'message': result.get('message'),
            'already': bool(result.get('already'))
        })
    logger.warning("Scan API stop failed: id=%s state=%s message=%s", scan_id, result.get('state'), result.get('message'))
    if result.get('state') == 'not_found':
        return jsonify({'error': 'Scan not found'}), 404
    return jsonify({'error': result.get('message') or 'Unable to stop scan'}), 409

@scanning_bp.route('/api/ping_device', methods=['POST'])
def ping_device():
    # Auth handled by middleware

    ip = request.get_json().get('ip_address')
    if not ip:
        return jsonify({'error': 'IP required'}), 400

    import asyncio
    service = get_discovery_service()
    
    # We need to run async ping since we are in a sync route
    # Ideally, we should have an async route or use the service's executor if exposed
    # For now, creating a loop is safe enough for low volume
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        status, latency, packet_loss = loop.run_until_complete(
            service.scanner.ping_device(ip, timeout=PING_TIMEOUT)
        )
        
        # Return format expected by JavaScript
        if status == 'Online':
            return jsonify({
                'success': True,
                'latency': latency,
                'packet_loss': packet_loss,  # NEW: Include packet loss
                'ttl': 64,  # Standard TTL value
                'ip_address': ip
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Host is offline or unreachable',
                'packet_loss': packet_loss
            })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        })
    finally:
        loop.close()

@scanning_bp.route('/api/scan_ports', methods=['POST'])
def scan_ports():
    # Auth handled by middleware

    data = request.get_json()
    ip_address = data.get('ip_address')
    
    if not ip_address:
        return jsonify({'error': 'IP address required'}), 400

    import asyncio
    service = get_discovery_service()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # Re-use scanner from service
        open_ports = loop.run_until_complete(service.scanner.scan_ports(ip_address))
        return jsonify({
            'ip_address': ip_address,
            'open_ports': open_ports
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        loop.close()

@scanning_bp.route('/api/add_to_inventory', methods=['POST'])
def add_to_inventory():
    # Auth handled by middleware
    
    try:
        
        data = request.get_json()
        ip_address = data.get('ip_address', '').strip()
        hostname = data.get('hostname', 'Unknown').strip()
        mac_address = data.get('mac_address', 'N/A').strip()
        from services.device_classifier import DeviceClassifier
        device_type_raw = (data.get('device_type') or data.get('type') or '').strip()
        device_type = DeviceClassifier.normalize_device_type(device_type_raw)
        confidence_score = data.get('confidence_score')
        classification_confidence = (data.get('classification_confidence') or '').strip()
        classification_details = data.get('classification_details')
        
        if not ip_address:
            return jsonify({'success': False, 'message': 'IP address required'}), 400

        device, action, _prev_ip = upsert_device_from_identity(
            ip=ip_address,
            mac=mac_address,
            hostname=hostname or 'Unknown',
            manufacturer='Unknown',
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

        if action == "skipped":
            return jsonify({'success': False, 'message': 'IP address required'}), 400

        if action in ("created", "updated"):
            _upsert_snmp_config_for_device(device, data)
            db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Device added or updated successfully',
            'device': {
                'device_id': device.device_id,
                'device_ip': device.device_ip,
                'device_name': device.device_name,
                'macaddress': device.macaddress
            },
            'action': action
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@scanning_bp.route('/api/discovery/start', methods=['POST'])
def start_discovery():
    # Auth handled by middleware
    
    from flask import current_app
    from services.snmp_discovery_service import get_snmp_discovery_service
    
    data = request.json or {}
    seed_ip = data.get('seed_ip')
    if not seed_ip:
        return jsonify({"error": "seed_ip is required"}), 400

    # Start SNMP-based discovery in background thread
    app = current_app._get_current_object()
    svc = get_snmp_discovery_service()

    job_id = svc.start_job(
        seed_ip=seed_ip,
        app=app,
        community=data.get('community') or current_app.config.get('SNMP_COMMUNITY', 'public'),
        version=data.get('version') or current_app.config.get('SNMP_VERSION', '2c'),
        max_depth=int(data.get('max_depth', 3)),
        max_switches=int(data.get('max_switches', 50)),
        persist=bool(data.get('persist', True)),
        timeout=int(data.get('timeout', 2)),
        retries=int(data.get('retries', 1)),
        username=session.get('username', 'system')
    )

    return jsonify({
        "success": True,
        "message": "Discovery started in background",
        "job_id": job_id
    })


@scanning_bp.route('/api/discovery/status/<job_id>')
def discovery_status(job_id):
    # Auth handled by middleware

    from services.snmp_discovery_service import get_snmp_discovery_service

    svc = get_snmp_discovery_service()
    job = svc.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    return jsonify(job)


@scanning_bp.route('/api/discovery/active')
def discovery_active():
    # Auth handled by middleware

    from services.snmp_discovery_service import get_snmp_discovery_service
    svc = get_snmp_discovery_service()
    job = svc.get_active_job(session.get('username', 'system'))
    return jsonify({'active_job': job})
