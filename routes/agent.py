from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models.device import Device
from models.server_health import ServerHealthLog
from datetime import datetime

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
    # Use remote addr if agent didn't send IP (though agent usually sends what it sees)
    # But for "Server Agent", we rely on what we can match.
    # The user script doesn't explicitly send "ip" in top level, calls it implicitly via request
    # script: collect_metrics() -> hostname, os_info, etc. No explicit IP.
    # So we use request.remote_addr
    ip_address = request.remote_addr

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
        cpu = data.get('cpu', {}).get('cpu_percent')
        memory = data.get('memory', {}).get('percent')
        disk = data.get('disk', {}).get('percent')
        uptime = data.get('uptime_seconds')
        
        net = data.get('network', {})
        throughput = net.get('throughput', {}) if isinstance(net, dict) else {}
        net_in = throughput.get('recv_bps')
        net_out = throughput.get('sent_bps')

        # Fallback to byte counters if throughput is not provided
        if net_in is None and isinstance(net, dict):
            net_in = net.get('bytes_recv')
        if net_out is None and isinstance(net, dict):
            net_out = net.get('bytes_sent')

        os_info = data.get('os_info', {}) if isinstance(data.get('os_info', {}), dict) else {}
        os_name = os_info.get('os')
        os_version = os_info.get('os_version')
        os_arch = os_info.get('architecture')

        log = ServerHealthLog(
            device_id=device.device_id,
            cpu_usage=cpu,
            memory_usage=memory,
            disk_usage=disk,
            network_in_bps=net_in,
            network_out_bps=net_out,
            uptime=str(uptime),
            source='agent',
            os_name=os_name,
            os_version=os_version,
            os_arch=os_arch,
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
        
        db.session.commit()
        
        return jsonify({'success': True}), 200

    except Exception as e:
        db.session.rollback()
        print(f"Error saving agent metrics: {e}")
        return jsonify({'error': str(e)}), 500
