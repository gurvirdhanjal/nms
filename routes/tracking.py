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

def _wav_header(sample_rate=16000, bits_per_sample=16, channels=1, data_size=0x7FFFFFFF):
    """Create a WAV header for streaming PCM audio."""
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    riff_size = data_size + 36
    return (
        b'RIFF' +
        riff_size.to_bytes(4, 'little') +
        b'WAVE' +
        b'fmt ' +
        (16).to_bytes(4, 'little') +
        (1).to_bytes(2, 'little') +
        channels.to_bytes(2, 'little') +
        sample_rate.to_bytes(4, 'little') +
        byte_rate.to_bytes(4, 'little') +
        block_align.to_bytes(2, 'little') +
        bits_per_sample.to_bytes(2, 'little') +
        b'data' +
        data_size.to_bytes(4, 'little')
    )


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
            identity_data = None
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
                    # Identity endpoint missing or failed. Try stats for legacy agents.
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
                                'identity': None
                            }
                    except:
                        pass

                    # Fallback to a lightweight health check
                    try:
                        health_response = requests.get(
                            f"http://{ip}:{port}/api/health",
                            timeout=self.timeout
                        )
                        if health_response.status_code == 200:
                            return {
                                'status': 'tracking_active',
                                'data': {'device_info': identity_data} if identity_data else {},
                                'identity': identity_data,
                                'health_only': True
                            }
                    except:
                        pass

                    print(f"[DEBUG] Identity check failed on {ip}: Status {identity_response.status_code}")
                    return {
                        'status': 'port_open_no_service',
                        'data': None
                    }

            # Identity failed (timeout/connection). Try stats for legacy/slow agents.
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
                        'identity': None
                    }
            except:
                pass

            # Fallback to health endpoint if stats/identity fail
            try:
                health_response = requests.get(
                    f"http://{ip}:{port}/api/health",
                    timeout=self.timeout
                )
                if health_response.status_code == 200:
                    return {
                        'status': 'tracking_active',
                        'data': {'device_info': identity_data} if identity_data else {},
                        'identity': identity_data,
                        'health_only': True
                    }
            except:
                pass

            # Identity/stats failed (timeout/connection). Do a raw port check to classify.
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
                'unique_client_id': None,
                'hostname': hostname,
                'system': 'Unknown',
                'tracking_data': service_info.get('data')
            }

            if service_info.get('status') == 'tracking_active' and service_info.get('data'):
                device_data = service_info['data'].get('device_info', {})
                # Extract identity info from the agent response
                agent_mac = device_data.get('mac_address')
                agent_client_id = device_data.get('unique_client_id')
                
                device_info.update({
                    'hostname': device_data.get('hostname', hostname),
                    'system': device_data.get('system', device_data.get('os', 'Unknown')),
                    'mac_address': agent_mac if agent_mac else mac,
                    'unique_client_id': agent_client_id
                })
            
            # --- AUTO-UPDATE IP LOGIC ---
            # If we found a valid device identity, update the DB immediately to fix connectivity
            target_mac = device_info.get('mac_address')
            target_client_id = device_info.get('unique_client_id')
            
            if target_mac and target_mac != "N/A":
                # 1. Try finding by Unique Client ID first (Most robust)
                device = None
                if target_client_id:
                    device = TrackedDevice.query.filter_by(unique_client_id=target_client_id).first()
                
                # 2. Fallback to MAC address
                if not device:
                    device = TrackedDevice.query.filter_by(mac_address=target_mac).first()
                    # If found by MAC but missing client ID, save it for future
                    if device and target_client_id and not device.unique_client_id:
                        device.unique_client_id = target_client_id
                        db.session.commit()
                        print(f"[Auto-Repair] Linked Client ID {target_client_id} to device {device.device_name}")

                # 3. Update IP if changed
                if device:
                    if device.ip_address != ip:
                        print(f"[Auto-Repair] IP Change Detect: {device.device_name} moved from {device.ip_address} to {ip}")
                        device.ip_address = ip
                        device.last_seen = datetime.utcnow()
                        db.session.commit()

            return device_info
        except Exception as e:
            print(f"Error scanning IP {ip}: {e}")
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
metrics_refresh_state = {'last_run': 0}

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
        'unique_client_id': device.unique_client_id,
        'department': device.department,
        'notes': device.notes,
        'maintenance_mode': device.maintenance_mode,
        'created_at': device.created_at.isoformat() if device.created_at else None,
        'updated_at': device.updated_at.isoformat() if device.updated_at else None,
        'last_seen': device.last_seen.isoformat() if device.last_seen else None,
        'status': check_device_status(device)
    }

