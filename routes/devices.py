from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from werkzeug.security import generate_password_hash
from extensions import db
from services.network_scanner import NetworkScanner
from services.device_identity import upsert_device_from_identity, compute_subnet_cidr
from middleware.rbac import require_login
import asyncio
import json
import logging
from sqlalchemy import inspect, or_

devices_bp = Blueprint('devices_bp', __name__, url_prefix='')
logger = logging.getLogger(__name__)


def _normalize_snmp_version(version):
    normalized = (version or "2c").strip().lower().replace("v", "")
    if normalized in ("1", "2c", "3"):
        return normalized
    return "2c"


def _upsert_device_snmp_config(
    device_id,
    monitoring_mode,
    is_monitored,
    snmp_version,
    snmp_port,
    snmp_community,
    snmp_username,
    snmp_auth_proto,
    snmp_auth_password,
    snmp_priv_proto,
    snmp_priv_password,
):
    from models.snmp_config import DeviceSnmpConfig

    normalized_version = _normalize_snmp_version(snmp_version)
    existing = DeviceSnmpConfig.query.filter_by(device_id=device_id).first()
    should_track = bool(existing) or monitoring_mode in ("snmp", "agent") or bool((snmp_community or "").strip())
    if not should_track:
        return
    config = existing or DeviceSnmpConfig(device_id=device_id)

    config.snmp_version = normalized_version
    config.snmp_port = int(snmp_port or 161)
    config.community_string = (snmp_community or "public").strip() or "public"
    config.security_name = (snmp_username or "").strip() or None
    config.auth_protocol = (snmp_auth_proto or "").strip() or None
    config.auth_password = (snmp_auth_password or "").strip() or None
    config.priv_protocol = (snmp_priv_proto or "").strip() or None
    config.priv_password = (snmp_priv_password or "").strip() or None
    config.is_enabled = bool(is_monitored and monitoring_mode in ("snmp", "agent"))

    db.session.add(config)


@devices_bp.before_request
@require_login
def _devices_auth_guard():
    return None

from services.discovery_service import get_discovery_service


def _delete_device_with_dependencies(device, existing_tables=None):
    """Delete one device and its dependent rows that do not cascade automatically."""
    from models.device import Device
    from models.interfaces import DeviceInterface
    from models.topology import SwitchTopology

    device_id = device.device_id
    device_ip = device.device_ip
    if existing_tables is None:
        existing_tables = set(inspect(db.engine).get_table_names())

    interface_ids = [
        row[0]
        for row in db.session.query(DeviceInterface.interface_id).filter_by(device_id=device_id).all()
    ]

    # Break self/peer FK links first.
    Device.query.filter(Device.parent_switch_id == device_id).update(
        {Device.parent_switch_id: None},
        synchronize_session=False
    )
    if interface_ids:
        Device.query.filter(Device.parent_port_id.in_(interface_ids)).update(
            {Device.parent_port_id: None},
            synchronize_session=False
        )

    # Remove topology rows that point to this device or its interfaces.
    SwitchTopology.query.filter(
        or_(
            SwitchTopology.local_device_id == device_id,
            SwitchTopology.remote_device_id == device_id
        )
    ).delete(synchronize_session=False)
    if interface_ids:
        SwitchTopology.query.filter(
            SwitchTopology.local_interface_id.in_(interface_ids)
        ).delete(synchronize_session=False)

    # Cleanup tables without guaranteed FK cascade support.
    if 'device_scan_history' in existing_tables:
        from models.scan_history import DeviceScanHistory
        DeviceScanHistory.query.filter_by(device_ip=device_ip).delete(synchronize_session=False)

    if 'dashboard_events' in existing_tables:
        from models.dashboard import DashboardEvent
        DashboardEvent.query.filter_by(device_id=device_id).delete(synchronize_session=False)

    if 'daily_device_stats' in existing_tables:
        from models.dashboard import DailyDeviceStats
        DailyDeviceStats.query.filter_by(device_id=device_id).delete(synchronize_session=False)

    if 'device_snmp_config' in existing_tables:
        from models.snmp_config import DeviceSnmpConfig
        DeviceSnmpConfig.query.filter_by(device_id=device_id).delete(synchronize_session=False)

    if 'poll_tasks' in existing_tables:
        from models.poll_task import PollTask
        PollTask.query.filter_by(device_id=device_id).delete(synchronize_session=False)

    if 'server_health_hourly_rollups' in existing_tables:
        from models.server_health_rollups import ServerHealthHourlyRollup
        ServerHealthHourlyRollup.query.filter_by(device_id=device_id).delete(synchronize_session=False)

    if 'server_health_daily_rollups' in existing_tables:
        from models.server_health_rollups import ServerHealthDailyRollup
        ServerHealthDailyRollup.query.filter_by(device_id=device_id).delete(synchronize_session=False)

    db.session.delete(device)

