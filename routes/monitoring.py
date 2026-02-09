from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from extensions import db, event_manager
from services.device_monitor import DeviceMonitor
import asyncio
import time
from sqlalchemy.exc import OperationalError

monitoring_bp = Blueprint('monitoring_bp', __name__, url_prefix='')
monitor = DeviceMonitor()

@monitoring_bp.route('/dashboard')
def dashboard():
    if 'logged_in' not in session:
        return redirect(url_for('auth_bp.login'))
    
    from models.device import Device
    import ipaddress
    
    # Get basic stats for dashboard - SHOW ALL DEVICES
    try:
        # No more filtering by local range. Show everything in DB.
        # local_range = monitor.scanner.get_local_ip_range()
        # network = ipaddress.IPv4Network(local_range, strict=False)
        
        all_devices = Device.query.all()
        # filtered_devices = []
        # for d in all_devices: ...
        
        # Use all devices
        total_devices = len(all_devices)
        monitored_devices = len([d for d in all_devices if d.is_monitored])
        
    except Exception as e:
        print(f"DEBUG: Dashboard stats error: {e}")
        # Fallback to DB counts (show all devices)
        total_devices = Device.query.count()
        monitored_devices = total_devices  # Count all devices, not just monitored
    
    return render_template('dashboard.html', 
                         total_devices=total_devices,
                         monitored_devices=monitored_devices)

@monitoring_bp.route('/monitoring')
def monitoring_page():
    if 'logged_in' not in session:
        return redirect(url_for('auth_bp.login'))
    return render_template('monitoring.html')
import ipaddress

# ... existing imports ...

@monitoring_bp.route('/api/monitoring/status')
def get_monitoring_status():
    # Auth handled by middleware

    
    from models.device import Device
    device_type = request.args.get('device_type')
    status_filter = request.args.get('status')
    
    query = Device.query
    device_ip = request.args.get('device_ip')
    
    if device_ip:
        query = query.filter_by(device_ip=device_ip)
    
    if device_type and device_type != 'all':
        query = query.filter_by(device_type=device_type)
    
    devices = query.all()
    
    # NO FILTERING - SHOW ALL DEVICES
    # try:
    #     local_range = monitor.scanner.get_local_ip_range()
    #     network = ipaddress.IPv4Network(local_range, strict=False)
    #     ...
    # except Exception as e: ...
    
    # Just use the query result directly
    pass
    
    print(f"DEBUG: Status endpoint - Found {len(devices)} devices in local network") 
    
    devices_list = []

    async def fetch_device_status(device):
        # ... existing fetch_device_status logic ...
        try:
            # Optimization: Single ping for dashboard speed
            status, latency, _packet_loss = await monitor.scanner.ping_device(device.device_ip, count=1, timeout=1.5)
            
            # Fallback: Check Tactical Agent Port (5002) if Ping fails
            if status == 'Offline': 
                try:
                    agent_info = await monitor.scanner.check_tactical_agent(device.device_ip)
                    if agent_info:
                        status = 'Online'
                        if latency is None:
                            latency = 1.0 
                        print(f"DEBUG: Status check - {device.device_name} ({device.device_ip}) IS ONLINE via Agent")
                except:
                    pass

            print(f"DEBUG: Status check - {device.device_name} ({device.device_ip}): {status}")
            return {
                "device_id": device.device_id,
                "device_name": device.device_name,
                "device_ip": device.device_ip,
                "device_type": device.device_type,
                "macaddress": device.macaddress,
                "hostname": device.hostname,
                "manufacturer": device.manufacturer,
                "rstp_link": device.rstplink,
                "port": device.port,
                "is_monitored": device.is_monitored,
                "status": status,
                "latency": latency,
                "packet_loss": _packet_loss if '_packet_loss' in locals() else 0,
            }
        except Exception as e:
            print(f"DEBUG: Error checking {device.device_ip}: {e}")
            return {
                "device_id": device.device_id,
                "device_name": device.device_name,
                "device_ip": device.device_ip,
                "device_type": device.device_type,
                "macaddress": device.macaddress,
                "hostname": device.hostname,
                "manufacturer": device.manufacturer,
                "rstp_link": device.rstplink,
                "port": device.port,
                "is_monitored": device.is_monitored,
                "status": "Unknown",
                "latency": None,
            }

    async def fetch_all_statuses():
        tasks = [fetch_device_status(device) for device in devices]
        return await asyncio.gather(*tasks)

    try:
        devices_data = asyncio.run(fetch_all_statuses())
        
        # SAVE HISTORY TO DB (Synchronize Live View with Dashboard Stats)
        try:
            from extensions import db
            from models.scan_history import DeviceScanHistory
            from datetime import datetime

            history_entries = []
            for d in devices_data:
                # Basic validation
                if not d.get('device_ip'): continue
                
                # Determine packet loss safely
                pkt_loss = d.get('packet_loss', 0)
                if pkt_loss is None: pkt_loss = 0
                
                # Create history record
                entry = DeviceScanHistory(
                    device_ip=d['device_ip'],
                    device_name=d.get('device_name'),
                    status=d.get('status', 'Unknown'),
                    ping_time_ms=d.get('latency'),
                    packet_loss=pkt_loss,
                    scan_timestamp=datetime.utcnow(),
                    scan_type='live_check'
                )
                history_entries.append(entry)
            
            if history_entries:
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        db.session.add_all(history_entries)
                        db.session.commit()
                        print(f"DEBUG: Saved {len(history_entries)} scan records to history")
                        break
                    except OperationalError as e:
                        if "database is locked" in str(e) and attempt < max_retries - 1:
                            print(f"DEBUG: DB locked, retrying ({attempt+1}/{max_retries})...")
                            time.sleep(0.1 * (attempt + 1))
                            continue
                        else:
                            raise e


        except Exception as db_e:
            print(f"DEBUG: Error saving history in monitoring endpoint: {db_e}")
            db.session.rollback()

        # Apply status filter if provided
        if status_filter and status_filter != 'all':
            devices_data = [device for device in devices_data if device['status'] == status_filter]
        
        online_count = len([d for d in devices_data if d['status'] == 'Online'])
        print(f"DEBUG: Status endpoint - Returning {len(devices_data)} devices, {online_count} online")
        
        return jsonify({"devices": devices_data})
    
    except Exception as e:
        print(f"DEBUG: Error in status endpoint: {e}")
        return jsonify({"error": str(e)}), 500
    