PRODUCTIVE_KEYWORDS = [
    'code', 'studio', 'pycharm', 'intellij', 'eclipse', 'vim', 'emacs',
    'excel', 'word', 'powerpoint', 'slack', 'jira', 'teams', 'outlook',
    'terminal', 'powershell', 'cmd', 'notepad', 'confluence'
]
DISTRACTING_KEYWORDS = [
    'youtube', 'facebook', 'instagram', 'tiktok', 'steam', 'game', 'netflix',
    'spotify', 'twitch', 'reddit'
]

def classify_app(app_name):
    """Classify applications into productive, distracting, or neutral."""
    name = (app_name or '').lower()
    if any(keyword in name for keyword in PRODUCTIVE_KEYWORDS):
        return 'productive'
    if any(keyword in name for keyword in DISTRACTING_KEYWORDS):
        return 'distracting'
    return 'neutral'

def calculate_focus_score(app_logs):
    """Calculate focus score and time breakdown from app logs."""
    productive_time = 0
    distracting_time = 0
    neutral_time = 0

    for log in app_logs:
        duration = log.duration or 60
        category = classify_app(log.application_name)
        if category == 'productive':
            productive_time += duration
        elif category == 'distracting':
            distracting_time += duration
        else:
            neutral_time += duration

    total_time = productive_time + distracting_time + neutral_time
    focus_score = int((productive_time / total_time) * 100) if total_time > 0 else 0

    return focus_score, productive_time, distracting_time, neutral_time, total_time

def calculate_longest_idle_seconds(activity_logs):
    """Find the longest idle duration recorded in activity logs."""
    longest_idle = 0
    for log in activity_logs:
        try:
            details = json.loads(log.details) if log.details else {}
        except Exception:
            details = {}
        idle_seconds = details.get('idle_seconds', 0) or 0
        if idle_seconds > longest_idle:
            longest_idle = idle_seconds
    return longest_idle

def build_work_sessions(activity_logs, idle_threshold=300, gap_threshold=300):
    """Build work session blocks based on activity logs and idle time."""
    sessions = []
    logs_by_device = {}

    for log in activity_logs:
        logs_by_device.setdefault(log.device_id, []).append(log)

    if not logs_by_device:
        return sessions

    device_ids = list(logs_by_device.keys())
    devices = TrackedDevice.query.filter(TrackedDevice.id.in_(device_ids)).all()
    device_lookup = {device.id: device.device_name for device in devices}

    for device_id, logs in logs_by_device.items():
        logs.sort(key=lambda entry: entry.timestamp)
        session_start = None
        last_timestamp = None

        for log in logs:
            try:
                details = json.loads(log.details) if log.details else {}
            except Exception:
                details = {}
            idle_seconds = details.get('idle_seconds', 0) or 0
            is_active = idle_seconds <= idle_threshold

            if not is_active:
                if session_start:
                    end_time = last_timestamp or log.timestamp
                    duration = (end_time - session_start).total_seconds()
                    sessions.append({
                        'device_id': device_id,
                        'device_name': device_lookup.get(device_id, 'Unknown'),
                        'start': session_start.isoformat(),
                        'end': end_time.isoformat(),
                        'duration_seconds': int(duration)
                    })
                    session_start = None
                last_timestamp = log.timestamp
                continue

            if session_start is None:
                session_start = log.timestamp
            elif last_timestamp and (log.timestamp - last_timestamp).total_seconds() > gap_threshold:
                end_time = last_timestamp
                duration = (end_time - session_start).total_seconds()
                sessions.append({
                    'device_id': device_id,
                    'device_name': device_lookup.get(device_id, 'Unknown'),
                    'start': session_start.isoformat(),
                    'end': end_time.isoformat(),
                    'duration_seconds': int(duration)
                })
                session_start = log.timestamp

            last_timestamp = log.timestamp

        if session_start:
            end_time = last_timestamp or session_start
            duration = (end_time - session_start).total_seconds()
            sessions.append({
                'device_id': device_id,
                'device_name': device_lookup.get(device_id, 'Unknown'),
                'start': session_start.isoformat(),
                'end': end_time.isoformat(),
                'duration_seconds': int(duration)
            })

    sessions.sort(key=lambda entry: entry['duration_seconds'], reverse=True)
    return sessions[:20]