@devices_bp.route('/devices')
def device_management():
    try:
        from models.device import Device
        from models.snmp_config import DeviceSnmpConfig
        devices = Device.query.order_by(Device.device_ip.asc()).all()
        device_ids = [d.device_id for d in devices]
        snmp_by_device_id = {}
        if device_ids:
            configs = DeviceSnmpConfig.query.filter(DeviceSnmpConfig.device_id.in_(device_ids)).all()
            snmp_by_device_id = {cfg.device_id: cfg for cfg in configs}
        for d in devices:
            cfg = snmp_by_device_id.get(d.device_id)
            d.snmp_enabled = bool(cfg.is_enabled) if cfg else False
            d.snmp_last_poll = cfg.last_successful_poll if cfg else None
            d.snmp_last_error = cfg.last_poll_error if cfg else None
        print(f"DEBUG: Found {len(devices)} devices in database")  # Debug line
        
        device = None
        
        prefill_data = None
        if request.args.get('prefill') == 'true':
            prefill_data = {
                'device_ip': request.args.get('ip'),
                'hostname': request.args.get('hostname'),
                'macaddress': request.args.get('mac')
            }

        if 'edit_id' in request.args:
            device = Device.query.get(request.args.get('edit_id'))
            print(f"DEBUG: Editing device {device}")  # Debug line

        if 'delete_id' in request.args:
            device = Device.query.get(request.args.get('delete_id'))
            if device:
                _delete_device_with_dependencies(device)
                db.session.commit()
                print(f"DEBUG: Deleted device {device.device_id}")  # Debug line
            return redirect(url_for('devices_bp.device_management'))

        # Count devices that still need auto-classification
        unclassified_count = 0
        for d in devices:
            dtype = (d.device_type or "").strip().lower()
            conf = (d.classification_confidence or "").strip().lower()
            if conf == "manual":
                continue
            if dtype in ("", "unknown", "network device"):
                unclassified_count += 1

        return render_template(
            'devices.html',
            devices=devices,
            device=device,
            prefill_data=prefill_data,
            unclassified_count=unclassified_count,
            subnets=sorted(set(d.subnet_cidr for d in devices if d.subnet_cidr))
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Internal Error: {str(e)} <br> <pre>{traceback.format_exc()}</pre>", 500


from services.snmp_service import snmp_service

@devices_bp.route('/api/check_connectivity', methods=['POST'])
def check_connectivity():
    data = request.get_json()
    ip = data.get('ip')
    mode = data.get('mode', 'ping')
    
    if not ip:
        return jsonify({'success': False, 'message': 'IP Address is required'})
    
    scanner = get_discovery_service().scanner
    
    try:
        if mode == 'ping':
            status, latency, packet_loss = asyncio.run(scanner.ping_device(ip, timeout=2, count=2))
            if status == 'Online':
                return jsonify({
                    'success': True, 
                    'message': f"Ping successful ({latency}ms)",
                    'latency': latency
                })
            else:
                return jsonify({'success': False, 'message': 'Ping failed (Host unreachable)'})
        
        elif mode == 'snmp':
            community = data.get('snmp_community', 'public')
            version = data.get('snmp_version', 'v2c')
            port = int(data.get('snmp_port', 161))
            
            # Use sync wrapper for simplicity or async if available
            sys_info = snmp_service.get_system_info(ip, community, version, port)
            
            if 'error' in sys_info:
                return jsonify({'success': False, 'message': f"SNMP Failed: {sys_info['error']}"})
            else:
                return jsonify({
                    'success': True,
                    'message': f"SNMP Connected: {sys_info.get('sys_descr', 'System info retrieved')}"
                })
                
        elif mode == 'agent':
            # Check tactical agent
            agent_info = asyncio.run(scanner.check_tactical_agent(ip))
            if agent_info:
                return jsonify({
                    'success': True,
                    'message': f"Agent Detected: {agent_info.get('agent_version', 'Unknown Version')}"
                })
            else:
                return jsonify({'success': False, 'message': 'Agent not detected on port 5002'})
        
        elif mode == 'wmi':
            # Basic port check for RPC (135) or SMB (445)
            # Using scanner.check_port
            is_rpc = asyncio.run(scanner.check_port(ip, 135, timeout=2))
            if is_rpc and is_rpc[1]:
                 return jsonify({'success': True, 'message': 'WMI Port (RPC 135) is reachable'})
            
            is_smb = asyncio.run(scanner.check_port(ip, 445, timeout=2))
            if is_smb and is_smb[1]:
                 return jsonify({'success': True, 'message': 'WMI Port (SMB 445) is reachable'})
                 
            return jsonify({'success': False, 'message': 'WMI Ports (135/445) unreachable'})
            
        else:
            return jsonify({'success': False, 'message': 'Unknown monitoring mode'})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@devices_bp.route('/devices/save', methods=['POST'])
def save_device():
    try:
        from models.device import Device
        device_id = request.form.get('device_id')
        device_name = request.form['device_name']
        device_ip = request.form['device_ip']
        device_type = request.form['device_type']
        
        # Identity
        hostname = request.form.get('hostname', 'Unknown')
        mac_address = request.form.get('macaddress', 'N/A')
        manufacturer = request.form.get('manufacturer', 'Unknown')
        location = request.form.get('location', '')
        description = request.form.get('description', '')
        
        # Monitoring Config
        is_monitored = request.form.get('is_monitored') == 'on'
        monitoring_mode = request.form.get('monitoring_mode', 'ping')
        
        # SNMP
        snmp_version = request.form.get('snmp_version', 'v2c')
        snmp_community = request.form.get('snmp_community', '')
        snmp_port = int(request.form.get('snmp_port', 161))
        snmp_timeout = int(request.form.get('snmp_timeout', 2))
        snmp_retries = int(request.form.get('snmp_retries', 1))
        snmp_username = request.form.get('snmp_username', '')
        snmp_auth_proto = request.form.get('snmp_auth_proto', '')
        snmp_auth_password = request.form.get('snmp_auth_password', '')
        snmp_priv_proto = request.form.get('snmp_priv_proto', '')
        snmp_priv_password = request.form.get('snmp_priv_password', '')

        # Agent
        agent_token = request.form.get('agent_token', '')
        agent_interval = int(request.form.get('agent_interval', 300))
        agent_os_type = request.form.get('agent_os_type', '')

        # WMI
        wmi_username = request.form.get('wmi_username', '')
        wmi_password = request.form.get('wmi_password', '')
        wmi_domain = request.form.get('wmi_domain', '')
        
        # Operational
        maintenance_mode = request.form.get('maintenance_mode') == 'on'

        # Device Credentials
        device_username = request.form.get('device_username', '').strip() or None
        device_password_raw = request.form.get('device_password', '').strip()

        # Legacy fields mapping
        port = request.form.get('port', str(snmp_port))
        rstplink = request.form.get('rstplink')
        if rstplink is not None:
            rstplink = rstplink.strip() or None

        # Get shared scanner instance
        scanner = get_discovery_service().scanner

        # Get network information - fast path only
        status, latency, _packet_loss = "Unknown", None, 0.0
        if is_monitored and monitoring_mode == 'ping':
            try:
                # Fast timeout
                status, latency, _packet_loss = asyncio.run(scanner.ping_device(device_ip, timeout=1, count=1))
            except Exception:
                status, latency, _packet_loss = "Offline", None, 100.0
        
        # NOTE: We skip synchronous MAC/Hostname enrichment here to prevent UI blocking.
        # The background scanner will pick this up later.


        if device_id:
            # Update existing device
            device = Device.query.get(device_id)
            device.device_name = device_name
            device.device_ip = device_ip
            device.device_type = device_type
            
            device.location = location
            device.description = description
            device.monitoring_mode = monitoring_mode
            
            # SNMP
            device.snmp_version = snmp_version
            device.snmp_community = snmp_community
            device.snmp_port = snmp_port
            device.snmp_timeout = snmp_timeout
            device.snmp_retries = snmp_retries
            device.snmp_username = snmp_username
            device.snmp_auth_proto = snmp_auth_proto
            device.snmp_auth_password = snmp_auth_password
            device.snmp_priv_proto = snmp_priv_proto
            device.snmp_priv_password = snmp_priv_password
            
            # Agent
            device.agent_token = agent_token
            device.agent_interval = agent_interval
            device.agent_os_type = agent_os_type
            
            # WMI
            device.wmi_username = wmi_username
            device.wmi_password = wmi_password
            device.wmi_domain = wmi_domain
            
            device.maintenance_mode = maintenance_mode
            
            # Legacy & Common
            device.port = port
            if rstplink is not None:
                device.rstplink = rstplink
            device.macaddress = mac_address
            device.hostname = hostname
            device.manufacturer = manufacturer
            device.is_monitored = is_monitored
            device.subnet_cidr = compute_subnet_cidr(device_ip)
            
            # Credentials
            device.device_username = device_username
            # Only update password hash if a new password was provided
            if device_password_raw:
                device.device_password_hash = generate_password_hash(
                    device_password_raw, method='pbkdf2:sha256', salt_length=16
                )
        else:
            # Create new device
            device = Device(
                device_name=device_name,
                device_ip=device_ip,
                device_type=device_type,
                location=location,
                description=description,
                monitoring_mode=monitoring_mode,
                subnet_cidr=compute_subnet_cidr(device_ip),
                
                # SNMP
                snmp_version=snmp_version,
                snmp_community=snmp_community,
                snmp_port=snmp_port,
                snmp_timeout=snmp_timeout,
                snmp_retries=snmp_retries,
                snmp_username=snmp_username,
                snmp_auth_proto=snmp_auth_proto,
                snmp_auth_password=snmp_auth_password,
                snmp_priv_proto=snmp_priv_proto,
                snmp_priv_password=snmp_priv_password,
                
                # Agent
                agent_token=agent_token,
                agent_interval=agent_interval,
                agent_os_type=agent_os_type,
                
                # WMI
                wmi_username=wmi_username,
                wmi_password=wmi_password,
                wmi_domain=wmi_domain,
                
                maintenance_mode=maintenance_mode,
                
                port=port,
                rstplink=rstplink,
                macaddress=mac_address,
                hostname=hostname,
                manufacturer=manufacturer,
                is_monitored=is_monitored,
                
                # Credentials
                device_username=device_username,
                device_password_hash=generate_password_hash(
                    device_password_raw, method='pbkdf2:sha256', salt_length=16
                ) if device_password_raw else None
            )
            db.session.add(device)

        db.session.flush()
        _upsert_device_snmp_config(
            device_id=device.device_id,
            monitoring_mode=monitoring_mode,
            is_monitored=is_monitored,
            snmp_version=snmp_version,
            snmp_port=snmp_port,
            snmp_community=snmp_community,
            snmp_username=snmp_username,
            snmp_auth_proto=snmp_auth_proto,
            snmp_auth_password=snmp_auth_password,
            snmp_priv_proto=snmp_priv_proto,
            snmp_priv_password=snmp_priv_password,
        )
        db.session.commit()
        return redirect(url_for('devices_bp.device_management'))
    
    except Exception as e:
        from models.device import Device
        devices = Device.query.all()
        return render_template('devices.html', devices=devices, error=f"Error saving device: {str(e)}")

@devices_bp.route('/api/devices/subnets')
def api_device_subnets():
    """Return sorted list of distinct subnet_cidr values."""
    from models.device import Device
    rows = db.session.query(Device.subnet_cidr).distinct().all()
    subnets = sorted([r[0] for r in rows if r[0]])
    return jsonify(subnets)

@devices_bp.route('/api/devices')
def api_devices():
    from models.device import Device
    query = Device.query.order_by(Device.device_ip.asc())
    # Optional subnet filter (backward-compatible: absent param = all devices)
    subnet_filter = request.args.get('subnet')
    if subnet_filter:
        query = query.filter_by(subnet_cidr=subnet_filter)
    device_dicts = [d.to_dict() for d in query.all()]
    return jsonify(device_dicts)

@devices_bp.route('/api/devices/<int:device_id>')
def api_device_detail(device_id):
    from models.device import Device
    device = Device.query.get(device_id)
    if device:
        return jsonify(device.to_dict())
    else:
        return jsonify({'error': 'Device not found'}), 404

@devices_bp.route('/api/devices/<int:device_id>/toggle_monitoring', methods=['POST'])
def toggle_device_monitoring(device_id):
    # Auth handled by middleware

    
    from models.device import Device
    device = Device.query.get(device_id)
    if device:
        device.is_monitored = not device.is_monitored
        db.session.commit()
        return jsonify({'success': True, 'is_monitored': device.is_monitored})
    else:
        return jsonify({'error': 'Device not found'}), 404

@devices_bp.route('/api/devices/bulk_add', methods=['POST'])
def bulk_add_devices():
    # Auth handled by middleware

    
    try:
        from models.device import Device
        
        devices_data = request.get_json()
        if not devices_data or not isinstance(devices_data, list):
             return jsonify({'error': 'Invalid data format. Expected a list of devices.'}), 400

        added_count = 0
        updated_count = 0
        skipped_count = 0
        errors = []

        seen_ips = set()
        
        for data in devices_data:
            ip_address = data.get('ip', '').strip()
            hostname = data.get('hostname', 'Unknown').strip()
            mac_address = data.get('mac', 'N/A').strip()
            manufacturer = data.get('manufacturer', 'Unknown').strip()
            from services.device_classifier import DeviceClassifier
            device_type_raw = (data.get('device_type') or data.get('type') or '').strip()
            device_type = DeviceClassifier.normalize_device_type(device_type_raw)
            confidence_score = data.get('confidence_score')
            classification_confidence = (data.get('classification_confidence') or '').strip()
            classification_details = data.get('classification_details')
            
            if not ip_address:
                continue
                
            if ip_address in seen_ips:
                continue
            seen_ips.add(ip_address)

            try:
                device, action, _prev_ip = upsert_device_from_identity(
                    ip=ip_address,
                    mac=mac_address,
                    hostname=hostname,
                    manufacturer=manufacturer,
                    device_type=device_type or 'unknown',
                    is_monitored=False,
                    is_active=True
                )

                # Apply classification metadata when available (avoid overwriting manual)
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
                    added_count += 1
                elif action == "updated":
                    updated_count += 1
                else:
                    skipped_count += 1

                _upsert_device_snmp_config(
                    device_id=device.device_id,
                    monitoring_mode='snmp' if data.get('snmp_working') else (device.monitoring_mode or 'ping'),
                    is_monitored=bool(device.is_monitored),
                    snmp_version=data.get('snmp_version') or device.snmp_version or '2c',
                    snmp_port=data.get('snmp_port') or device.snmp_port or 161,
                    snmp_community=data.get('snmp_community') or device.snmp_community or 'public',
                    snmp_username='',
                    snmp_auth_proto='',
                    snmp_auth_password='',
                    snmp_priv_proto='',
                    snmp_priv_password='',
                )
            except Exception as item_error:
                errors.append(f"Error adding {ip_address}: {str(item_error)}")

        db.session.commit()
        
        return jsonify({
            'success': True,
            'added': added_count,
            'updated': updated_count,
            'skipped': skipped_count,
            'errors': errors
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@devices_bp.route('/api/devices/bulk_delete', methods=['POST'])
def bulk_delete_devices():
    # Auth handled by middleware

    
    try:
        from models.device import Device
        
        data = request.get_json()
        if not data or 'device_ids' not in data:
             return jsonify({'error': 'Invalid data. Expected device_ids list.'}), 400
        
        device_ids = data['device_ids']
        if not isinstance(device_ids, list):
             return jsonify({'error': 'device_ids must be a list'}), 400
        logger.info("Bulk delete requested: count=%s", len(device_ids))

        # Stop active scans so deleted devices are not immediately re-added by scan completion.
        stopped_scans = 0
        service = get_discovery_service()
        with service.active_scans_lock:
            active_scan_ids = [
                scan_id for scan_id, scan in service.active_scans.items()
                if scan.get('status') == service.STATUS_SCANNING
            ]
        for scan_id in active_scan_ids:
            stop_result = service.stop_scan(scan_id)
            if stop_result.get('ok') and stop_result.get('state') == service.STATUS_STOPPED:
                stopped_scans += 1
        if stopped_scans:
            logger.info("Bulk delete pre-stop scans: stopped=%s", stopped_scans)

        deleted_count = 0
        errors = []
        existing_tables = set(inspect(db.engine).get_table_names())
        
        for dev_id in device_ids:
            try:
                with db.session.begin_nested():
                    device_query = Device.query.filter(Device.device_id == dev_id)
                    try:
                        device = device_query.with_for_update().first()
                    except Exception:
                        device = device_query.first()
                    if device:
                        _delete_device_with_dependencies(device, existing_tables=existing_tables)
                        deleted_count += 1
            except Exception as e:
                logger.warning("Bulk delete cleanup failure: device_id=%s error=%s", dev_id, e)
                errors.append(f"Error deleting ID {dev_id}: {str(e)}")
        
        db.session.commit()
        logger.info(
            "Bulk delete completed: requested=%s deleted=%s errors=%s stopped_scans=%s",
            len(device_ids),
            deleted_count,
            len(errors),
            stopped_scans
        )
        
        return jsonify({
            'success': True,
            'deleted': deleted_count,
            'errors': errors,
            'stopped_scans': stopped_scans
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@devices_bp.route('/api/devices/<int:device_id>/update_type', methods=['POST'])
def update_device_type(device_id):
    try:
        data = request.get_json()
        new_type = data.get('device_type')
        
        if not new_type:
            return jsonify({'error': 'Missing device_type'}), 400
            
        from models.device import Device
        device = Device.query.get(device_id)
        if not device:
            return jsonify({'error': 'Device not found'}), 404
            
        # Update device type
        device.device_type = new_type
        device.classification_confidence = 'Manual'
        device.confidence_score = 100
        
        db.session.commit()
        
        return jsonify({'success': True, 'device_type': new_type})
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@devices_bp.route('/api/devices/reclassify_all', methods=['GET'])
def reclassify_all():
    # Auth handled by middleware

    
    from models.device import Device
    from services.device_classifier import DeviceClassifier, DeviceSignals
    from flask import current_app
    import os

    # DEBUG: Print DB Path
    print(f"DEBUG DB URI: {current_app.config.get('SQLALCHEMY_DATABASE_URI')}")
    try:
        print(f"DEBUG Instance Path: {current_app.instance_path}")
    except:
        pass
    
    classifier = DeviceClassifier()
    devices = Device.query.all()
    updated_count = 0
    updated_devices = []
    force = request.args.get('force', 'false').lower() == 'true'
    auto_mode = request.args.get('auto', 'false').lower() == 'true'
    
    # Get shared scanner instance
    scanner = get_discovery_service().scanner

    print(f"[Reclassify] start devices={len(devices)} force={force} auto={auto_mode}")
    
    for device in devices:
        try:
            dtype = (device.device_type or "").strip().lower()
            conf = (device.classification_confidence or "").strip().lower()

            # Auto mode: only classify unknown / low-confidence, skip manual
            if not force:
                if conf == "manual":
                    continue
                if dtype not in ("", "unknown", "network device") and conf in ("medium", "high"):
                    continue
                if auto_mode and dtype not in ("", "unknown", "network device"):
                    continue

            # Ping first (ICMP may be blocked; do not rely on it for classification)
            status, _latency, _packet_loss = asyncio.run(scanner.ping_device(device.device_ip))

            mac_address = device.macaddress or "N/A"
            hostname = device.hostname or ""
            if not hostname or hostname.strip().lower() in ("unknown", "n/a", "na"):
                name_fallback = device.device_name or ""
                if name_fallback and name_fallback.strip().lower() not in ("unknown", "n/a", "na"):
                    hostname = name_fallback
                else:
                    hostname = "Unknown"
            manufacturer = device.manufacturer or "Unknown"

            if status == "Online":
                mac_address = scanner.get_mac_address(device.device_ip) or mac_address
                hostname = scanner.get_hostname(device.device_ip) or hostname

            if (manufacturer in ("Unknown", "N/A", "") and mac_address not in ("", "N/A", None)):
                try:
                    manufacturer = asyncio.run(scanner.get_manufacturer(mac_address))
                except:
                    pass

            # Port scan for classification (even if ping fails, ports might still be open)
            open_ports = asyncio.run(scanner.scan_ports(device.device_ip))
            port_numbers = [p.get("port") for p in open_ports if isinstance(p, dict)]

            signals = DeviceSignals(
                ip_address=device.device_ip,
                mac_address=mac_address,
                hostname=hostname,
                manufacturer=manufacturer,
                open_ports=port_numbers
            )

            result = classifier.classify(signals)
            normalized_type = DeviceClassifier.normalize_device_type(result.device_type)

            # Update device
            device.device_type = normalized_type
            device.confidence_score = result.score
            device.classification_confidence = result.confidence.value
            device.classification_details = json.dumps(result.to_dict())
            device.manufacturer = manufacturer
            device.macaddress = mac_address
            device.hostname = hostname

            updated_count += 1
            updated_devices.append({
                "device_id": device.device_id,
                "device_type": device.device_type,
                "classification_confidence": device.classification_confidence,
                "confidence_score": device.confidence_score
            })
        except Exception as e:
            print(f"[Reclassify] Failed for {device.device_ip}: {e}")
            
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f"Reclassified {updated_count} devices.",
        'updated_count': updated_count,
        'updated_devices': updated_devices,
        'db_uri': current_app.config.get('SQLALCHEMY_DATABASE_URI', 'unknown')
    })
@devices_bp.route('/api/devices/<int:device_id>', methods=['POST'])
def update_device(device_id):
    # Auth handled by middleware

        
    from models.device import Device
    device = Device.query.get_or_404(device_id)
    data = request.json or {}
    
    if 'switch_brand' in data:
        device.switch_brand = data['switch_brand']
    if 'device_type' in data:
        device.device_type = data['device_type']
    if 'cos_tier' in data:
        device.cos_tier = data['cos_tier']
    if 'is_monitored' in data:
        device.is_monitored = bool(data['is_monitored'])
    if 'parent_switch_id' in data:
        device.parent_switch_id = data['parent_switch_id']
    if 'parent_port_id' in data:
        device.parent_port_id = data['parent_port_id']
        
    db.session.commit()
    return jsonify({"success": True, "device": device.to_dict()})
