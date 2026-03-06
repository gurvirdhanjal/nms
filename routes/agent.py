from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models.device import Device
from models.server_health import ServerHealthLog
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError
from middleware.rbac import require_agent_token

agent_bp = Blueprint('agent_bp', __name__)

_ALLOWED_HARDWARE_SPEC_KEYS = {
    'cpu_model',
    'cpu_physical_cores',
    'cpu_logical_cores',
    'memory_total_gb',
    'disk_total_gb',
    'architecture'
}


def _extract_hardware_specs(payload):
    raw_specs = payload.get('hardware_specs')
    if not isinstance(raw_specs, dict):
        return None

    specs = {}
    for key in _ALLOWED_HARDWARE_SPEC_KEYS:
        value = raw_specs.get(key)
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            specs[key] = value
    return specs or None

@agent_bp.route('/api/agent/metrics', methods=['POST'])
@require_agent_token
def receive_metrics():
    # Get device from decorator (already validated)
    device = request.agent_device

    # Enforce Postgres-only ingestion if configured
    if current_app.config.get('REQUIRE_POSTGRES_ONLY'):
        backend = db.engine.url.get_backend_name()
        if backend != 'postgresql':
            return jsonify({'error': f'Agent ingestion disabled for backend: {backend}'}), 503

    # Parse Payload
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400

    hostname = data.get('hostname')
    payload_ip = data.get('ip_address') or data.get('ip')
    if payload_ip and payload_ip.startswith('127.'):
        payload_ip = None
    ip_address = payload_ip or request.remote_addr
    print(f"[Agent] Incoming metrics from {ip_address} host={hostname} device_id={device.device_id}")
    # 3. Save Metrics (device already validated by decorator)
    try:
        # Ensure the device still exists (may have been deleted concurrently)
        try:
            db.session.refresh(device)
        except ObjectDeletedError:
            db.session.rollback()
            return jsonify({'error': f'Device was deleted: id={device.device_id}'}), 404

        # Basic metrics
        cpu_data = data.get('cpu', {}) if isinstance(data.get('cpu', {}), dict) else {}
        cpu = cpu_data.get('cpu_percent')
        cpu_iowait = cpu_data.get('cpu_iowait_percent')
        cpu_steal = cpu_data.get('cpu_steal_percent')
        try:
            cpu_iowait = float(cpu_iowait) if cpu_iowait is not None else None
        except (TypeError, ValueError):
            cpu_iowait = None
        try:
            cpu_steal = float(cpu_steal) if cpu_steal is not None else None
        except (TypeError, ValueError):
            cpu_steal = None
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
        tcp_retransmits_delta = net.get('tcp_retransmits_delta') if isinstance(net, dict) else None
        network_per_interface = net.get('per_interface_throughput') if isinstance(net, dict) else None
        try:
            tcp_retransmits_delta = int(tcp_retransmits_delta) if tcp_retransmits_delta is not None else None
        except (TypeError, ValueError):
            tcp_retransmits_delta = None
        if not isinstance(network_per_interface, dict):
            network_per_interface = None

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
        page_faults_per_sec = memory_data.get('page_faults_per_sec')
        try:
            page_faults_per_sec = float(page_faults_per_sec) if page_faults_per_sec is not None else None
        except (TypeError, ValueError):
            page_faults_per_sec = None

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
        disk_read_latency_ms = disk_io.get('read_latency_ms') if isinstance(disk_io, dict) else None
        disk_write_latency_ms = disk_io.get('write_latency_ms') if isinstance(disk_io, dict) else None
        disk_busy_percent = disk_io.get('busy_percent') if isinstance(disk_io, dict) else None
        try:
            disk_read_latency_ms = float(disk_read_latency_ms) if disk_read_latency_ms is not None else None
        except (TypeError, ValueError):
            disk_read_latency_ms = None
        try:
            disk_write_latency_ms = float(disk_write_latency_ms) if disk_write_latency_ms is not None else None
        except (TypeError, ValueError):
            disk_write_latency_ms = None
        try:
            disk_busy_percent = float(disk_busy_percent) if disk_busy_percent is not None else None
        except (TypeError, ValueError):
            disk_busy_percent = None

        # Network Connections
        net_conns = data.get('network_connections', {})
        network_connections_total = net_conns.get('total') if isinstance(net_conns, dict) else None
        network_connections_established = net_conns.get('established') if isinstance(net_conns, dict) else None
        network_connections_unique_ips = net_conns.get('unique_remote_ips_count') if isinstance(net_conns, dict) else None
        
        network_top_remote_ips = net_conns.get('top_remote_ips', [])
        if not isinstance(network_top_remote_ips, list):
            network_top_remote_ips = None

        # Processes
        processes = data.get('processes', {})
        process_count = processes.get('total_processes') if isinstance(processes, dict) else None
        zombie_count = processes.get('zombie_processes') if isinstance(processes, dict) else None
        context_switches_per_sec = processes.get('context_switches_per_sec') if isinstance(processes, dict) else None
        open_fds = processes.get('open_fds') if isinstance(processes, dict) else None
        fd_limit = processes.get('fd_limit') if isinstance(processes, dict) else None
        fd_percent = processes.get('fd_percent') if isinstance(processes, dict) else None
        try:
            context_switches_per_sec = float(context_switches_per_sec) if context_switches_per_sec is not None else None
        except (TypeError, ValueError):
            context_switches_per_sec = None
        try:
            open_fds = int(open_fds) if open_fds is not None else None
        except (TypeError, ValueError):
            open_fds = None
        try:
            fd_limit = int(fd_limit) if fd_limit is not None else None
        except (TypeError, ValueError):
            fd_limit = None
        try:
            fd_percent = float(fd_percent) if fd_percent is not None else None
        except (TypeError, ValueError):
            fd_percent = None

        # Top Processes (JSON)
        top_processes = data.get('top_processes', [])
        if not isinstance(top_processes, list):
            top_processes = None
        top_processes_cpu = data.get('top_processes_cpu', [])
        if not isinstance(top_processes_cpu, list):
            top_processes_cpu = None

        # Alerts (JSON)
        alerts = data.get('alerts', [])
        if not isinstance(alerts, list):
            alerts = None

        hardware_specs = _extract_hardware_specs(data)

        disk_data = data.get('disk', {}) if isinstance(data.get('disk', {}), dict) else {}
        log = ServerHealthLog(
            device_id=device.device_id,
            cpu_usage=cpu,
            cpu_iowait_percent=cpu_iowait,
            cpu_steal_percent=cpu_steal,
            memory_usage=memory,
            memory_used_gb=memory_used_gb,
            memory_total_gb=memory_total_gb,
            disk_usage=disk,
            disk_used_gb=disk_data.get('used_gb'),
            disk_free_gb=disk_data.get('free_gb'),
            disk_total_gb=disk_data.get('total_gb'),
            network_in_bps=net_in,
            network_out_bps=net_out,
            tcp_retransmits_delta=tcp_retransmits_delta,
            network_per_interface=network_per_interface,
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
            page_faults_per_sec=page_faults_per_sec,
            disk_read_bytes=disk_read_bytes,
            disk_write_bytes=disk_write_bytes,
            disk_read_count=disk_read_count,
            disk_write_count=disk_write_count,
            disk_read_latency_ms=disk_read_latency_ms,
            disk_write_latency_ms=disk_write_latency_ms,
            disk_busy_percent=disk_busy_percent,
            network_connections_total=network_connections_total,
            network_connections_established=network_connections_established,
            network_connections_unique_ips=network_connections_unique_ips,
            network_top_remote_ips=network_top_remote_ips,
            process_count=process_count,
            zombie_count=zombie_count,
            context_switches_per_sec=context_switches_per_sec,
            open_fds=open_fds,
            fd_limit=fd_limit,
            fd_percent=fd_percent,
            top_processes=top_processes,
            top_processes_cpu=top_processes_cpu,
            alerts=alerts,
            timestamp=datetime.utcnow()
        )
        db.session.add(log)

        # 5. Update Device Status
        device.is_active = True
        if hardware_specs:
            device.hardware_specs = hardware_specs
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

        # 4. Evaluate Server Health Alerts (strikes-based)
        try:
            from services.alert_manager import AlertManager
            AlertManager.check_server_health(device, log, commit=True)
        except Exception as alert_err:
            print(f"[Agent] Alert check failed for {device.device_ip}: {alert_err}")

        print(f"[Agent] Metrics saved for device_id={device.device_id} ip={device.device_ip}")

        response_payload = {'success': True}
        bootstrap_token = getattr(request, 'agent_bootstrap_assigned_token', None)
        if bootstrap_token:
            response_payload['agent_token'] = bootstrap_token
            response_payload['auth_mode'] = getattr(request, 'agent_auth_mode', 'shared_bootstrap')

        return jsonify(response_payload), 200

    except Exception as e:
        db.session.rollback()
        print(f"Error saving agent metrics: {e}")
        return jsonify({'error': str(e)}), 500
