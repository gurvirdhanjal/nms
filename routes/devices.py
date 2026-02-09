from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from extensions import db
from services.network_scanner import NetworkScanner
import asyncio
import json

devices_bp = Blueprint('devices_bp', __name__, url_prefix='')
scanner = NetworkScanner()

@devices_bp.route('/devices')
def device_management():
    if 'logged_in' not in session:
        return redirect(url_for('auth_bp.login'))
    
    from models.device import Device
    devices = Device.query.all()
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
            # Clean up scan history
            from models.scan_history import DeviceScanHistory
            DeviceScanHistory.query.filter_by(device_ip=device.device_ip).delete()
            
            db.session.delete(device)
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
        unclassified_count=unclassified_count
    )


@devices_bp.route('/devices/save', methods=['POST'])
def save_device():
    if 'logged_in' not in session:
        return redirect(url_for('auth_bp.login'))
    
    try:
        from models.device import Device
        device_id = request.form.get('device_id')
        device_name = request.form['device_name']
        device_ip = request.form['device_ip']
        device_type = request.form['device_type']
        port = request.form.get('port', '')
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        rstplink = request.form.get('rstplink', '')
        is_monitored = request.form.get('is_monitored') == 'on'

        # Generator Smart RTSP link based on brand if not provided
        if not rstplink and username and password and port and device_type == 'camera':
            encoded_password = password.replace('@', '%40').replace('#', '%23')
            brand = request.form.get('brand', '').lower()
            
            if brand == 'hikvision':
                rstplink = f"rtsp://{username}:{encoded_password}@{device_ip}:{port}/Streaming/Channels/101"
            elif brand == 'dahua':
                rstplink = f"rtsp://{username}:{encoded_password}@{device_ip}:{port}/cam/realmonitor?channel=1&subtype=0"
            elif brand == 'axis':
                rstplink = f"rtsp://{username}:{encoded_password}@{device_ip}:{port}/axis-media/media.amp"
            elif brand == 'uniview':
                rstplink = f"rtsp://{username}:{encoded_password}@{device_ip}:{port}/unicast/c1/s0/live"
            else:
                # Generic fallback
                rstplink = f"rtsp://{username}:{encoded_password}@{device_ip}:{port}/stream"

        # Get network information
        status, latency, _packet_loss = asyncio.run(scanner.ping_device(device_ip))
        
        if status == "Online":
            mac_address = scanner.get_mac_address(device_ip)
            hostname = scanner.get_hostname(device_ip)
            manufacturer = asyncio.run(scanner.get_manufacturer(mac_address))
        else:
            mac_address = request.form.get('macaddress', 'N/A')
            hostname = request.form.get('hostname', 'Unknown')
            manufacturer = "Unknown"

        if device_id:
            # Update existing device
            device = Device.query.get(device_id)
            device.device_name = device_name
            device.device_ip = device_ip
            device.device_type = device_type
            device.port = port
            device.rstplink = rstplink
            device.macaddress = mac_address
            device.hostname = hostname
            device.manufacturer = manufacturer
            device.is_monitored = is_monitored
        else:
            # Create new device
            device = Device(
                device_name=device_name,
                device_ip=device_ip,
                device_type=device_type,
                port=port,
                rstplink=rstplink,
                macaddress=mac_address,
                hostname=hostname,
                manufacturer=manufacturer,
                is_monitored=is_monitored
                
            )
            db.session.add(device)
        
        db.session.commit()
        return redirect(url_for('devices_bp.device_management'))
    
    except Exception as e:
        from models.device import Device
        devices = Device.query.all()
        return render_template('devices.html', devices=devices, error=f"Error saving device: {str(e)}")

@devices_bp.route('/api/devices')
def api_devices():
    # Auth handled by middleware

    
    from models.device import Device
    devices = Device.query.all()
    return jsonify([device.to_dict() for device in devices])

@devices_bp.route('/api/devices/<int:device_id>')
def api_device_detail(device_id):
    # Auth handled by middleware

    
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
        skipped_count = 0
        errors = []

        for data in devices_data:
            ip_address = data.get('ip', '').strip()
            hostname = data.get('hostname', 'Unknown').strip()
            mac_address = data.get('mac', 'N/A').strip()
            manufacturer = data.get('manufacturer', 'Unknown').strip()
            
            if not ip_address:
                continue

            # Check if exists (by IP or MAC if MAC is valid)
            existing = Device.query.filter_by(device_ip=ip_address).first()
            
            # Also check by MAC if we have one
            if not existing and mac_address and mac_address != 'N/A':
                 existing = Device.query.filter_by(macaddress=mac_address).first()

            if existing:
                skipped_count += 1
                continue
            
            try:
                device = Device(
                    device_name=hostname if hostname != 'Unknown' else f"Device-{ip_address}",
                    device_ip=ip_address,
                    device_type='Network Device',
                    macaddress=mac_address,
                    hostname=hostname,
                    manufacturer=manufacturer,
                    is_monitored=False, # Default to not monitored
                    is_active=True
                )
                db.session.add(device)
                added_count += 1
            except Exception as item_error:
                errors.append(f"Error adding {ip_address}: {str(item_error)}")

        db.session.commit()
        
        return jsonify({
            'success': True,
            'added': added_count,
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

        deleted_count = 0
        errors = []
        
        for dev_id in device_ids:
            try:
                device = Device.query.get(dev_id)
                if device:
                    # Clean up scan history
                    from models.scan_history import DeviceScanHistory
                    DeviceScanHistory.query.filter_by(device_ip=device.device_ip).delete()
                    
                    db.session.delete(device)
                    deleted_count += 1
            except Exception as e:
                errors.append(f"Error deleting ID {dev_id}: {str(e)}")
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'deleted': deleted_count,
            'errors': errors
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@devices_bp.route('/api/devices/<int:device_id>/update_type', methods=['POST'])
def update_device_type(device_id):
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
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
            hostname = device.hostname or "Unknown"
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

            # Update device
            device.device_type = result.device_type.value
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
