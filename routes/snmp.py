"""
SNMP API routes for Network Monitoring System.
Provides endpoints for SNMP configuration and polling.
"""
from flask import Blueprint, jsonify, request
from datetime import datetime
from extensions import db
from middleware.rbac import require_login

snmp_bp = Blueprint('snmp_bp', __name__, url_prefix='/api/snmp')


@snmp_bp.before_request
@require_login
def _snmp_auth_guard():
    return None


# ============================================================
# GET /api/snmp/poll/<device_id>
# ============================================================
@snmp_bp.route('/poll/<int:device_id>')
def poll_device(device_id):
    """
    Poll a specific device for SNMP data.
    Returns system info and interface list.
    """
    try:
        from models.device import Device
        from models.snmp_config import DeviceSnmpConfig
        from services.snmp_service import snmp_service
        
        device = Device.query.get(device_id)
        if not device:
            return jsonify({'error': 'Device not found'}), 404
        
        # Get SNMP config or use defaults
        snmp_config = DeviceSnmpConfig.query.filter_by(device_id=device_id).first()
        
        community = snmp_config.community_string if snmp_config else 'public'
        version = snmp_config.snmp_version if snmp_config else '2c'
        port = snmp_config.snmp_port if snmp_config else 161
        
        # Poll device
        system_info = snmp_service.get_system_info(
            device.device_ip, community, version, port
        )
        
        if 'error' in system_info:
            # Update last poll error
            if snmp_config:
                snmp_config.last_poll_error = system_info['error']
                db.session.commit()
            return jsonify({
                'device_id': device_id,
                'device_ip': device.device_ip,
                'error': system_info['error']
            }), 500
        
        # Get server health (CPU/RAM/Disk via SNMP)
        health_metrics = snmp_service.get_server_health_snmp(
            device.device_ip, community, version, port
        )
        
        if health_metrics:
            from models.server_health import ServerHealthLog
            log = ServerHealthLog(
                device_id=device_id,
                cpu_usage=health_metrics.get('cpu_usage'),
                memory_usage=health_metrics.get('memory_usage'),
                disk_usage=health_metrics.get('disk_usage'),
                uptime=str(system_info.get('sys_uptime_seconds', '')),
                source='snmp'
            )
            db.session.add(log)
            # Add to response for debug
            system_info['health'] = health_metrics

        # Get interfaces
        interfaces = snmp_service.get_interfaces(
            device.device_ip, community, version, port
        )
        
        # Update last successful poll
        if snmp_config:
            snmp_config.last_successful_poll = datetime.utcnow()
            snmp_config.last_poll_error = None
            db.session.commit()
        
        return jsonify({
            'device_id': device_id,
            'device_ip': device.device_ip,
            'device_name': device.device_name,
            'system': system_info,
            'interfaces': interfaces,
            'polled_at': datetime.utcnow().isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# GET /api/snmp/test
# ============================================================
@snmp_bp.route('/test')
def test_snmp():
    """
    Test SNMP connectivity to a device.
    Query params: ip, community, version, port
    """
    ip = request.args.get('ip')
    if not ip:
        return jsonify({'error': 'Missing ip parameter'}), 400
    
    community = request.args.get('community', 'public')
    version = request.args.get('version', '2c')
    port = int(request.args.get('port', 161))
    
    try:
        from services.snmp_service import snmp_service
        
        result = snmp_service.get_system_info(ip, community, version, port)
        
        if 'error' in result:
            return jsonify({
                'success': False,
                'ip': ip,
                'error': result['error']
            })
        
        return jsonify({
            'success': True,
            'ip': ip,
            'system': result
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'ip': ip,
            'error': str(e)
        }), 500


# ============================================================
# POST /api/snmp/config
# ============================================================
@snmp_bp.route('/config', methods=['POST'])
def save_snmp_config():
    """
    Save SNMP configuration for a device.
    Body: { device_id, community_string, snmp_version, snmp_port, poll_interval_seconds, is_enabled }
    """
    try:
        from models.device import Device
        from models.snmp_config import DeviceSnmpConfig
        
        data = request.get_json()
        device_id = data.get('device_id')
        
        if not device_id:
            return jsonify({'error': 'Missing device_id'}), 400
        
        device = Device.query.get(device_id)
        if not device:
            return jsonify({'error': 'Device not found'}), 404
        
        # Find or create config
        snmp_config = DeviceSnmpConfig.query.filter_by(device_id=device_id).first()
        
        if not snmp_config:
            snmp_config = DeviceSnmpConfig(device_id=device_id)
            db.session.add(snmp_config)
        
        # Update fields
        if 'community_string' in data:
            snmp_config.community_string = data['community_string']
        if 'snmp_version' in data:
            snmp_config.snmp_version = data['snmp_version']
        if 'snmp_port' in data:
            snmp_config.snmp_port = data['snmp_port']
        if 'poll_interval_seconds' in data:
            snmp_config.poll_interval_seconds = data['poll_interval_seconds']
        if 'is_enabled' in data:
            snmp_config.is_enabled = data['is_enabled']
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'config': snmp_config.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ============================================================
# GET /api/snmp/config/<device_id>
# ============================================================
@snmp_bp.route('/config/<int:device_id>')
def get_snmp_config(device_id):
    """Get SNMP configuration for a device."""
    try:
        from models.snmp_config import DeviceSnmpConfig
        
        config = DeviceSnmpConfig.query.filter_by(device_id=device_id).first()
        
        if not config:
            return jsonify({
                'device_id': device_id,
                'configured': False,
                'defaults': {
                    'community_string': 'public',
                    'snmp_version': '2c',
                    'snmp_port': 161
                }
            })
        
        return jsonify({
            'device_id': device_id,
            'configured': True,
            'config': config.to_dict()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# GET /api/snmp/interfaces/<device_id>
# ============================================================
@snmp_bp.route('/interfaces/<int:device_id>')
def get_device_interfaces(device_id):
    """
    Get stored interfaces for a device.
    Use ?refresh=true to poll live data.
    """
    try:
        from models.device import Device
        from models.interfaces import DeviceInterface
        from models.snmp_config import DeviceSnmpConfig
        from services.snmp_service import snmp_service
        
        device = Device.query.get(device_id)
        if not device:
            return jsonify({'error': 'Device not found'}), 404
        
        refresh = request.args.get('refresh', 'false').lower() == 'true'
        
        if refresh:
            # Poll live data
            snmp_config = DeviceSnmpConfig.query.filter_by(device_id=device_id).first()
            community = snmp_config.community_string if snmp_config else 'public'
            version = snmp_config.snmp_version if snmp_config else '2c'
            port = snmp_config.snmp_port if snmp_config else 161
            
            interfaces = snmp_service.get_interfaces(
                device.device_ip, community, version, port
            )
            
            # Upsert interfaces to DB
            for if_data in interfaces:
                existing = DeviceInterface.query.filter_by(
                    device_id=device_id,
                    if_index=if_data.get('if_index')
                ).first()
                
                if existing:
                    existing.name = if_data.get('name')
                    existing.if_type = str(if_data.get('if_type'))
                    existing.speed_bps = if_data.get('speed_bps')
                    existing.mac_address = if_data.get('mac_address')
                    existing.admin_status = if_data.get('admin_status')
                    existing.oper_status = if_data.get('oper_status')
                else:
                    new_if = DeviceInterface(
                        device_id=device_id,
                        if_index=if_data.get('if_index'),
                        name=if_data.get('name'),
                        if_type=str(if_data.get('if_type')),
                        speed_bps=if_data.get('speed_bps'),
                        mac_address=if_data.get('mac_address'),
                        admin_status=if_data.get('admin_status'),
                        oper_status=if_data.get('oper_status')
                    )
                    db.session.add(new_if)
            
            db.session.commit()
        
        # Return stored interfaces
        interfaces = DeviceInterface.query.filter_by(device_id=device_id).all()
        
        return jsonify({
            'device_id': device_id,
            'device_name': device.device_name,
            'interfaces': [i.to_dict() for i in interfaces],
            'count': len(interfaces)
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ============================================================
# POST /api/snmp/poll-counters/<device_id>
# ============================================================
@snmp_bp.route('/poll-counters/<int:device_id>', methods=['POST'])
def poll_interface_counters(device_id):
    """
    Poll interface traffic counters and store metrics.
    Calculates bandwidth utilization from counter deltas.
    """
    try:
        from services.interface_poller import interface_poller
        
        result = interface_poller.poll_device_interfaces(device_id)
        
        if result.get('success'):
            return jsonify(result)
        else:
            return jsonify(result), 500
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# POST /api/snmp/poll-all
# ============================================================
@snmp_bp.route('/poll-all', methods=['POST'])
def poll_all_devices():
    """Poll all SNMP-enabled devices for interface counters."""
    try:
        from services.interface_poller import interface_poller
        
        result = interface_poller.poll_all_devices()
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# GET /api/snmp/utilization/<interface_id>
# ============================================================
@snmp_bp.route('/utilization/<int:interface_id>')
def get_interface_utilization(interface_id):
    """
    Get bandwidth utilization history for an interface.
    Query params: minutes (default: 60)
    """
    minutes = request.args.get('minutes', 60, type=int)
    
    try:
        from services.interface_poller import interface_poller
        
        result = interface_poller.get_interface_utilization(interface_id, minutes)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
