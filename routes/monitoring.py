from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from extensions import db, event_manager
from services.device_monitor import DeviceMonitor
import asyncio
import time
import logging
from sqlalchemy.exc import OperationalError
from middleware.rbac import require_login

monitoring_bp = Blueprint('monitoring_bp', __name__, url_prefix='')
monitor = DeviceMonitor()
logger = logging.getLogger(__name__)


from extensions import db, event_manager
from services.device_monitor import DeviceMonitor
import asyncio
import time
import logging
from sqlalchemy.exc import OperationalError
from middleware.rbac import require_login

monitoring_bp = Blueprint('monitoring_bp', __name__, url_prefix='')
monitor = DeviceMonitor()
logger = logging.getLogger(__name__)


@monitoring_bp.before_request
@require_login
def _monitoring_auth_guard():
    return None

@monitoring_bp.route('/dashboard')
def dashboard():
    from models.device import Device
    import ipaddress
    
    # Get basic stats for dashboard - SHOW ALL DEVICES
    
    # No more filtering by local range. Show everything in DB.
    # local_range = monitor.scanner.get_local_ip_range()
    # network = ipaddress.IPv4Network(local_range, strict=False)
    
    # RBAC: scope dashboard counts to current user's department/site (admins see all)
    from middleware.rbac import scoped_query
    all_devices_query = scoped_query(Device)
    
    all_devices = all_devices_query.all()
    # filtered_devices = []
    # for d in all_devices: ...

    monitored_devices = [d for d in all_devices if d.is_monitored]
    
    return render_template('dashboard.html',
                         all_devices=all_devices,
                         monitored_devices=monitored_devices)

@monitoring_bp.route('/dashboard/servers')
def server_dashboard():
    """Dedicated server operations dashboard for fleet-wide server monitoring."""
    return render_template('server_dashboard.html')

    
    # Use all devices
    total_devices = len(all_devices)
    monitored_devices = len([d for d in all_devices if d.is_monitored])
    
    return render_template('dashboard.html', 
                         total_devices=total_devices,
                         monitored_devices=monitored_devices)

@monitoring_bp.route('/monitoring')
def monitoring_page():
    # Monitoring tab is now dedicated to employee/device tracking.
    return redirect(url_for('tracking_bp.device_tracking'))
import ipaddress

# ... existing imports ...

