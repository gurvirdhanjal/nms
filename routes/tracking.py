from flask import Blueprint, render_template, jsonify, request, session, redirect, url_for, Response
from extensions import db
from models.tracked_device import TrackedDevice, DeviceScanHistory, DeviceActivityLog, DeviceResourceLog, DeviceApplicationLog
from datetime import datetime, timedelta
import requests
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
import platform
import subprocess
import psutil
import ipaddress
import time
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import io

tracking_bp = Blueprint('tracking_bp', __name__)

# Use centralized config for API key
from config import Config
SHARED_API_KEY = Config.API_KEY

def generate_placeholder_image(text="No Feed"):
    """Generate a placeholder image with text"""
    img = Image.new('RGB', (640, 480), color=(73, 109, 137))
    d = ImageDraw.Draw(img)
    
    # Try to use a font, fallback to default if not available
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except:
        font = ImageFont.load_default()
    
    # Get text size and center it
    bbox = d.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    position = ((640 - text_width) / 2, (480 - text_height) / 2)
    d.text(position, text, fill=(255, 255, 255), font=font)
    
    # Convert to JPEG bytes
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG', quality=80)
    img_byte_arr.seek(0)
    
    return (b'--frame\r\n'
            b'Content-Type: image/jpeg\r\n\r\n' + 
            img_byte_arr.read() + 
            b'\r\n')