def _calc_interval_seconds(log, last_ts_by_device, default_interval=60, max_interval=300):
    """Estimate sample interval per device for converting KB/s to KB."""
    last_ts = last_ts_by_device.get(log.device_id)
    if last_ts:
        delta = (log.timestamp - last_ts).total_seconds()
        if delta <= 0:
            delta = default_interval
    else:
        delta = default_interval
    if delta > max_interval:
        delta = max_interval
    last_ts_by_device[log.device_id] = log.timestamp
    return delta

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
        network_metrics = tracking_data.get('network')
        if not network_metrics:
            network_metrics = system_metrics.get('network_speed') or {}
        
        resource_log = DeviceResourceLog(
            device_id=device_id,
            timestamp=current_time,
            cpu_usage=system_metrics.get('cpu_percent'),
            memory_usage=system_metrics.get('memory_percent'),
            disk_usage=system_metrics.get('disk_usage'),
            upload_kbps=network_metrics.get('upload_speed_kbps', 0.0),
            download_kbps=network_metrics.get('download_speed_kbps', 0.0)
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

def refresh_tracking_snapshot(force=False, min_interval_seconds=15, force_log=False):
    """Refresh device snapshots from agents to keep metrics accurate."""
    if not force:
        return 0

    now = time.time()
    last_run = metrics_refresh_state.get('last_run', 0)
    if now - last_run < min_interval_seconds:
        return 0

    metrics_refresh_state['last_run'] = now
    refreshed = 0

    try:
        devices = TrackedDevice.query.all()
        if not devices:
            return 0

        scanner = NetworkScanner()
        scanner.timeout = 1.2

        for device in devices:
            if not device.ip_address:
                continue

            service_info = scanner.check_tracking_service(device.ip_address)
            if not service_info or service_info.get('status') != 'tracking_active':
                continue

            tracking_data = service_info.get('data') or {}
            has_metrics = (
                tracking_data.get('system_metrics') or
                tracking_data.get('today_stats') or
                tracking_data.get('current_activity')
            )

            if not has_metrics:
                continue

            # Update last seen and cache
            device.last_seen = datetime.utcnow()
            cache_entry = real_time_data.get(device.mac_address, {})
            last_log_time = cache_entry.get('last_log_time', 0)
            real_time_data[device.mac_address] = {
                'data': tracking_data,
                'status': 'online',
                'device_info': device_to_dict(device),
                'timestamp': time.time(),
                'last_log_time': last_log_time
            }

            # Throttled DB logging (force_log allows on-demand freshness)
            if force_log or time.time() - last_log_time > 60:
                log_device_data(device.id, tracking_data)
                real_time_data[device.mac_address]['last_log_time'] = time.time()

            refreshed += 1

        if refreshed:
            db.session.commit()

    except Exception as exc:
        db.session.rollback()
        print(f"[Metrics Refresh] Warning: {exc}")

    return refreshed

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
    
    # 1. Fetch all devices (Fast)
    saved_devices = TrackedDevice.query.order_by(TrackedDevice.device_name).all()
    
    # 2. Calculate Online/Offline counts in-memory (0 extra DB queries)
    # Define "Online" as seen in the last 5 minutes
    online_threshold = datetime.utcnow() - timedelta(minutes=5)
    
    online_count = 0
    for device in saved_devices:
        if device.last_seen and device.last_seen > online_threshold:
            online_count += 1
            
    offline_count = len(saved_devices) - online_count
    
    # 3. Calculate 24h Activity using a single efficient aggregate query
    yesterday = datetime.utcnow() - timedelta(hours=24)
    last_24h_activity = db.session.query(db.func.count(db.distinct(DeviceActivityLog.device_id)))\
        .filter(DeviceActivityLog.timestamp >= yesterday).scalar() or 0
    
    # 4. Remove the expensive per-device get_device_statistics loop
    # The template does NOT use 'device_stats' or 'saved_devices_dicts'
    
    return render_template('tracking/device_tracking.html', 
                         saved_devices=saved_devices,
                         online_count=online_count,
                         offline_count=offline_count,
                         active_count=online_count, # Use online count for active card
                         last_24h_activity=last_24h_activity)

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
        force_refresh = request.args.get('force') == '1'
        # Check cache first (CACHE HIT)
        if not force_refresh and mac_address in real_time_data:
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
        # Slightly higher timeout for reliability on real-time stats
        scanner.timeout = 1.5
        service_info = scanner.check_tracking_service(device.ip_address)
        
        if service_info and service_info['status'] == 'tracking_active' and service_info['data']:
            tracking_data = service_info['data']
            
            # Update device last seen
            device.last_seen = datetime.utcnow()
            
            # FAST WRITE: Update in-memory cache (preserve throttling metadata)
            cached_entry = real_time_data.get(mac_address, {})
            last_log_time = cached_entry.get('last_log_time', 0)
            real_time_data[mac_address] = {
                'data': tracking_data,
                'status': 'online',
                'device_info': device_to_dict(device),
                'timestamp': time.time(),
                'last_log_time': last_log_time
            }
            
            # Log activity and resources (only if we have metrics)
            has_metrics = (
                tracking_data.get('system_metrics') or
                tracking_data.get('today_stats') or
                tracking_data.get('current_activity')
            )
            # PERFORMANCE FIX: Throttled DB writes (once per 60s) to prevent lag
            if has_metrics and time.time() - last_log_time > 60:
                log_device_data(device.id, tracking_data)
                # Update the last_log_time in the cache
                real_time_data[mac_address]['last_log_time'] = time.time()
                # db.session.commit() # log_device_data already commits
            
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


@tracking_bp.route('/api/tracking/stream/audio/<mac_address>')
def api_stream_audio(mac_address):
    """Stream real-time audio"""
    # Auth handled by middleware

    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
         return jsonify({'error': 'Device not found'}), 404
    
    def generate():
        try:
            # Connect to the device's audio stream
            # stream=True is crucial here
            with requests.get(
                f"http://{device.ip_address}:5002/audio_stream.wav",
                timeout=5,
                headers={'X-API-Key': SHARED_API_KEY},
                stream=True
            ) as response:
                
                if response.status_code == 200:
                    # Send WAV header once so browsers can play raw PCM stream
                    yield _wav_header()
                    # Forward chunks
                    # Use a smaller chunk size for audio to reduce latency
                    for chunk in response.iter_content(chunk_size=1024):
                        if chunk:
                            yield chunk
                else:
                    return
        except Exception as e:
            print(f"Audio stream error for {device.ip_address}: {e}")
            return

    # Return audio stream (WAV container for browser compatibility)
    return Response(generate(), mimetype='audio/wav')


@tracking_bp.route('/api/tracking/toggle-mic/<mac_address>', methods=['POST'])
def api_toggle_mic(mac_address):
    """Toggle microphone state"""
    # Auth handled by middleware

    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return jsonify({'success': False, 'error': 'Device not found'}), 404
    
    try:
        # We need to know current state to toggle. 
        # But for "toggle", we can just call an endpoint on the device that handles logic,
        # OR we can check status first. 
        # Device doesn't have a "toggle_mic" endpoint yet, only start/stop/status.
        # Let's check status first.
        
        status_resp = requests.get(
            f"http://{device.ip_address}:5002/mic_status",
            timeout=2,
            headers={'X-API-Key': SHARED_API_KEY}
        )
        
        if status_resp.status_code != 200:
             return jsonify({'success': False, 'error': 'Failed to check mic status'}), 502
             
        is_active = status_resp.json().get('active', False)
        
        if is_active:
            # Stop it
            action_resp = requests.get(
                f"http://{device.ip_address}:5002/stop_mic",
                timeout=2,
                headers={'X-API-Key': SHARED_API_KEY}
            )
            action = "stopped"
        else:
            # Start it - actually we don't have a distinct "start" endpoint 
            # because accessing the stream auto-starts it in the current implementation?
            # Wait, looking at service.py... 
            # "/audio_stream.wav" calls "start_microphone()" internally if needed.
            # But the user might want to explicitly "Enable" it so it's ready?
            # Actually, `service.py` logic:
            # `stream_audio` -> `mic_manager.start_microphone()`
            # So effectively, hitting the stream endpoint starts it.
            # But we might want a visible "On/Off" state in UI.
            # If we want to "Start" it without streaming yet, we might need a start endpoint.
            # However, for now, let's assume "Active" means "Streaming or Ready".
            # The current `stop_mic` stops the thread.
            # The `audio_stream.wav` starts it.
            
            # If the user clicks "Start Mic", they usually immediately want to listen.
            # So the UI will likely just connect to the stream.
            # BUT, if we want to toggle it OFF, we definitively need `stop_mic`.
            
            # If we want to support a button that just says "Mic On" (even if nobody listening?)
            # The current `service.py` doesn't have a standalone `/start_mic`. 
            # It only starts on stream access.
            # So "Toggle On" effectively does nothing until stream connects?
            # Implementation detail: When client connects to /stream/audio/..., it calls device /audio_stream.wav, 
            # which calls `start_microphone`. 
            
            # ISSUE: If I click "Stop Mic", it calls `/stop_mic`. 
            # If I then click "Start Mic", what do I call? 
            # If I just connect the stream, it starts.
            # So maybe this route is only needed for STOPPING?
            # Or maybe we want a dedicated START endpoint in service.py?
            
            # Let's stick to: This route handles STOP. 
            # For START, the frontend just connects to stream.
            # But wait, the user asked for "Toggle".
            # If I return "started", the UI might show "Recording".
            # Let's allow explicit stop. 
            
            # For consistency with Camera (which has start/stop/toggle):
            # Camera has `toggle_camera_route` in service.py. 
            # Mic does NOT.
            # I should probably just implement the STOP logic here, 
            # and let the frontend stream connection handle START.
            
            # However, if the user wants to "Enable" the mic remotely for *other* reasons (recording to disk?), 
            # we might want a start. 
            # For this task, "Start Mic stream" is the goal.
            
            pass 

        # Let's implement full toggle logic here if possible, 
        # but since we lack a standalone start, we'll just handle STOP 
        # and maybe "dummy" start if needed (or assume stream start).
        
        if is_active:
             # Stop
             requests.get(f"http://{device.ip_address}:5002/stop_mic", headers={'X-API-Key': SHARED_API_KEY}, timeout=2)
             time.sleep(0.5)
             return jsonify({'success': True, 'action': 'stopped'})
        else:
             # We can't explicitly "start" without a stream client in the current service.py design 
             # UNLESS we modify service.py to adds `start_mic`.
             # BUT, the stream endpoint `stream_audio` DOES call `start_microphone`.
             # So if the UI connects to stream, it starts.
             # If we want a button "Toggle Mic", 
             # Case 1: Active -> Click -> Stop.
             # Case 2: Inactive -> Click -> Start? -> Needs to connect stream.
             
             # So actually, "Toggle Mic" button in UI translates to:
             # If off: Create <audio> element and load source.
             # If on: Destroy <audio> element AND call /stop_mic? 
             # Yes.
             
             # So this endpoint might mainly be used for "Force Stop" or status check.
             # Let's keep it compatible.
             
             # If we return "started", UI expects it to be active.
             # But if no stream connects, it might auto-stop or just sit there?
             # `MicrophoneManager` logic: `get_audio_stream` yields chunks. `start_microphone` starts thread.
             # If thread interacts with PyAudio, it stays running until `stop_microphone`.
             # So yes, we CAN start it. But we need a `start_mic` endpoint in service.py if we want it independent of stream.
             # I didn't add `start_mic` in service.py, only `stream_audio`.
             
             # CHANGE OF PLAN: 
             # I will only use this route to "Stop". 
             # The Frontend will "Start" by simply playing the audio.
             # The "Toggle" button in UI effectively means "Connect/Disconnect".
             # But to be clean, "Disconnect" should also call "Stop" on backend to free resources.
             
             return jsonify({'success': True, 'action': 'ready'}) # "Ready" implies "Go ahead and stream"

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@tracking_bp.route('/api/tracking/stream/camera/<mac_address>')
def proxy_camera_stream(mac_address):
    """Proxy camera stream from device"""
    # Check auth (can be disabled for debugging if needed but better safe)
    if not session.get('logged_in'):
        return Response('Unauthorized', 401)
        
    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return Response("Device not found or offline", 404)
        
    def generate():
        try:
            # Connect to the service's /start_camera stream
            # Note: stream=True is crucial for MJPEG
            resp = requests.get(
                f"http://{device.ip_address}:5002/start_camera",
                stream=True,
                timeout=5, # Connection timeout
                headers={'X-API-Key': SHARED_API_KEY}
            )
            
            if resp.status_code != 200:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + 
                       generate_placeholder_image(f"Error: {resp.status_code}") + 
                       b'\r\n')
                return

            # Stream the content
            for chunk in resp.iter_content(chunk_size=4096):
                yield chunk
                
        except Exception as e:
            print(f"Camera proxy error for {mac_address}: {e}")
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + 
                   generate_placeholder_image("Connection Lost") + 
                   b'\r\n')

    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')