@monitoring_bp.route('/api/monitoring/status')
def get_monitoring_status():
    # Auth handled by middleware

    
    from models.device import Device
    from models.scan_history import DeviceScanHistory
    from sqlalchemy import func
    from datetime import datetime
    from middleware.rbac import scoped_query
    device_type = request.args.get('device_type')
    status_filter = request.args.get('status')
    
    query = scoped_query(Device)
    device_ip = request.args.get('device_ip')
    raw_device_ids = request.args.getlist('device_ids')
    max_status_device_ids = 500

    parsed_device_ids = []
    if raw_device_ids:
        tokens = []
        for raw in raw_device_ids:
            if raw is None:
                continue
            tokens.extend(str(raw).split(','))

        seen_ids = set()
        for token in tokens:
            token = str(token).strip()
            if not token:
                continue
            if not token.isdigit():
                continue
            device_id = int(token)
            if device_id in seen_ids:
                continue
            seen_ids.add(device_id)
            parsed_device_ids.append(device_id)
            if len(parsed_device_ids) >= max_status_device_ids:
                break

    if raw_device_ids and not parsed_device_ids:
        return jsonify({"devices": []})
    
    if device_ip:
        query = query.filter_by(device_ip=device_ip)

    if parsed_device_ids:
        query = query.filter(Device.device_id.in_(parsed_device_ids))
    
    if device_type and device_type != 'all':
        dtype = (device_type or '').strip().lower()
        if dtype in ('camera', 'camera/iot', 'camera_iot', 'iot'):
            query = query.filter(Device.device_type.in_(['camera', 'camera/iot', 'camera_iot', 'iot']))
        else:
            query = query.filter_by(device_type=device_type)
    
    devices = query.all()
    mode = (request.args.get('mode') or '').lower()
    fallback_live = (request.args.get('fallback') or '').lower() in ('1', 'true', 'yes', 'live', 'ping')
    max_cached_status_age_seconds = 90
    max_live_fallback_devices = 24
    max_live_fallback_concurrency = 8
    per_device_live_timeout_seconds = 3.5

    def normalize_status(value):
        if value is None:
            return "Unknown"
        val = str(value).strip().lower()
        if val in ("online", "up"):
            return "Online"
        if val in ("offline", "down"):
            return "Offline"
        if val in ("maintenance", "maintaince"):
            return "Maintenance"
        if val in ("unknown", "n/a", "na", "none", "null", ""):
            return "Unknown"
        return val.capitalize()

    def normalize_availability_status(value):
        """Collapse statuses to Online/Offline/Maintenance for UI consistency."""
        normalized = normalize_status(value)
        if normalized == "Online":
            return "Online"
        if normalized == "Maintenance":
            return "Maintenance"
        return "Offline"

    def save_scan_history(history_entries):
        if not history_entries:
            return
        max_retries = 3
        for attempt in range(max_retries):
            try:
                db.session.add_all(history_entries)
                db.session.commit()
                logger.debug("Saved %d scan records to history", len(history_entries))
                break
            except OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    logger.debug("DB locked, retrying (%d/%d)...", attempt + 1, max_retries)
                    # An OperationalError aborts the transaction. Roll back before retrying
                    # so the session is clean for the next add_all() call.
                    try:
                        db.session.rollback()
                    except Exception:
                        pass
                    time.sleep(0.1 * (attempt + 1))
                    continue
                raise e

    if mode in ('latest', 'cached'):
        # Fast path: use latest scan history instead of live ping
        if not devices:
            return jsonify({"devices": []})

        target_device_ips = list({d.device_ip for d in devices if d.device_ip})
        latest_map = {}

        if target_device_ips:
            latest_subq = db.session.query(
                DeviceScanHistory.device_ip,
                func.max(DeviceScanHistory.scan_id).label('max_id')
            ).filter(
                DeviceScanHistory.device_ip.in_(target_device_ips)
            ).group_by(DeviceScanHistory.device_ip).subquery()

            latest_rows = db.session.query(DeviceScanHistory).join(
                latest_subq,
                (DeviceScanHistory.device_ip == latest_subq.c.device_ip) &
                (DeviceScanHistory.scan_id == latest_subq.c.max_id)
            ).all()
            latest_map = {row.device_ip: row for row in latest_rows}

        devices_list = []
        device_index = {}
        live_check_devices = []

        for device in devices:
            scan = latest_map.get(device.device_ip)
            if getattr(device, 'maintenance_mode', False):
                status = "Maintenance"
            else:
                status = normalize_availability_status(scan.status if scan else None)

            entry = {
                "device_id": device.device_id,
                "device_name": device.device_name,
                "device_ip": device.device_ip,
                "device_type": device.device_type,
                "status": status,
                "latency": scan.ping_time_ms if scan else None,
                "packet_loss": scan.packet_loss if scan else None,
                "maintenance_mode": getattr(device, 'maintenance_mode', False)
            }
            devices_list.append(entry)
            device_index[device.device_id] = entry

            scan_age_seconds = None
            if scan and scan.scan_timestamp:
                scan_age_seconds = max((datetime.utcnow() - scan.scan_timestamp).total_seconds(), 0.0)

            needs_live_check = False
            if fallback_live and not getattr(device, 'maintenance_mode', False) and device.device_ip:
                if status in ("Unknown", "Offline"):
                    needs_live_check = True
                elif scan_age_seconds is None or scan_age_seconds > max_cached_status_age_seconds:
                    needs_live_check = True

            if needs_live_check and len(live_check_devices) < max_live_fallback_devices:
                live_check_devices.append(device)

        if fallback_live and live_check_devices:
            logger.info(
                "Live refresh fallback triggered: candidates=%s limited=%s",
                len(live_check_devices),
                max_live_fallback_devices
            )

            async def ping_live_check_devices():
                sem = asyncio.Semaphore(max_live_fallback_concurrency)

                async def ping_one(device):
                    async with sem:
                        return await asyncio.wait_for(
                            monitor.scanner.ping_device(device.device_ip, count=1, timeout=1.5),
                            timeout=per_device_live_timeout_seconds
                        )

                tasks = [ping_one(d) for d in live_check_devices]
                return await asyncio.gather(*tasks, return_exceptions=True)

            try:
                ping_results = asyncio.run(ping_live_check_devices())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                ping_results = loop.run_until_complete(ping_live_check_devices())
                loop.close()

            history_entries = []
            fallback_errors = 0
            for device, result in zip(live_check_devices, ping_results):
                status = "Offline"
                latency = None
                packet_loss = None

                if isinstance(result, Exception):
                    fallback_errors += 1
                elif isinstance(result, tuple) and len(result) >= 3:
                    status, latency, packet_loss, *_ = result
                    status = normalize_availability_status(status)

                entry = device_index.get(device.device_id)
                if entry:
                    entry["status"] = status
                    entry["latency"] = latency
                    entry["packet_loss"] = packet_loss

                if status in ("Online", "Offline"):
                    pkt_loss = packet_loss if packet_loss is not None else 0
                    history_entries.append(DeviceScanHistory(
                        device_ip=device.device_ip,
                        device_name=device.device_name,
                        status=status,
                        ping_time_ms=latency,
                        packet_loss=pkt_loss,
                        scan_timestamp=datetime.utcnow(),
                        scan_type='live_check'
                    ))

            try:
                save_scan_history(history_entries)
            except Exception as db_e:
                logger.error("Error saving fallback history: %s", db_e)
                db.session.rollback()
            if fallback_errors:
                logger.warning(
                    "Live refresh fallback errors: count=%s total=%s",
                    fallback_errors,
                    len(live_check_devices)
                )

        if status_filter and status_filter != 'all':
            devices_list = [device for device in devices_list if device['status'].lower() == status_filter.lower()]

        return jsonify({"devices": devices_list})
    
    # NO FILTERING - SHOW ALL DEVICES
    # try:
    #     local_range = monitor.scanner.get_local_ip_range()
    #     network = ipaddress.IPv4Network(local_range, strict=False)
    #     ...
    # except Exception as e: ...
    
    logger.debug("Status endpoint - Found %d devices in local network", len(devices))

    devices_list = []

    async def fetch_device_status(device):
        # CHECK MAINTENANCE MODE FIRST
        if getattr(device, 'maintenance_mode', False):
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
                "status": "Maintenance",
                "latency": None,
                "packet_loss": 0,
            }

        try:
            # Optimization: Single ping for dashboard speed
            status, latency, _packet_loss, *_ = await monitor.scanner.ping_device(device.device_ip, count=1, timeout=1.5)
            status = normalize_availability_status(status)
            
            # Fallback: Check Tactical Agent Port (5002) if Ping fails
            if status == 'Offline': 
                try:
                    agent_info = await monitor.scanner.check_tactical_agent(device.device_ip)
                    if agent_info:
                        status = 'Online'
                        if latency is None:
                            latency = 1.0 
                        logger.debug("Status check - %s (%s) IS ONLINE via Agent", device.device_name, device.device_ip)
                except Exception:
                    pass

            logger.debug("Status check - %s (%s): %s", device.device_name, device.device_ip, status)
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
            logger.debug("Error checking %s: %s", device.device_ip, e)
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
                "status": "Offline",
                "latency": None,
            }

    async def fetch_all_statuses():
        tasks = [fetch_device_status(device) for device in devices]
        return await asyncio.gather(*tasks)

    try:
        devices_data = asyncio.run(fetch_all_statuses())
        
        # SAVE HISTORY TO DB (Synchronize Live View with Dashboard Stats)
        try:
            history_entries = []
            for d in devices_data:
                if not d.get('device_ip'):
                    continue

                pkt_loss = d.get('packet_loss', 0)
                if pkt_loss is None:
                    pkt_loss = 0

                entry = DeviceScanHistory(
                    device_ip=d['device_ip'],
                    device_name=d.get('device_name'),
                    status=normalize_status(d.get('status')),
                    ping_time_ms=d.get('latency'),
                    packet_loss=pkt_loss,
                    scan_timestamp=datetime.utcnow(),
                    scan_type='live_check'
                )
                history_entries.append(entry)

            save_scan_history(history_entries)
        except Exception as db_e:
            logger.error("Error saving history in monitoring endpoint: %s", db_e)
            db.session.rollback()

        # Apply status filter if provided
        if status_filter and status_filter != 'all':
            target = normalize_status(status_filter)
            devices_data = [device for device in devices_data if normalize_status(device.get('status')) == target]
        
        online_count = len([d for d in devices_data if d['status'] == 'Online'])
        logger.debug("Status endpoint - Returning %d devices, %d online", len(devices_data), online_count)
        
        return jsonify({"devices": devices_data})
    
    except Exception as e:
        logger.exception("Error in status endpoint")
        return jsonify({"error": "Internal server error"}), 500