class NetworkScanner:
    def __init__(self):
        self.timeout = 2.0  # Increased to 2.0s for reliability
        self.max_workers = 100
    
    def get_mac_address(self, ip_address):
        """Get MAC address for an IP"""
        try:
            startupinfo = None
            creationflags = 0
            if platform.system().lower() == "windows":
                cmd = ["arp", "-a", ip_address]
                # Stop terminal window from popping up
                if hasattr(subprocess, 'CREATE_NO_WINDOW'):
                     creationflags = subprocess.CREATE_NO_WINDOW
                else:
                     # Fallback for older python or non-standard envs
                     startupinfo = subprocess.STARTUPINFO()
                     startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            else:
                cmd = ["arp", "-n", ip_address]
            
            # Safe subprocess call with suppression flags
            arp_output = subprocess.check_output(
                cmd, 
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
                creationflags=creationflags
            ).decode('utf-8', errors='ignore')
            
            for line in arp_output.splitlines():
                if ip_address in line:
                    parts = line.split()
                    for part in parts:
                        if ':' in part or '-' in part:
                            return part.upper().replace('-', ':')
        except:
            pass
        return "N/A"
    
    def check_port_open(self, ip, port=5002):
        """Check if port is open"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex((ip, port))
            sock.close()
            return result == 0
        except:
            return False
    
    def check_tracking_service(self, ip, port=5002):
        """Check if tracking service is running"""
        try:
            # Try identity endpoint first (avoid false negatives from raw port checks)
            identity_response = None
            try:
                identity_response = requests.get(
                    f"http://{ip}:{port}/api/identity",
                    timeout=self.timeout
                )
            except requests.exceptions.RequestException as e:
                # Network/timeout/etc. We'll fall back to raw port check below.
                identity_response = None
            except Exception as e:
                print(f"[DEBUG] Identity check exception on {ip}: {e}")
                identity_response = None

            if identity_response is not None:
                if identity_response.status_code == 200:
                    identity_data = identity_response.json()

                    # Try to get full stats if authenticated
                    try:
                        stats_response = requests.get(
                            f"http://{ip}:{port}/api/secure/stats",
                            timeout=self.timeout,
                            headers={'X-API-Key': SHARED_API_KEY}
                        )

                        if stats_response.status_code == 200:
                            return {
                                'status': 'tracking_active',
                                'data': stats_response.json(),
                                'identity': identity_data
                            }
                    except:
                        pass

                    # If stats failed but identity worked, return identity info
                    return {
                        'status': 'tracking_active', # It IS active, just maybe not authenticated yet
                        'data': {'device_info': identity_data}, # Fallback data
                        'identity': identity_data
                    }
                else:
                    print(f"[DEBUG] Identity check failed on {ip}: Status {identity_response.status_code}")
                    return {
                        'status': 'port_open_no_service',
                        'data': None
                    }

            # Identity failed (timeout/connection). Do a raw port check to classify.
            if self.check_port_open(ip, port):
                return {
                    'status': 'port_open_no_service',
                    'data': None
                }

            return None
        except Exception as e:
            print(f"[DEBUG] check_tracking_service error on {ip}: {e}")
            return None
    
    def scan_single_ip(self, ip):
        """Scan a single IP"""
        try:
            service_info = self.check_tracking_service(ip)
            if not service_info:
                return None

            # After a successful HTTP/port check, ARP cache is warm—MAC lookup is more reliable.
            mac = self.get_mac_address(ip)

            try:
                hostname = socket.gethostbyaddr(ip)[0]
            except:
                hostname = "Unknown"

            device_info = {
                'ip': ip,
                'port': 5002,
                'status': service_info.get('status', 'unknown'),
                'mac_address': mac,
                'hostname': hostname,
                'system': 'Unknown',
                'tracking_data': service_info.get('data')
            }

            if service_info.get('status') == 'tracking_active' and service_info.get('data'):
                device_data = service_info['data'].get('device_info', {})
                device_info.update({
                    'hostname': device_data.get('hostname', hostname),
                    'system': device_data.get('system', device_data.get('os', 'Unknown')),
                    'mac_address': device_data.get('mac_address', mac)
                })

            return device_info
        except Exception as e:
            return None
    
    def get_local_network_ranges(self):
        """Get local network range"""
        try:
            interfaces = psutil.net_if_addrs()
            for interface_name, addrs in interfaces.items():
                for addr in addrs:
                    if addr.family.name == 'AF_INET':
                        ip = addr.address
                        netmask = addr.netmask
                        # We still return the network, but we won't skip 127.0.0.1 during actual scan list gen
                        if not ip:
                            continue
                        if ip.startswith("127."):
                             continue
                        try:
                            network = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
                            return str(network)
                        except:
                            continue
        except:
            pass
        return "192.168.1.0/24"
    
    def scan_for_trackable_devices(self):
        """Scan network for devices"""
        print("[DEBUG] Tracking Scanner: Starting scan...", flush=True)
        local_network = self.get_local_network_ranges()
        print(f"[DEBUG] Tracking Scanner: Scanning network {local_network}", flush=True)
        
        all_ips = []
        try:
            # Add all network IPs
            all_ips = [str(ip) for ip in ipaddress.IPv4Network(local_network, strict=False)]
        except Exception as e:
            print(f"[DEBUG] Error generating IP list: {e}")

        # ALWAYS ADD LOCALHOST FOR TESTING
        if "127.0.0.1" not in all_ips:
            all_ips.append("127.0.0.1")
        
        # Add typical local IPs if not present
        local_ip = socket.gethostbyname(socket.gethostname())
        if local_ip not in all_ips:
            all_ips.append(local_ip)

        print(f"[DEBUG] Tracking Scanner: Found {len(all_ips)} IPs to scan. (First 5: {all_ips[:5]})", flush=True)
        
        devices_found = []
        # Use simple loop for debugging if needed, but keeping threads for now
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            results = executor.map(self.scan_single_ip, all_ips)
            for result in results:
                if result:
                    print(f"[DEBUG] Found device: {result['ip']} - Status: {result['status']}", flush=True)
                    devices_found.append(result)
        
        print(f"[DEBUG] Scan complete. Found {len(devices_found)} devices.", flush=True)
        return devices_found

# Real-time tracking storage
real_time_data = {}

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def check_device_status(device):
    """Check if device is online/offline based on last_seen"""
    if not device.last_seen:
        return "offline"
    
    time_diff = datetime.utcnow() - device.last_seen
    if time_diff < timedelta(minutes=5):  # Device seen in last 5 minutes
        return "online"
    else:
        return "offline"

def device_to_dict(device):
    """Convert device to a JSON-serializable dictionary"""
    if not device:
        return {}
    
    return {
        'id': device.id,
        'device_name': device.device_name,
        'employee_name': device.employee_name,
        'hostname': device.hostname,
        'ip_address': device.ip_address,
        'mac_address': device.mac_address,
        'department': device.department,
        'notes': device.notes,
        'created_at': device.created_at.isoformat() if device.created_at else None,
        'updated_at': device.updated_at.isoformat() if device.updated_at else None,
        'last_seen': device.last_seen.isoformat() if device.last_seen else None,
        'status': check_device_status(device)
    }

def log_device_data(device_id, tracking_data):
    """Log device activity and resource data"""
    try:
        current_time = datetime.utcnow()
        
        # Log activity
        current_activity = tracking_data.get('current_activity', {})
        activity_log = DeviceActivityLog(
            device_id=device_id,
            timestamp=current_time,
            activity_type='status_update',
            event_count=1,
            details=json.dumps(current_activity)
        )
        db.session.add(activity_log)
        
        # Log resources
        system_metrics = tracking_data.get('system_metrics', {})
        resource_log = DeviceResourceLog(
            device_id=device_id,
            timestamp=current_time,
            cpu_usage=system_metrics.get('cpu_percent'),
            memory_usage=system_metrics.get('memory_percent'),
            disk_usage=system_metrics.get('disk_usage')
        )
        db.session.add(resource_log)
        
        # Log applications
        today_stats = tracking_data.get('today_stats', {})
        applications = today_stats.get('applications_used', [])
        for app in applications[-5:]:  # Log last 5 applications
            app_log = DeviceApplicationLog(
                device_id=device_id,
                timestamp=current_time,
                application_name=app,
                status='active',
                duration=60  # Assume 1 minute per check
            )
            db.session.add(app_log)
        
        db.session.commit()
        
    except Exception as e:
        db.session.rollback()
        print(f"Error logging device data: {e}")

def get_device_statistics(device_id):
    """Get comprehensive statistics for device"""
    try:
        # Get today's date
        today = datetime.utcnow().date()
        
        # Activity statistics
        activity_logs = DeviceActivityLog.query.filter(
            DeviceActivityLog.device_id == device_id,
            db.func.date(DeviceActivityLog.timestamp) == today
        ).all()
        
        # Resource statistics
        resource_logs = DeviceResourceLog.query.filter(
            DeviceResourceLog.device_id == device_id,
            db.func.date(DeviceResourceLog.timestamp) == today
        ).all()
        
        # Application statistics
        app_logs = DeviceApplicationLog.query.filter(
            DeviceApplicationLog.device_id == device_id,
            db.func.date(DeviceApplicationLog.timestamp) == today
        ).all()
        
        stats = {
            'total_activity_time': len(activity_logs) * 60,  # Approximate seconds
            'keyboard_events': sum(log.event_count for log in activity_logs if 'keyboard' in log.activity_type),
            'mouse_events': sum(log.event_count for log in activity_logs if 'mouse' in log.activity_type),
            'unique_applications': len(set(log.application_name for log in app_logs)),
            'avg_cpu_usage': np.mean([log.cpu_usage for log in resource_logs if log.cpu_usage]) if resource_logs else 0,
            'avg_memory_usage': np.mean([log.memory_usage for log in resource_logs if log.memory_usage]) if resource_logs else 0,
        }
        
        return stats
        
    except Exception as e:
        print(f"Error getting device statistics: {e}")
        return {}

# ============================================================
# CONTEXT PROCESSOR
# ============================================================

@tracking_bp.context_processor
def utility_processor():
    """Make helper functions available in templates"""
    return dict(check_device_status=check_device_status)

# ============================================================
# ROUTES
# ============================================================

@tracking_bp.route('/tracking')
def device_tracking():
    """Main device tracking page"""
    if not session.get('logged_in'):
        return redirect(url_for('auth_bp.login'))
    
    saved_devices = TrackedDevice.query.order_by(TrackedDevice.device_name).all()
    
    # Convert devices to JSON-serializable dictionaries
    saved_devices_dicts = [device_to_dict(device) for device in saved_devices]
    
    # Get device statistics for the dashboard
    device_stats = {}
    for device in saved_devices:
        stats = get_device_statistics(device.id)
        device_stats[device.id] = stats
    
    return render_template('tracking/device_tracking.html', 
                         saved_devices=saved_devices,
                         saved_devices_dicts=saved_devices_dicts,  # Pass serializable version
                         device_stats=device_stats)

@tracking_bp.route('/tracking/history/<int:device_id>')
def device_history(device_id):
    """Device history page"""
    if not session.get('logged_in'):
        return redirect(url_for('auth_bp.login'))
    
    device = TrackedDevice.query.get_or_404(device_id)
    
    # Get date range from request
    days = request.args.get('days', 7, type=int)
    start_date = datetime.utcnow() - timedelta(days=days)
    
    # Get historical data
    activity_logs = DeviceActivityLog.query.filter(
        DeviceActivityLog.device_id == device_id,
        DeviceActivityLog.timestamp >= start_date
    ).order_by(DeviceActivityLog.timestamp.desc()).all()
    
    resource_logs = DeviceResourceLog.query.filter(
        DeviceResourceLog.device_id == device_id,
        DeviceResourceLog.timestamp >= start_date
    ).order_by(DeviceResourceLog.timestamp.desc()).limit(1000).all()
    
    application_logs = DeviceApplicationLog.query.filter(
        DeviceApplicationLog.device_id == device_id,
        DeviceApplicationLog.timestamp >= start_date
    ).order_by(DeviceApplicationLog.timestamp.desc()).all()
    
    return render_template('tracking/device_history.html',
                         device=device,
                         activity_logs=activity_logs,
                         resource_logs=resource_logs,
                         application_logs=application_logs,
                         days=days)

# ============================================================
# API ENDPOINTS - REAL TIME TRACKING
# ============================================================

# Global cache for real-time data
real_time_data = {}

@tracking_bp.route('/api/tracking/real-time/<mac_address>')
def api_real_time_tracking(mac_address):
    """Real-time tracking data for device"""
    # Auth handled by middleware

    
    try:
        # Check cache first (CACHE HIT)
        if mac_address in real_time_data:
            cached = real_time_data[mac_address]
            # Valid for 5 seconds (prevents spamming the device)
            if time.time() - cached['timestamp'] < 5:
                # If cached status was offline, returning it avoids a timeout wait
                if cached.get('status') == 'offline':
                     return jsonify({
                        'success': False,
                        'error': 'Device not responding (Cached)',
                        'device_info': cached.get('device_info')
                    }), 503
                
                return jsonify({
                    'success': True,
                    'tracking_data': cached['data'],
                    'device_info': cached.get('device_info'),
                    'timestamp': datetime.fromtimestamp(cached['timestamp']).isoformat(),
                    'cached': True
                })

        device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
        if not device or not device.ip_address:
            # Cache the failure too
            return jsonify({'success': False, 'error': 'Device not found'}), 404
        
        # Get live data from device
        scanner = NetworkScanner()
        # Timeout reduced to 0.5s in class init
        service_info = scanner.check_tracking_service(device.ip_address)
        
        if service_info and service_info['status'] == 'tracking_active' and service_info['data']:
            tracking_data = service_info['data']
            
            # Update device last seen
            device.last_seen = datetime.utcnow()
            
            # FAST WRITE: Update in-memory cache
            real_time_data[mac_address] = {
                'data': tracking_data,
                'status': 'online',
                'device_info': device_to_dict(device),
                'timestamp': time.time()
            }
            
            # Log activity and resources
            # PERFORMANCE FIX: Disable per-second DB writes to prevent lag
            # log_device_data(device.id, tracking_data)
            
            # db.session.commit()
            
            return jsonify({
                'success': True,
                'tracking_data': tracking_data,
                'device_info': device_to_dict(device),  # Use serializable version
                'timestamp': datetime.utcnow().isoformat()
            })
        else:
             # Cache the OFFLINE status for 5 seconds so we don't keep hitting the timeout
            real_time_data[mac_address] = {
                'data': None,
                'status': 'offline',
                'device_info': device_to_dict(device),
                'timestamp': time.time()
            }

            return jsonify({
                'success': False,
                'error': 'Device not responding',
                'device_info': device_to_dict(device)  # Use serializable version
            }), 503
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@tracking_bp.route('/api/tracking/history/activity/<int:device_id>')
def api_activity_history(device_id):
    """Get activity history for device"""
    # Auth handled by middleware

    
    try:
        days = request.args.get('days', 7, type=int)
        start_date = datetime.utcnow() - timedelta(days=days)
        
        logs = DeviceActivityLog.query.filter(
            DeviceActivityLog.device_id == device_id,
            DeviceActivityLog.timestamp >= start_date
        ).order_by(DeviceActivityLog.timestamp.asc()).all()
        
        # Convert logs to serializable format
        log_dicts = []
        for log in logs:
            log_dict = {
                'id': log.id,
                'device_id': log.device_id,
                'timestamp': log.timestamp.isoformat(),
                'activity_type': log.activity_type,
                'event_count': log.event_count,
                'details': log.details
            }
            log_dicts.append(log_dict)
        
        return jsonify({
            'success': True,
            'data': log_dicts
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@tracking_bp.route('/api/tracking/history/resources/<int:device_id>')
def api_resource_history(device_id):
    """Get resource usage history for device"""
    # Auth handled by middleware

    
    try:
        hours = request.args.get('hours', 24, type=int)
        start_date = datetime.utcnow() - timedelta(hours=hours)
        
        logs = DeviceResourceLog.query.filter(
            DeviceResourceLog.device_id == device_id,
            DeviceResourceLog.timestamp >= start_date
        ).order_by(DeviceResourceLog.timestamp.asc()).all()
        
        # Convert logs to serializable format
        log_dicts = []
        for log in logs:
            log_dict = {
                'id': log.id,
                'device_id': log.device_id,
                'timestamp': log.timestamp.isoformat(),
                'cpu_usage': log.cpu_usage,
                'memory_usage': log.memory_usage,
                'disk_usage': log.disk_usage
            }
            log_dicts.append(log_dict)
        
        return jsonify({
            'success': True,
            'data': log_dicts
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@tracking_bp.route('/api/tracking/history/applications/<int:device_id>')
def api_application_history(device_id):
    """Get application usage history for device"""
    # Auth handled by middleware

    
    try:
        days = request.args.get('days', 7, type=int)
        start_date = datetime.utcnow() - timedelta(days=days)
        
        logs = DeviceApplicationLog.query.filter(
            DeviceApplicationLog.device_id == device_id,
            DeviceApplicationLog.timestamp >= start_date
        ).order_by(DeviceApplicationLog.timestamp.desc()).all()
        
        # Convert logs to serializable format
        log_dicts = []
        for log in logs:
            log_dict = {
                'id': log.id,
                'device_id': log.device_id,
                'timestamp': log.timestamp.isoformat(),
                'application_name': log.application_name,
                'status': log.status,
                'duration': log.duration
            }
            log_dicts.append(log_dict)
        
        # Group by application and calculate total usage
        app_usage = {}
        for log in logs:
            if log.application_name not in app_usage:
                app_usage[log.application_name] = {
                    'name': log.application_name,
                    'total_duration': 0,
                    'sessions': 0,
                    'last_used': log.timestamp.isoformat()
                }
            app_usage[log.application_name]['total_duration'] += (log.duration or 0)
            app_usage[log.application_name]['sessions'] += 1
        
        return jsonify({
            'success': True,
            'data': list(app_usage.values()),
            'raw_data': log_dicts[:100]  # Last 100 entries
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500



@tracking_bp.route('/api/tracking/stream/screenshot/<mac_address>')
def api_stream_screenshot(mac_address):
    """Stream real-time screenshots"""
    # Auth handled by middleware

    
    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return Response(
            generate_placeholder_image("Device Not Found"),
            mimetype='multipart/x-mixed-replace; boundary=frame'
        )
    
    def generate():
        consecutive_errors = 0
        max_errors = 5
        
        while consecutive_errors < max_errors:
            try:
                # Make request with stream=True to get chunks
                response = requests.get(
                    f"http://{device.ip_address}:5002/stream",
                    timeout=5,
                    headers={'X-API-Key': SHARED_API_KEY},
                    stream=True
                )
                
                if response.status_code == 200:
                    consecutive_errors = 0  # Reset error counter
                    
                    # Read and forward the multipart stream chunks
                    for chunk in response.iter_content(chunk_size=4096):
                        if chunk:
                            yield chunk
                else:
                    consecutive_errors += 1
                    print(f"Screenshot stream HTTP error {response.status_code} for {device.ip_address}")
                    yield generate_placeholder_image(f"Error {response.status_code}")
                    time.sleep(2)
                    
            except requests.exceptions.Timeout:
                consecutive_errors += 1
                print(f"Screenshot stream timeout for {device.ip_address}")
                yield generate_placeholder_image("Timeout")
                time.sleep(2)
                
            except requests.exceptions.ConnectionError:
                consecutive_errors += 1
                print(f"Screenshot stream connection error for {device.ip_address}")
                yield generate_placeholder_image("Connection Error")
                time.sleep(2)
                
            except Exception as e:
                consecutive_errors += 1
                print(f"Screenshot stream error for {device.ip_address}: {e}")
                yield generate_placeholder_image("Stream Error")
                time.sleep(2)
        
        # Max errors reached, stop streaming
        yield generate_placeholder_image("Stream Stopped")
    
    return Response(
        generate(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


@tracking_bp.route('/api/tracking/stream/camera/<mac_address>')
def api_stream_camera(mac_address):
    """Stream real-time camera feed"""
    # Auth handled by middleware

    
    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return Response(
            generate_placeholder_image("Device Not Found"),
            mimetype='multipart/x-mixed-replace; boundary=frame'
        )
    
    def generate():
        consecutive_errors = 0
        max_errors = 5
        
        while consecutive_errors < max_errors:
            try:
                # Use start_camera endpoint which returns a stream
                # Use context manager to ensure connection is closed
                with requests.get(
                    f"http://{device.ip_address}:5002/start_camera",
                    timeout=5,
                    headers={'X-API-Key': SHARED_API_KEY},
                    stream=True
                ) as response:
                
                    if response.status_code == 200:
                        consecutive_errors = 0  # Reset error counter
                        
                        # Forward camera stream chunks
                        for chunk in response.iter_content(chunk_size=4096):
                            if chunk:
                                yield chunk
                    else:
                        consecutive_errors += 1
                        print(f"Camera stream HTTP error {response.status_code} for {device.ip_address}")
                        yield generate_placeholder_image(f"Camera Error {response.status_code}")
                        time.sleep(2)
                    
            except requests.exceptions.Timeout:
                consecutive_errors += 1
                print(f"Camera stream timeout for {device.ip_address}")
                yield generate_placeholder_image("Camera Timeout")
                time.sleep(2)
                
            except requests.exceptions.ConnectionError:
                consecutive_errors += 1
                print(f"Camera stream connection error for {device.ip_address}")
                yield generate_placeholder_image("Camera Offline")
                time.sleep(2)
                
            except Exception as e:
                consecutive_errors += 1
                print(f"Camera stream error for {device.ip_address}: {e}")
                yield generate_placeholder_image("Camera Error")
                time.sleep(2)
                
        # Max errors reached, stop streaming
        yield generate_placeholder_image("Camera Stopped")
    
    return Response(
        generate(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


@tracking_bp.route('/api/tracking/stop-camera/<mac_address>', methods=['POST'])
def api_stop_camera(mac_address):
    """Stop camera stream on device"""
    if not session.get('logged_in'):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return jsonify({"success": False, "error": "Device not found"}), 404
    
    try:
        response = requests.get(
            f"http://{device.ip_address}:5002/stop_camera",
            timeout=3,
            headers={'X-API-Key': SHARED_API_KEY}
        )
        
        if response.status_code == 200:
            return jsonify({"success": True, "message": "Camera stopped"})
        else:
            return jsonify({"success": False, "error": "Failed to stop camera"}), 500
            
    except Exception as e:
        print(f"Error stopping camera: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@tracking_bp.route('/api/tracking/toggle-camera/<mac_address>', methods=['POST'])
def api_toggle_camera(mac_address):
    """Toggle camera on/off"""
    if not session.get('logged_in'):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return jsonify({"success": False, "error": "Device not found"}), 404
    
    try:
        # First check current camera status
        status_response = requests.get(
            f"http://{device.ip_address}:5002/camera_status",
            timeout=2,
            headers={'X-API-Key': SHARED_API_KEY}
        )
        
        if status_response.status_code == 200:
            status_data = status_response.json()
            is_active = status_data.get('active', False)
            
            if is_active:
                # Camera is active, stop it
                stop_response = requests.get(
                    f"http://{device.ip_address}:5002/stop_camera",
                    timeout=2,
                    headers={'X-API-Key': SHARED_API_KEY}
                )
                return jsonify({
                    "success": True,
                    "message": "Camera stopped",
                    "action": "stopped"
                })
            else:
                # Camera is inactive, start it (frontend will handle stream display)
                return jsonify({
                    "success": True,
                    "message": "Camera ready to start",
                    "action": "started"
                })
        else:
            # Status check failed, try to stop anyway
            requests.get(
                f"http://{device.ip_address}:5002/stop_camera",
                timeout=2,
                headers={'X-API-Key': SHARED_API_KEY}
            )
            return jsonify({
                "success": True,
                "message": "Camera toggled",
                "action": "stopped"
            })
        
    except Exception as e:
        print(f"Error toggling camera: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ============================================================
# DEVICE MANAGEMENT ENDPOINTS
# ============================================================

@tracking_bp.route('/api/tracking/scan', methods=['POST'])
def api_scan_devices():
    """Scan network for devices"""
    print("[DEBUG] /api/tracking/scan endpoint called!")
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        scanner = NetworkScanner()
        devices_found = scanner.scan_for_trackable_devices()
        
        saved_devices = TrackedDevice.query.all()
        saved_macs = {device.mac_address: device for device in saved_devices}
        
        enhanced_devices = []
        updated_ips = []
        
        for device in devices_found:
            mac = device.get('mac_address', 'Unknown').upper()
            
            # AUTO-SAVE LOGIC: If tracking is active and device not saved, save it automatically
            if mac not in saved_macs and mac != 'N/A' and device['status'] == 'tracking_active':
                try:
                    new_device = TrackedDevice(
                        mac_address=mac,
                        device_name=device.get('hostname', f"Device_{mac[-4:]}"),
                        employee_name="Auto-Discovered",
                        hostname=device.get('hostname'),
                        ip_address=device['ip'],
                        department="Unassigned",
                        notes="Auto-discovered by scanner"
                    )
                    db.session.add(new_device)
                    # Update local cache so we don't duplicate if list has dupes
                    saved_macs[mac] = new_device 
                    print(f"[AUTO-SAVE] Added new device: {mac} ({device['ip']})")
                except Exception as e:
                    print(f"[AUTO-SAVE] Error saving {mac}: {e}")

            if mac in saved_macs and mac != 'N/A':
                saved_device = saved_macs[mac]
                # Auto-update IP if changed
                if device['ip'] != saved_device.ip_address:
                    saved_device.ip_address = device['ip']
                    saved_device.last_seen = datetime.utcnow()
                    updated_ips.append({
                        'device_name': saved_device.device_name,
                        'old_ip': saved_device.ip_address,
                        'new_ip': device['ip']
                    })
            
            device_dict = {
                'ip': device['ip'],
                'port': device['port'],
                'status': device['status'],
                'mac_address': mac,
                'hostname': device.get('hostname', 'Unknown'),
                'system': device.get('system', 'Unknown'),
                'tracking_data': device.get('tracking_data'),
                'is_saved': mac in saved_macs and mac != 'N/A'
            }
            
            if device_dict['is_saved'] and mac in saved_macs:
                device_dict['saved_info'] = device_to_dict(saved_macs[mac])  # Use serializable version
            
            enhanced_devices.append(device_dict)
        
        # Commit all changes (new devices + IP updates)
        db.session.commit()
        
        tracking_active = [d for d in enhanced_devices if d['status'] == 'tracking_active']
        port_only = [d for d in enhanced_devices if d['status'] == 'port_open_no_service']
        
        return jsonify({
            'success': True,
            'devices_found': enhanced_devices,
            'total_found': len(enhanced_devices),
            'tracking_active': len(tracking_active),
            'port_only': len(port_only),
            'new_devices': len([d for d in enhanced_devices if not d['is_saved']]),
            'updated_ips': updated_ips
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@tracking_bp.route('/api/tracking/save-device', methods=['POST'])
def api_save_device():
    """Save/update device"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        data = request.json
        mac_address = data['mac_address'].upper()
        
        if mac_address == 'N/A' or mac_address == 'UNKNOWN':
            return jsonify({'success': False, 'error': 'Cannot save device with unknown MAC address'})
        
        device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
        
        if device:
            device.device_name = data['device_name']
            device.employee_name = data.get('employee_name')
            device.hostname = data.get('hostname')
            device.ip_address = data.get('ip_address')
            device.department = data.get('department')
            device.notes = data.get('notes')
            device.updated_at = datetime.utcnow()
        else:
            device = TrackedDevice(
                mac_address=mac_address,
                device_name=data['device_name'],
                employee_name=data.get('employee_name'),
                hostname=data.get('hostname'),
                ip_address=data.get('ip_address'),
                department=data.get('department'),
                notes=data.get('notes')
            )
            db.session.add(device)
        
        db.session.commit()
        return jsonify({'success': True, 'message': 'Device saved successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})

@tracking_bp.route('/api/tracking/delete-device', methods=['POST'])
def api_delete_device():
    """Delete device"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        mac_address = request.json.get('mac_address')
        device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
        
        if not device:
            return jsonify({'success': False, 'error': 'Device not found'})
        
        db.session.delete(device)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Device deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})

@tracking_bp.route('/api/tracking/sync-ips', methods=['POST'])
def api_sync_ips():
    """Sync IP addresses for all devices"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        scanner = NetworkScanner()
        devices_found = scanner.scan_for_trackable_devices()
        
        saved_devices = TrackedDevice.query.all()
        saved_macs = {device.mac_address: device for device in saved_devices}
        
        updated_devices = []
        
        for device in devices_found:
            mac = device.get('mac_address', 'Unknown').upper()
            
            if mac in saved_macs and mac != 'N/A':
                saved_device = saved_macs[mac]
                if device['ip'] != saved_device.ip_address:
                    old_ip = saved_device.ip_address
                    saved_device.ip_address = device['ip']
                    saved_device.last_seen = datetime.utcnow()
                    updated_devices.append({
                        'device_name': saved_device.device_name,
                        'old_ip': old_ip,
                        'new_ip': device['ip']
                    })
        
        if updated_devices:
            db.session.commit()
        
        return jsonify({
            'success': True,
            'updated_devices': updated_devices,
            'message': f'Updated {len(updated_devices)} device(s)'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================
# LIVE TRACKING ROUTES
# ============================================================

@tracking_bp.route('/tracking/live')
def live_tracking():
    """Live tracking page (separate from main tracking)"""
    if not session.get('logged_in'):
        return redirect(url_for('auth_bp.login'))
    
    saved_devices = TrackedDevice.query.order_by(TrackedDevice.device_name).all()
    
    # Convert devices to serializable dictionaries
    saved_devices_dicts = [device_to_dict(device) for device in saved_devices]
    
    return render_template('tracking/live_tracking.html', 
                         saved_devices=saved_devices,
                         saved_devices_dicts=saved_devices_dicts)

@tracking_bp.route('/api/tracking/live-summary')
def api_live_summary():
    """Get live summary data for all devices"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        devices = TrackedDevice.query.all()
        summary_data = []
        
        for device in devices:
            if device.ip_address:
                scanner = NetworkScanner()
                service_info = scanner.check_tracking_service(device.ip_address)
                
                if service_info and service_info['status'] == 'tracking_active':
                    device_data = {
                        'id': device.id,
                        'device_name': device.device_name,
                        'employee_name': device.employee_name,
                        'status': 'online',
                        'tracking_data': service_info['data']
                    }
                else:
                    device_data = {
                        'id': device.id,
                        'device_name': device.device_name,
                        'employee_name': device.employee_name,
                        'status': 'offline'
                    }
                
                summary_data.append(device_data)
        
        return jsonify({
            'success': True,
            'total_devices': len(devices),
            'online_devices': len([d for d in summary_data if d['status'] == 'online']),
            'devices': summary_data
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@tracking_bp.route('/api/tracking/live-status/<mac_address>')
def api_live_status(mac_address):
    """Get simplified live status for a device"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
        if not device or not device.ip_address:
            return jsonify({'success': False, 'error': 'Device not found'}), 404
        
        scanner = NetworkScanner()
        service_info = scanner.check_tracking_service(device.ip_address)
        
        if service_info and service_info['status'] == 'tracking_active':
            tracking_data = service_info['data']
            
            return jsonify({
                'success': True,
                'status': 'online',
                'device_name': device.device_name,
                'activity': tracking_data.get('current_activity', {}),
                'resources': tracking_data.get('system_metrics', {}),
                'timestamp': datetime.utcnow().isoformat()
            })
        else:
            return jsonify({
                'success': True,
                'status': 'offline',
                'device_name': device.device_name,
                'timestamp': datetime.utcnow().isoformat()
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================
# ALERT FUNCTIONS
# ============================================================

def check_live_alerts(tracking_data, device_info):
    """Check for live tracking alerts"""
    alerts = []
    
    # Check for high resource usage
    system_metrics = tracking_data.get('system_metrics', {})
    if system_metrics.get('cpu_percent', 0) > 90:
        alerts.append({
            'type': 'high_cpu',
            'message': f'High CPU usage: {system_metrics["cpu_percent"]}%',
            'severity': 'warning'
        })
    
    if system_metrics.get('memory_percent', 0) > 90:
        alerts.append({
            'type': 'high_memory',
            'message': f'High memory usage: {system_metrics["memory_percent"]}%',
            'severity': 'warning'
        })
    
    # Check for prolonged inactivity
    current_activity = tracking_data.get('current_activity', {})
    if current_activity.get('idle_seconds', 0) > 1800:  # 30 minutes
        alerts.append({
            'type': 'inactive',
            'message': f'Device inactive for {current_activity["idle_seconds"] // 60} minutes',
            'severity': 'info'
        })
    
    return alerts

@tracking_bp.route('/api/tracking/live-alerts')
def api_live_alerts():
    """Get live alerts for all devices"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        devices = TrackedDevice.query.all()
        all_alerts = []
        
        for device in devices:
            if device.ip_address:
                scanner = NetworkScanner()
                service_info = scanner.check_tracking_service(device.ip_address)
                
                if service_info and service_info['status'] == 'tracking_active':
                    alerts = check_live_alerts(
                        service_info['data'], 
                        device_to_dict(device)  # Use serializable version
                    )
                    
                    for alert in alerts:
                        alert['device_name'] = device.device_name
                        alert['device_id'] = device.id
                        all_alerts.append(alert)
        
        return jsonify({
            'success': True,
            'alerts': all_alerts,
            'total_alerts': len(all_alerts)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
        