@tracking_bp.route('/api/tracking/toggle-camera/<mac_address>', methods=['POST'])
def api_toggle_camera(mac_address):
    """Toggle camera state"""
    # Auth handled by middleware

    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return jsonify({'success': False, 'error': 'Device not found'}), 404
    
    try:
        response = requests.post(
            f"http://{device.ip_address}:5002/toggle_camera",
            timeout=5,
            headers={'X-API-Key': SHARED_API_KEY}
        )
        
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'success': False, 'error': f'Device returned {response.status_code}'}), 502
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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
    if device_info and device_info.get('maintenance_mode'):
        return []

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
            'count': len(all_alerts)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@tracking_bp.route('/api/tracking/maintenance/<mac_address>', methods=['POST'])
def api_toggle_device_maintenance(mac_address):
    """Toggle maintenance mode for a tracked device."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    data = request.get_json(silent=True) or {}
    if 'enabled' not in data:
        return jsonify({'success': False, 'error': 'Missing enabled flag'}), 400

    enabled = data.get('enabled')
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ('true', '1', 'yes', 'on')
    else:
        enabled = bool(enabled)

    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device:
        return jsonify({'success': False, 'error': 'Device not found'}), 404

    device.maintenance_mode = enabled
    device.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        'success': True,
        'mac_address': device.mac_address,
        'maintenance_mode': device.maintenance_mode
    })

# ============================================================
# PRODUCTIVITY & INTELLIGENCE METRICS
# ============================================================

@tracking_bp.route('/api/tracking/metrics/productivity')
def api_productivity_metrics():
    """Get productivity metrics and work session blocks."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
    try:
        refresh_requested = request.args.get('refresh') == '1'
        refresh_tracking_snapshot(force=refresh_requested, force_log=refresh_requested)

        today = datetime.utcnow().date()

        # Fetch all app logs for today
        app_logs = DeviceApplicationLog.query.filter(
            db.func.date(DeviceApplicationLog.timestamp) == today
        ).all()

        focus_score, productive_time, distracting_time, neutral_time, total_time = calculate_focus_score(app_logs)

        # Work session blocks and idle insights
        activity_logs = DeviceActivityLog.query.filter(
            db.func.date(DeviceActivityLog.timestamp) == today
        ).all()
        work_sessions = build_work_sessions(activity_logs)
        longest_idle_seconds = calculate_longest_idle_seconds(activity_logs)

        return jsonify({
            'success': True,
            'productivity': {
                'focus_score': focus_score,
                'productive_seconds': productive_time,
                'neutral_seconds': neutral_time,
                'distracting_seconds': distracting_time,
                'non_productive_seconds': distracting_time + neutral_time,
                'longest_idle_seconds': longest_idle_seconds,
                'total_tracked_seconds': total_time
            },
            'work_sessions': work_sessions,
            'timestamp': datetime.utcnow().isoformat()
        })
        
    except Exception as e:
        print(f"Error calculating metrics: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@tracking_bp.route('/api/tracking/metrics/security')
def api_security_metrics():
    """Get security risk metrics and unusual activity alerts."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    try:
        refresh_requested = request.args.get('refresh') == '1'
        refresh_tracking_snapshot(force=refresh_requested, force_log=refresh_requested)

        today = datetime.utcnow().date()
        resource_logs = DeviceResourceLog.query.filter(
            db.func.date(DeviceResourceLog.timestamp) == today
        ).order_by(DeviceResourceLog.device_id.asc(), DeviceResourceLog.timestamp.asc()).all()

        device_stats = {}
        total_upload_kb = 0
        total_download_kb = 0
        high_cpu_events_total = 0

        last_ts_by_device = {}
        for log in resource_logs:
            stats = device_stats.setdefault(log.device_id, {
                'device_id': log.device_id,
                'high_cpu_events': 0,
                'high_mem_events': 0,
                'total_upload_kb': 0,
                'total_download_kb': 0,
                'risk_score': 0
            })

            if (log.cpu_usage or 0) > 90:
                stats['high_cpu_events'] += 1
                high_cpu_events_total += 1
            if (log.memory_usage or 0) > 90:
                stats['high_mem_events'] += 1

            interval_seconds = _calc_interval_seconds(log, last_ts_by_device)
            upload_kb = (log.upload_kbps or 0) * interval_seconds
            download_kb = (log.download_kbps or 0) * interval_seconds
            stats['total_upload_kb'] += upload_kb
            stats['total_download_kb'] += download_kb
            total_upload_kb += upload_kb
            total_download_kb += download_kb

        device_ids = list(device_stats.keys())
        devices = TrackedDevice.query.filter(TrackedDevice.id.in_(device_ids)).all() if device_ids else []
        device_lookup = {device.id: device.device_name for device in devices}

        risk_devices = []
        for device_id, stats in device_stats.items():
            risk_score = 0
            if stats['high_cpu_events'] > 10:
                risk_score += 20
            if stats['total_upload_kb'] > 500 * 1024:
                risk_score += 30
            if stats['total_download_kb'] > 0 and stats['total_upload_kb'] > stats['total_download_kb'] * 1.5:
                risk_score += 40

            stats['risk_score'] = min(100, risk_score)
            stats['device_name'] = device_lookup.get(device_id, 'Unknown')
            stats['upload_mb'] = round(stats['total_upload_kb'] / 1024, 2)
            stats['download_mb'] = round(stats['total_download_kb'] / 1024, 2)
            risk_devices.append(stats)

        risk_devices.sort(key=lambda entry: entry['risk_score'], reverse=True)
        highest_risk_device = risk_devices[0] if risk_devices else None
        highest_risk_score = highest_risk_device['risk_score'] if highest_risk_device else 0
        high_risk_count = sum(1 for entry in risk_devices if entry['risk_score'] > 70)

        total_upload_mb = round(total_upload_kb / 1024, 2)
        total_download_mb = round(total_download_kb / 1024, 2)
        upload_download_ratio = round(total_upload_kb / total_download_kb, 2) if total_download_kb > 0 else 0

        alerts = []
        if highest_risk_device and highest_risk_score >= 70:
            alerts.append({
                'type': 'high_risk_device',
                'message': f"High risk device: {highest_risk_device['device_name']} (score {highest_risk_score})",
                'severity': 'warning'
            })
        if total_upload_mb > 500:
            alerts.append({
                'type': 'high_upload',
                'message': f"High total upload volume today: {total_upload_mb} MB",
                'severity': 'warning'
            })
        if upload_download_ratio > 1.5 and total_upload_mb > 50:
            alerts.append({
                'type': 'upload_ratio',
                'message': f"Upload-to-download ratio elevated: {upload_download_ratio}x",
                'severity': 'info'
            })
        if high_cpu_events_total > 25:
            alerts.append({
                'type': 'cpu_spikes',
                'message': f"High CPU spikes detected: {high_cpu_events_total} events",
                'severity': 'info'
            })

        return jsonify({
            'success': True,
            'security': {
                'highest_risk_score': highest_risk_score,
                'highest_risk_device': highest_risk_device,
                'high_risk_count': high_risk_count,
                'network_upload_mb': total_upload_mb,
                'network_download_mb': total_download_mb,
                'upload_download_ratio': upload_download_ratio,
                'unusual_activity_alerts': alerts
            },
            'top_risk_devices': risk_devices[:10],
            'timestamp': datetime.utcnow().isoformat()
        })

    except Exception as e:
        print(f"Error calculating security metrics: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@tracking_bp.route('/api/tracking/metrics/performance')
def api_performance_metrics():
    """Get performance metrics (CPU heatmap data)."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    try:
        refresh_requested = request.args.get('refresh') == '1'
        refresh_tracking_snapshot(force=refresh_requested, force_log=refresh_requested)

        today = datetime.utcnow().date()
        resource_logs = DeviceResourceLog.query.filter(
            db.func.date(DeviceResourceLog.timestamp) == today
        ).all()

        hourly_samples = {hour: [] for hour in range(24)}
        for log in resource_logs:
            if log.cpu_usage is None:
                continue
            hourly_samples[log.timestamp.hour].append(log.cpu_usage)

        heatmap = []
        for hour in range(24):
            samples = hourly_samples.get(hour, [])
            avg_cpu = float(np.mean(samples)) if samples else 0.0
            heatmap.append({
                'hour': hour,
                'avg_cpu': round(avg_cpu, 2),
                'samples': len(samples)
            })

        return jsonify({
            'success': True,
            'cpu_heatmap': heatmap,
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        print(f"Error calculating performance metrics: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@tracking_bp.route('/api/tracking/metrics/details/<metric_type>')
def api_metric_details(metric_type):
    """Get detailed breakdown for a specific metric"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
    try:
        refresh_requested = request.args.get('refresh') == '1'
        refresh_tracking_snapshot(force=refresh_requested, force_log=refresh_requested)

        today = datetime.utcnow().date()
        details = []
        
        if metric_type == 'productivity':
            # Breakdown of applications by duration
            app_logs = DeviceApplicationLog.query.filter(
                db.func.date(DeviceApplicationLog.timestamp) == today
            ).all()
            
            app_usage = {}
            for log in app_logs:
                app = log.application_name
                dur = log.duration or 60
                app_usage[app] = app_usage.get(app, 0) + dur
            
            results = []
            for app, duration in app_usage.items():
                category = classify_app(app).title()
                
                results.append({
                    'name': app,
                    'duration_seconds': duration,
                    'category': category,
                    'duration_formatted': f"{duration // 3600}h {(duration % 3600) // 60}m"
                })
            
            # Sort by duration desc
            details = sorted(results, key=lambda x: x['duration_seconds'], reverse=True)[:20]
            
        elif metric_type == 'security':
            # List devices with high resource usage
            resource_logs = DeviceResourceLog.query.filter(
                db.func.date(DeviceResourceLog.timestamp) == today
            ).order_by(DeviceResourceLog.device_id.asc(), DeviceResourceLog.timestamp.asc()).all()
            
            device_risks = {}
            last_ts_by_device = {}
            for log in resource_logs:
                if log.device_id not in device_risks:
                    device = TrackedDevice.query.get(log.device_id)
                    device_risks[log.device_id] = {
                        'device_name': device.device_name if device else 'Unknown',
                        'high_cpu_events': 0,
                        'high_mem_events': 0,
                        'total_upload': 0,
                        'total_download': 0,
                        'risk_score': 0
                    }
                
                if (log.cpu_usage or 0) > 90: device_risks[log.device_id]['high_cpu_events'] += 1
                if (log.memory_usage or 0) > 90: device_risks[log.device_id]['high_mem_events'] += 1
                interval_seconds = _calc_interval_seconds(log, last_ts_by_device)
                device_risks[log.device_id]['total_upload'] += (log.upload_kbps or 0) * interval_seconds
                device_risks[log.device_id]['total_download'] += (log.download_kbps or 0) * interval_seconds
            
            # Filter for "risky" ones (any high event or high upload)
            final_list = []
            for did, data in device_risks.items():
                risk_score = 0
                if data['high_cpu_events'] > 10:
                    risk_score += 20
                if data['total_upload'] > 500 * 1024:
                    risk_score += 30
                if data['total_download'] > 0 and data['total_upload'] > data['total_download'] * 1.5:
                    risk_score += 40
                data['risk_score'] = min(100, risk_score)

                if data['high_cpu_events'] > 0 or data['high_mem_events'] > 0 or data['total_upload'] > 102400: # 100MB
                     data['upload_mb'] = round(data['total_upload'] / 1024, 2)
                     data['download_mb'] = round(data['total_download'] / 1024, 2)
                     final_list.append(data)
            
            details = sorted(final_list, key=lambda x: x['risk_score'], reverse=True)

        elif metric_type == 'network':
             # Top network consumers
            resource_logs = DeviceResourceLog.query.filter(
                db.func.date(DeviceResourceLog.timestamp) == today
            ).order_by(DeviceResourceLog.device_id.asc(), DeviceResourceLog.timestamp.asc()).all()
            
            device_net = {}
            last_ts_by_device = {}
            for log in resource_logs:
                if log.device_id not in device_net:
                    device = TrackedDevice.query.get(log.device_id)
                    device_net[log.device_id] = {
                        'device_name': device.device_name if device else 'Unknown',
                        'upload_kb': 0,
                        'download_kb': 0
                    }
                interval_seconds = _calc_interval_seconds(log, last_ts_by_device)
                device_net[log.device_id]['upload_kb'] += (log.upload_kbps or 0) * interval_seconds
                device_net[log.device_id]['download_kb'] += (log.download_kbps or 0) * interval_seconds
            
            results = []
            for did, data in device_net.items():
                results.append({
                    'device_name': data['device_name'],
                    'upload_mb': round(data['upload_kb'] / 1024, 2),
                    'download_mb': round(data['download_kb'] / 1024, 2),
                    'total_mb': round((data['upload_kb'] + data['download_kb']) / 1024, 2)
                })
            
            details = sorted(results, key=lambda x: x['total_mb'], reverse=True)

        return jsonify({'success': True, 'type': metric_type, 'data': details})

    except Exception as e:
        print(f"Error getting metric details: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