@monitoring_bp.route('/api/monitoring/statistics')
def get_monitoring_statistics():
    # Auth handled by middleware

    
    try:
        from models.device import Device
        
        # NO FILTERING - SHOW ALL DEVICES
        # try:
        #   local_range = monitor.scanner.get_local_ip_range()
        #   ...
        
        all_devices = scoped_query(Device).all()
        total_devices = len(all_devices)
        monitored_devices = len([d for d in all_devices if d.is_monitored])
        devices_to_scan = all_devices # Scan everything
        
        # except Exception as e: ...
        
        logger.debug("Filtered stats: %d total devices, %d monitored", total_devices, monitored_devices)
        
        # Get REAL-TIME online status (not historical data)
        online_count = 0
        
        async def check_device_online(device):
            try:
                # 1. Try Standard Ping
                status, latency, _packet_loss, *_ = await monitor.scanner.ping_device(device.device_ip)
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
                logger.debug("Real-time check: %d/%d devices online", online_count, len(devices_to_scan))
            except Exception as e:
                logger.error("Error in real-time check: %s", e)
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
        logger.exception("Error in statistics endpoint")
        return jsonify({"error": "Internal server error"}), 500

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
        logger.exception("Error in events endpoint")
        return jsonify({"error": "Internal server error"}), 500

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
        logger.exception("Error in metrics endpoint")
        return jsonify({"error": "Internal server error"}), 500