@monitoring_bp.route('/api/monitoring/statistics')
def get_monitoring_statistics():
    # Auth handled by middleware

    
    try:
        from models.device import Device
        
        # NO FILTERING - SHOW ALL DEVICES
        # try:
        #   local_range = monitor.scanner.get_local_ip_range()
        #   ...
        
        all_devices = Device.query.all()
        total_devices = len(all_devices)
        monitored_devices = len([d for d in all_devices if d.is_monitored])
        devices_to_scan = all_devices # Scan everything
        
        # except Exception as e: ...
        
        print(f"DEBUG: Filtered stats: {total_devices} total devices, {monitored_devices} monitored")
        
        # Get REAL-TIME online status (not historical data)
        online_count = 0
        
        async def check_device_online(device):
            try:
                # 1. Try Standard Ping
                status, latency, _packet_loss = await monitor.scanner.ping_device(device.device_ip)
                if status == 'Online':
                    return True
                
                # 2. Try Tactical Agent Port (5002)
                # print(f"DEBUG: Ping failed for {device.device_ip}, checking Agent Port 5002...")
                agent_info = await monitor.scanner.check_tactical_agent(device.device_ip)
                if agent_info:
                     return True
                
                return False
            except Exception as e:
                # print(f"DEBUG: Error pinging {device.device_ip}: {e}")
                return False
        
        async def check_all_devices():
            tasks = [check_device_online(device) for device in devices_to_scan]
            return await asyncio.gather(*tasks)
        
        if devices_to_scan:
            try:
                online_results = asyncio.run(check_all_devices())
                online_count = sum(online_results)
                print(f"DEBUG: Real-time check: {online_count}/{len(devices_to_scan)} devices online")
            except Exception as e:
                print(f"DEBUG: Error in real-time check: {e}")
                online_count = 0
        else:
            online_count = 0
            
        stats = {
            'total_devices': total_devices,
            'monitored_devices': monitored_devices,
            'online_count': online_count,
            'offline_count': total_devices - online_count,
            'online_percentage': (online_count / total_devices * 100) if total_devices > 0 else 0,
        }
        
        return jsonify(stats)
    
    except Exception as e:
        print(f"DEBUG: Error in statistics endpoint: {e}") 
        return jsonify({"error": str(e)}), 500

@monitoring_bp.route('/api/monitoring/events')
def get_recent_events():
    """
    Get recent monitoring events.
    Returns JSON list of events.
    """
    # Auth handled by middleware

    
    try:
        # Get recent events
        events = event_manager.get_recent_events()

        # Serialize events
        events_data = [e.to_dict() for e in events]
        return jsonify({"events": events_data})
        
    except Exception as e:
        print(f"Error in events endpoint: {e}")
        return jsonify({"error": str(e)}), 500

@monitoring_bp.route('/api/monitoring/metrics')
def get_metrics():
    """
    Get aggregated metrics for a specific device and metric.
    Query params:
        - device_ip: Device IP address
        - metric_name: Name of the metric (e.g., network_latency_ms)
        - time_range: Time range (e.g., last_1h, last_24h). Default: last_24h
    """
    # Auth handled by middleware

        
    device_ip = request.args.get('device_ip')
    metric_name = request.args.get('metric_name')
    time_range = request.args.get('time_range', 'last_24h')
    
    if not device_ip or not metric_name:
         return jsonify({'error': 'Missing device_ip or metric_name'}), 400
    
    try:
        from metrics.aggregator import get_cutoff_time, aggregate_metrics
        
        # Get cutoff time
        cutoff = get_cutoff_time(time_range)
        
        # Fetch metrics from collector
        # Note: collector.get_metrics returns sorted list by timestamp
        raw_metrics = monitor.collector.get_metrics(device_ip, metric_name)
        
        # Filter metrics by cutoff time
        filtered_metrics = [m for m in raw_metrics if m.timestamp >= cutoff]
        
        # Aggregate
        result = aggregate_metrics(filtered_metrics)
        
        # Add metadata
        result['device_ip'] = device_ip
        result['metric_name'] = metric_name
        result['time_range'] = time_range
        
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in metrics endpoint: {e}")
        return jsonify({"error": str(e)}), 500