from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models.device import Device
from models.server_health import ServerHealthLog
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

agent_bp = Blueprint('agent_bp', __name__)

@agent_bp.route('/api/agent/metrics', methods=['POST'])
def receive_metrics():
    # 1. Verify Token
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Missing or invalid Authorization header'}), 401
    
    token = auth_header.split(' ')[1]
    expected_token = current_app.config.get('API_KEY') # Matches TRACKING_API_KEY in .env

    if token != expected_token:
        # Also check MOBILE_API_KEY as fallback if configured differently
        mobile_key = current_app.config.get('MOBILE_API_KEY')
        if not mobile_key or token != mobile_key:
             return jsonify({'error': 'Invalid token'}), 403

    # Enforce Postgres-only ingestion if configured
    if current_app.config.get('REQUIRE_POSTGRES_ONLY'):
        backend = db.engine.url.get_backend_name()
        if backend != 'postgresql':
            return jsonify({'error': f'Agent ingestion disabled for backend: {backend}'}), 503

    # 2. Parse Payload
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400

    hostname = data.get('hostname')
    payload_ip = data.get('ip_address') or data.get('ip')
    if payload_ip and payload_ip.startswith('127.'):
        payload_ip = None
    ip_address = payload_ip or request.remote_addr
    print(f"[Agent] Incoming metrics from {ip_address} host={hostname}")
    # Use remote addr if agent didn't send IP (though agent usually sends what it sees)
    # But for "Server Agent", we rely on what we can match.
    # The user script doesn't explicitly send "ip" in top level, calls it implicitly via request
    # script: collect_metrics() -> hostname, os_info, etc. No explicit IP.
    # So we use request.remote_addr
    # Prefer agent-provided IP when available

    # 3. Find Device
    # Match by IP first (most reliable for "manually config server")
    device = Device.query.filter_by(device_ip=ip_address).first()
    
    if not device:
        # Try finding by hostname
        if hostname:
            device = Device.query.filter(Device.device_name.ilike(hostname)).first()
            if not device:
                device = Device.query.filter(Device.hostname.ilike(hostname)).first()

    if not device:
        return jsonify({'error': f'Device not found for IP {ip_address} or Hostname {hostname}. Please add it to devices first.'}), 404

    # 4. Save Metrics
    try:
        # Ensure the device still exists (may have been deleted concurrently)
        try:
            db.session.refresh(device)
        except ObjectDeletedError:
            db.session.rollback()
            return jsonify({'error': f'Device was deleted: id={device.device_id}'}), 404

        # Basic metrics
        cpu = data.get('cpu', {}).get('cpu_percent')
        memory_data = data.get('memory', {}) if isinstance(data.get('memory', {}), dict) else {}
        memory = memory_data.get('percent')
        disk = data.get('disk', {}).get('percent')
        uptime = data.get('uptime_seconds')
        
        # Network I/O
        net = data.get('network', {})
        throughput = net.get('throughput', {}) if isinstance(net, dict) else {}
        net_in = throughput.get('recv_bps')
        net_out = throughput.get('sent_bps')

        # Fallback to byte counters if throughput is not provided
        if net_in is None and isinstance(net, dict):
            net_in = net.get('bytes_recv')
        if net_out is None and isinstance(net, dict):
            net_out = net.get('bytes_sent')

        # OS Info
        os_info = data.get('os_info', {}) if isinstance(data.get('os_info', {}), dict) else {}
        os_name = os_info.get('os')
        os_version = os_info.get('os_version')
        os_arch = os_info.get('architecture')

        # Load Average
        load_avg = data.get('load_average', {})
        load_avg_1min = load_avg.get('1min') if isinstance(load_avg, dict) else None
        load_avg_5min = load_avg.get('5min') if isinstance(load_avg, dict) else None
        load_avg_15min = load_avg.get('15min') if isinstance(load_avg, dict) else None

        # Swap Memory
        swap_total_mb = memory_data.get('swap_total_mb')
        swap_used_mb = memory_data.get('swap_used_mb')
        swap_percent = memory_data.get('swap_percent')

        # Memory details (GB)
        memory_used_gb = memory_data.get('used_gb')
        memory_total_gb = memory_data.get('total_gb')
        if memory_used_gb is None or memory_total_gb is None:
            used_mb = memory_data.get('used_mb')
            total_mb = memory_data.get('total_mb')
            if used_mb is not None and total_mb is not None:
                memory_used_gb = round(used_mb / 1024, 2)
                memory_total_gb = round(total_mb / 1024, 2)

        # Disk I/O
        disk_io = data.get('disk_io', {})
        disk_read_bytes = disk_io.get('read_bytes') if isinstance(disk_io, dict) else None
        disk_write_bytes = disk_io.get('write_bytes') if isinstance(disk_io, dict) else None
        disk_read_count = disk_io.get('read_count') if isinstance(disk_io, dict) else None
        disk_write_count = disk_io.get('write_count') if isinstance(disk_io, dict) else None

        # Network Connections
        net_conns = data.get('network_connections', {})
        network_connections_total = net_conns.get('total') if isinstance(net_conns, dict) else None
        network_connections_established = net_conns.get('established') if isinstance(net_conns, dict) else None

        # Processes
        processes = data.get('processes', {})
        process_count = processes.get('total_processes') if isinstance(processes, dict) else None
        zombie_count = processes.get('zombie_processes') if isinstance(processes, dict) else None

        # Top Processes (JSON)
        top_processes = data.get('top_processes', [])
        if not isinstance(top_processes, list):
            top_processes = None

        # Alerts (JSON)
        alerts = data.get('alerts', [])
        if not isinstance(alerts, list):
            alerts = None

        disk_data = data.get('disk', {}) if isinstance(data.get('disk', {}), dict) else {}
        log = ServerHealthLog(
            device_id=device.device_id,
            cpu_usage=cpu,
            memory_usage=memory,
            memory_used_gb=memory_used_gb,
            memory_total_gb=memory_total_gb,
            disk_usage=disk,
            disk_used_gb=disk_data.get('used_gb'),
            disk_free_gb=disk_data.get('free_gb'),
            disk_total_gb=disk_data.get('total_gb'),
            network_in_bps=net_in,
            network_out_bps=net_out,
            uptime=str(uptime),
            source='agent',
            os_name=os_name,
            os_version=os_version,
            os_arch=os_arch,
            load_avg_1min=load_avg_1min,
            load_avg_5min=load_avg_5min,
            load_avg_15min=load_avg_15min,
            swap_total_mb=swap_total_mb,
            swap_used_mb=swap_used_mb,
            swap_percent=swap_percent,
            disk_read_bytes=disk_read_bytes,
            disk_write_bytes=disk_write_bytes,
            disk_read_count=disk_read_count,
            disk_write_count=disk_write_count,
            network_connections_total=network_connections_total,
            network_connections_established=network_connections_established,
            process_count=process_count,
            zombie_count=zombie_count,
            top_processes=top_processes,
            alerts=alerts,
            timestamp=datetime.utcnow()
        )
        db.session.add(log)

        # 5. Update Device Status
        device.is_active = True
        # Update specific fields if they are missing
        if device.hostname == 'Unknown' and hostname:
            device.hostname = hostname
        
        # We don't have a specific "last_seen" on Device model typically, 
        # but we can update updated_at or if there's a specific tracking field.
        # device.updated_at is auto-updated.
        
        try:
            db.session.commit()
        except (IntegrityError, StaleDataError) as commit_err:
            db.session.rollback()
            return jsonify({'error': f'Device no longer exists. {commit_err}'}), 409

        # 6. Evaluate Server Health Alerts (strikes-based)
        try:
            from services.alert_manager import AlertManager
            AlertManager.check_server_health(device, log, commit=True)
        except Exception as alert_err:
            print(f"[Agent] Alert check failed for {device.device_ip}: {alert_err}")

        print(f"[Agent] Metrics saved for device_id={device.device_id} ip={device.device_ip}")
        
        return jsonify({'success': True}), 200

    except Exception as e:
        db.session.rollback()
        print(f"Error saving agent metrics: {e}")
        return jsonify({'error': str(e)}), 500
