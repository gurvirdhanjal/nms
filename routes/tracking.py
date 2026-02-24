from flask import Blueprint, render_template, jsonify, request, session, redirect, url_for, Response
from middleware.rbac import require_login
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
import logging
from urllib.parse import urlparse
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import io

tracking_bp = Blueprint('tracking_bp', __name__)

@tracking_bp.before_request
def _tracking_auth_guard():
    # Only enforce tracking blueprint specific auth on specific endpoints.
    # The application-wide auth is handled by standard require_login decorators.
    pass

# Use centralized config for API key
from config import Config
SHARED_API_KEY = Config.API_KEY
logger = logging.getLogger(__name__)


class AgentHttpError(Exception):
    def __init__(self, code, message, original=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.original = original


_agent_http_session = None
_proxy_bypass_logged = False


def _get_agent_http_session():
    global _agent_http_session, _proxy_bypass_logged
    if _agent_http_session is None:
        session = requests.Session()
        # Critical for monitoring: never inherit host proxy env for LAN agent calls.
        session.trust_env = False
        _agent_http_session = session
        if not _proxy_bypass_logged:
            logger.info("[AgentHTTP] proxy-bypass enabled (trust_env=False) for service.py polling")
            _proxy_bypass_logged = True
    return _agent_http_session


def _map_agent_request_error(exc):
    if isinstance(exc, requests.exceptions.ProxyError):
        return "AGENT_PROXY_BLOCKED", "Agent request blocked by proxy settings"
    if isinstance(exc, requests.exceptions.Timeout):
        return "AGENT_TIMEOUT", "Agent request timed out"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "AGENT_UNREACHABLE", "Could not connect to agent endpoint"
    return "AGENT_REQUEST_FAILED", "Agent request failed"


def _agent_http_request(method, url, timeout=2.0, headers=None, stream=False):
    parsed = urlparse(url)
    started = time.monotonic()
    session = _get_agent_http_session()
    try:
        response = session.request(
            method=method,
            url=url,
            timeout=timeout,
            headers=headers,
            stream=stream,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "[AgentHTTP] method=%s host=%s path=%s result=ok status=%s latency_ms=%s",
            method.upper(),
            parsed.hostname,
            parsed.path,
            response.status_code,
            latency_ms,
        )
        return response
    except requests.exceptions.RequestException as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        code, message = _map_agent_request_error(exc)
        logger.warning(
            "[AgentHTTP] method=%s host=%s path=%s result=%s latency_ms=%s error=%s",
            method.upper(),
            parsed.hostname,
            parsed.path,
            code.lower(),
            latency_ms,
            exc,
        )
        raise AgentHttpError(code, message, original=exc) from exc


def _agent_http_get(url, timeout=2.0, headers=None, stream=False):
    return _agent_http_request("GET", url, timeout=timeout, headers=headers, stream=stream)


def _agent_http_post(url, timeout=2.0, headers=None, json_data=None, stream=False):
    parsed = urlparse(url)
    started = time.monotonic()
    session = _get_agent_http_session()
    try:
        response = session.request(
            method="POST",
            url=url,
            timeout=timeout,
            headers=headers,
            json=json_data,
            stream=stream,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "[AgentHTTP] method=POST host=%s path=%s result=ok status=%s latency_ms=%s",
            parsed.hostname,
            parsed.path,
            response.status_code,
            latency_ms,
        )
        return response
    except requests.exceptions.RequestException as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        code, message = _map_agent_request_error(exc)
        logger.warning(
            "[AgentHTTP] method=POST host=%s path=%s result=%s latency_ms=%s error=%s",
            parsed.hostname,
            parsed.path,
            code.lower(),
            latency_ms,
            exc,
        )
        raise AgentHttpError(code, message, original=exc) from exc


def _agent_error_response(error, status=503):
    return jsonify({
        'success': False,
        'error_code': error.code,
        'error': error.message,
    }), status


def _json_error(error_code, message, status=400):
    return jsonify({
        'success': False,
        'error_code': error_code,
        'error': message,
    }), status


def _json_exception(error_code, message, exc=None, status=500):
    if exc is not None:
        logger.exception("[TrackingAPI] %s (%s): %s", message, error_code, exc)
    else:
        logger.error("[TrackingAPI] %s (%s)", message, error_code)
    return _json_error(error_code, message, status)


def _extract_tracking_api_key():
    api_key = (request.headers.get('X-API-Key') or '').strip()
    if api_key:
        return api_key
    payload = request.get_json(silent=True) or {}
    return str(payload.get('api_key') or '').strip()


def _require_tracking_api_key():
    provided_key = _extract_tracking_api_key()
    if not provided_key or provided_key != SHARED_API_KEY:
        return _json_error('SESSION_EXPIRED', 'Unauthorized agent sync request.', 401)
    return None

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

    def _resolve_probe_profile(self, profile):
        if profile == 'interactive':
            return {
                'identity_timeout': max(self.timeout, 2.5),
                'stats_timeout': max(self.timeout, 3.0),
                'health_timeout': max(self.timeout, 2.0),
                'return_offline': True,
            }
        return {
            'identity_timeout': self.timeout,
            'stats_timeout': self.timeout,
            'health_timeout': self.timeout,
            'return_offline': False,
        }

    def _build_probe_result(
        self,
        availability_status,
        tracking_status,
        data=None,
        metrics_available=False,
        probe_error_code=None,
        probe_method=None,
        identity=None,
    ):
        payload = data if isinstance(data, dict) else {}
        identity_payload = identity if isinstance(identity, dict) else None
        return {
            'status': tracking_status,
            'tracking_status': tracking_status,
            'availability_status': availability_status,
            'metrics_available': bool(metrics_available),
            'probe_error_code': probe_error_code,
            'probe_method': probe_method,
            'data': payload,
            'identity': identity_payload,
            'last_probe_at': datetime.utcnow().isoformat(),
        }
    
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
    
    def check_tracking_service(self, ip, port=5002, profile='scan'):
        """Check if tracking service is running and classify availability."""
        probe_cfg = self._resolve_probe_profile(profile)
        base_url = f"http://{ip}:{port}"
        identity_data = {}
        probe_error_code = None

        try:
            # 1) Identity probe
            try:
                identity_response = _agent_http_get(
                    f"{base_url}/api/identity",
                    timeout=probe_cfg['identity_timeout'],
                )
                if identity_response.status_code == 200:
                    identity_payload = identity_response.json()
                    identity_data = identity_payload if isinstance(identity_payload, dict) else {}
                else:
                    probe_error_code = f"IDENTITY_HTTP_{identity_response.status_code}"
            except AgentHttpError as error:
                probe_error_code = error.code
            except Exception as error:
                logger.debug("[TrackingProbe] identity parse failure ip=%s err=%s", ip, error)

            # 2) Full stats probe
            try:
                stats_response = _agent_http_get(
                    f"{base_url}/api/secure/stats",
                    timeout=probe_cfg['stats_timeout'],
                    headers={'X-API-Key': SHARED_API_KEY},
                )
                if stats_response.status_code == 200:
                    stats_payload = stats_response.json()
                    stats_data = stats_payload if isinstance(stats_payload, dict) else {}
                    if identity_data:
                        device_info = stats_data.get('device_info')
                        if isinstance(device_info, dict):
                            for key, value in identity_data.items():
                                device_info.setdefault(key, value)
                        else:
                            stats_data['device_info'] = identity_data
                    return self._build_probe_result(
                        availability_status='online',
                        tracking_status='tracking_active',
                        data=stats_data,
                        metrics_available=True,
                        probe_error_code=None,
                        probe_method='stats',
                        identity=identity_data,
                    )
                if not probe_error_code:
                    probe_error_code = f"STATS_HTTP_{stats_response.status_code}"
            except AgentHttpError as error:
                probe_error_code = error.code
            except Exception as error:
                logger.debug("[TrackingProbe] stats parse failure ip=%s err=%s", ip, error)

            # 3) Identity reachable but metrics unavailable -> degraded
            if identity_data:
                return self._build_probe_result(
                    availability_status='degraded',
                    tracking_status='tracking_active',
                    data={'device_info': identity_data},
                    metrics_available=False,
                    probe_error_code=probe_error_code,
                    probe_method='identity',
                    identity=identity_data,
                )

            # 4) Health fallback -> degraded
            try:
                health_response = _agent_http_get(
                    f"{base_url}/api/health",
                    timeout=probe_cfg['health_timeout'],
                )
                if health_response.status_code == 200:
                    return self._build_probe_result(
                        availability_status='degraded',
                        tracking_status='tracking_active',
                        data={'device_info': identity_data} if identity_data else {},
                        metrics_available=False,
                        probe_error_code=probe_error_code,
                        probe_method='health',
                        identity=identity_data,
                    )
                if not probe_error_code:
                    probe_error_code = f"HEALTH_HTTP_{health_response.status_code}"
            except AgentHttpError as error:
                probe_error_code = error.code
            except Exception as error:
                logger.debug("[TrackingProbe] health parse failure ip=%s err=%s", ip, error)

            # 5) Port open but service signature missing -> degraded
            if self.check_port_open(ip, port):
                return self._build_probe_result(
                    availability_status='degraded',
                    tracking_status='port_open_no_service',
                    data={},
                    metrics_available=False,
                    probe_error_code=probe_error_code or 'AGENT_SERVICE_NOT_IDENTIFIED',
                    probe_method='port',
                    identity=identity_data,
                )

            # 6) Fully unreachable
            offline_result = self._build_probe_result(
                availability_status='offline',
                tracking_status='offline',
                data={},
                metrics_available=False,
                probe_error_code=probe_error_code or 'AGENT_UNREACHABLE',
                probe_method='none',
                identity=identity_data,
            )
            if probe_cfg.get('return_offline'):
                return offline_result
            return None
        except Exception as error:
            logger.warning("[TrackingProbe] ip=%s unexpected_error=%s", ip, error)
            if probe_cfg.get('return_offline'):
                return self._build_probe_result(
                    availability_status='offline',
                    tracking_status='offline',
                    data={},
                    metrics_available=False,
                    probe_error_code='AGENT_REQUEST_FAILED',
                    probe_method='none',
                    identity=identity_data,
                )
            return None
    
    def scan_single_ip(self, ip):
        """Scan a single IP"""
        try:
            service_info = self.check_tracking_service(ip)
            if not service_info:
                return None

            tracking_status = service_info.get('tracking_status') or service_info.get('status', 'unknown')
            availability_status = service_info.get('availability_status', 'offline')

            # After a successful HTTP/port check, ARP cache is warm—MAC lookup is more reliable.
            mac = self.get_mac_address(ip)

            try:
                hostname = socket.gethostbyaddr(ip)[0]
            except:
                hostname = "Unknown"

            device_info = {
                'ip': ip,
                'port': 5002,
                'status': tracking_status,
                'availability_status': availability_status,
                'mac_address': mac,
                'unique_client_id': None,
                'hostname': hostname,
                'system': 'Unknown',
                'tracking_data': service_info.get('data'),
                'metrics_available': bool(service_info.get('metrics_available')),
                'probe_error_code': service_info.get('probe_error_code'),
                'probe_method': service_info.get('probe_method'),
            }

            if tracking_status == 'tracking_active' and service_info.get('data'):
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


def _normalize_mac(raw_value):
    if raw_value is None:
        return None
    mac = str(raw_value).strip().upper().replace('-', ':')
    if mac in ('', 'N/A', 'UNKNOWN'):
        return None
    parts = mac.split(':')
    if len(parts) != 6:
        return None
    if any(len(part) != 2 for part in parts):
        return None
    try:
        int(''.join(parts), 16)
    except ValueError:
        return None
    return ':'.join(parts)


def _extract_identity_from_service_info(service_info):
    if not isinstance(service_info, dict):
        return {}
    data = service_info.get('data') if isinstance(service_info.get('data'), dict) else {}
    device_info = data.get('device_info') if isinstance(data.get('device_info'), dict) else {}
    identity = service_info.get('identity') if isinstance(service_info.get('identity'), dict) else {}

    raw_mac = (
        device_info.get('mac_address')
        or identity.get('mac_address')
        or data.get('mac_address')
    )
    return {
        'mac_address': _normalize_mac(raw_mac),
        'hostname': (
            device_info.get('hostname')
            or identity.get('hostname')
            or data.get('hostname')
        ),
        'unique_client_id': (
            device_info.get('unique_client_id')
            or identity.get('unique_client_id')
            or data.get('unique_client_id')
        ),
    }


def _find_tracked_device(mac_address=None, unique_client_id=None):
    if unique_client_id:
        existing = TrackedDevice.query.filter_by(unique_client_id=unique_client_id).first()
        if existing:
            return existing
    if mac_address:
        return TrackedDevice.query.filter_by(mac_address=mac_address).first()
    return None

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
        scanner.timeout = 2.5
        touched_devices = False

        for device in devices:
            if not device.ip_address:
                continue

            service_info = scanner.check_tracking_service(device.ip_address, profile='interactive')
            if not service_info:
                continue

            availability_status = service_info.get('availability_status', 'offline')
            if availability_status == 'offline':
                continue

            tracking_data = service_info.get('data') or {}
            has_metrics = (
                tracking_data.get('system_metrics') or
                tracking_data.get('today_stats') or
                tracking_data.get('current_activity')
            )

            # Reachable (online or degraded) updates last_seen for stable list status.
            device.last_seen = datetime.utcnow()
            touched_devices = True
            cache_entry = real_time_data.get(device.mac_address, {})
            last_log_time = cache_entry.get('last_log_time', 0)
            fallback_data = cache_entry.get('data') if isinstance(cache_entry.get('data'), dict) else {}
            metrics_stale = False
            cached_tracking_data = tracking_data
            if not has_metrics and fallback_data:
                cached_tracking_data = fallback_data
                metrics_stale = True

            real_time_data[device.mac_address] = {
                'data': cached_tracking_data,
                'status': availability_status,
                'availability_status': availability_status,
                'device_info': device_to_dict(device),
                'timestamp': time.time(),
                'last_log_time': last_log_time,
                'metrics_available': bool(has_metrics),
                'metrics_stale': metrics_stale,
                'probe_method': service_info.get('probe_method'),
                'probe_error_code': service_info.get('probe_error_code'),
            }

            # Throttled DB logging (force_log allows on-demand freshness)
            if has_metrics and (force_log or time.time() - last_log_time > 60):
                log_device_data(device.id, tracking_data)
                real_time_data[device.mac_address]['last_log_time'] = time.time()

            refreshed += 1

        if refreshed or touched_devices:
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
    try:
        force_refresh = request.args.get('force') == '1'

        # Check cache first (CACHE HIT)
        if not force_refresh and mac_address in real_time_data:
            cached = real_time_data[mac_address]
            if time.time() - cached['timestamp'] < 5:
                cached_data = cached.get('data') if isinstance(cached.get('data'), dict) else {}
                availability_status = cached.get('availability_status') or (
                    'offline' if cached.get('status') == 'offline' else 'online'
                )
                probe_method = cached.get('probe_method')
                probe_error_code = cached.get('probe_error_code')

                if availability_status == 'offline':
                    return jsonify({
                        'success': False,
                        'error_code': probe_error_code or 'AGENT_UNREACHABLE',
                        'error': 'Device not responding (Cached)',
                        'device_info': cached.get('device_info'),
                        'availability_status': 'offline',
                        'metrics_available': False,
                        'metrics_stale': False,
                        'probe': {
                            'method': probe_method,
                            'error_code': probe_error_code or 'AGENT_UNREACHABLE',
                        },
                    }), 503

                return jsonify({
                    'success': True,
                    'tracking_data': cached_data,
                    'device_info': cached.get('device_info'),
                    'timestamp': datetime.fromtimestamp(cached['timestamp']).isoformat(),
                    'cached': True,
                    'availability_status': availability_status,
                    'metrics_available': bool(cached.get('metrics_available')),
                    'metrics_stale': bool(cached.get('metrics_stale')),
                    'probe': {
                        'method': probe_method,
                        'error_code': probe_error_code,
                    },
                })

        device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
        if not device or not device.ip_address:
            return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)

        # Get live data from device using interactive probe profile.
        scanner = NetworkScanner()
        scanner.timeout = 2.5
        service_info = scanner.check_tracking_service(device.ip_address, profile='interactive')
        availability_status = service_info.get('availability_status', 'offline') if isinstance(service_info, dict) else 'offline'

        if service_info and availability_status in ('online', 'degraded'):
            raw_tracking_data = service_info.get('data') or {}
            has_metrics = bool(
                raw_tracking_data.get('system_metrics') or
                raw_tracking_data.get('today_stats') or
                raw_tracking_data.get('current_activity')
            )

            cached_entry = real_time_data.get(mac_address, {})
            last_log_time = cached_entry.get('last_log_time', 0)
            fallback_data = cached_entry.get('data') if isinstance(cached_entry.get('data'), dict) else {}
            tracking_data = raw_tracking_data
            metrics_stale = False
            if not has_metrics and fallback_data:
                tracking_data = fallback_data
                metrics_stale = True

            now_utc = datetime.utcnow()
            should_commit_last_seen = (
                not device.last_seen or
                (now_utc - device.last_seen).total_seconds() >= 30
            )
            device.last_seen = now_utc
            device_info_payload = device_to_dict(device)

            real_time_data[mac_address] = {
                'data': tracking_data,
                'status': availability_status,
                'availability_status': availability_status,
                'device_info': device_info_payload,
                'timestamp': time.time(),
                'last_log_time': last_log_time,
                'metrics_available': bool(has_metrics),
                'metrics_stale': metrics_stale,
                'probe_method': service_info.get('probe_method'),
                'probe_error_code': service_info.get('probe_error_code'),
            }

            if has_metrics and time.time() - last_log_time > 60:
                log_device_data(device.id, tracking_data)
                real_time_data[mac_address]['last_log_time'] = time.time()
            elif should_commit_last_seen:
                db.session.commit()

            return jsonify({
                'success': True,
                'tracking_data': tracking_data,
                'device_info': device_info_payload,
                'timestamp': now_utc.isoformat(),
                'availability_status': availability_status,
                'metrics_available': bool(has_metrics),
                'metrics_stale': metrics_stale,
                'probe': {
                    'method': service_info.get('probe_method'),
                    'error_code': service_info.get('probe_error_code'),
                },
            })

        probe_error_code = service_info.get('probe_error_code') if isinstance(service_info, dict) else None
        probe_method = service_info.get('probe_method') if isinstance(service_info, dict) else None
        real_time_data[mac_address] = {
            'data': None,
            'status': 'offline',
            'availability_status': 'offline',
            'device_info': device_to_dict(device),
            'timestamp': time.time(),
            'probe_error_code': probe_error_code or 'AGENT_UNREACHABLE',
            'probe_method': probe_method,
            'metrics_available': False,
            'metrics_stale': False,
        }

        return jsonify({
            'success': False,
            'error_code': probe_error_code or 'AGENT_UNREACHABLE',
            'error': 'Device not responding',
            'device_info': device_to_dict(device),
            'availability_status': 'offline',
            'metrics_available': False,
            'metrics_stale': False,
            'probe': {
                'method': probe_method,
                'error_code': probe_error_code or 'AGENT_UNREACHABLE',
            },
        }), 503
    except Exception as e:
        return _json_exception(
            'REAL_TIME_TRACKING_FAILED',
            'Failed to fetch real-time tracking data.',
            e,
        )

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
        return _json_exception(
            'ACTIVITY_HISTORY_FAILED',
            'Failed to load activity history.',
            e,
        )

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
        return _json_exception(
            'RESOURCE_HISTORY_FAILED',
            'Failed to load resource history.',
            e,
        )

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
        return _json_exception(
            'APPLICATION_HISTORY_FAILED',
            'Failed to load application history.',
            e,
        )



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
                response = _agent_http_get(
                    f"http://{device.ip_address}:5002/stream",
                    timeout=5,
                    headers={'X-API-Key': SHARED_API_KEY},
                    stream=True,
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
            except AgentHttpError as e:
                consecutive_errors += 1
                print(f"Screenshot stream agent error for {device.ip_address}: {e.code}")
                yield generate_placeholder_image(e.code)
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
                with _agent_http_get(
                    f"http://{device.ip_address}:5002/start_camera",
                    timeout=5,
                    headers={'X-API-Key': SHARED_API_KEY},
                    stream=True,
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
            except AgentHttpError as e:
                consecutive_errors += 1
                print(f"Camera stream agent error for {device.ip_address}: {e.code}")
                yield generate_placeholder_image(e.code)
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
         return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)
    
    def generate():
        try:
            # Connect to the device's audio stream
            # stream=True is crucial here
            with _agent_http_get(
                f"http://{device.ip_address}:5002/audio_stream.wav",
                timeout=5,
                headers={'X-API-Key': SHARED_API_KEY},
                stream=True,
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
        except AgentHttpError as e:
            print(f"Audio stream agent error for {device.ip_address}: {e.code}")
            return
        except Exception as e:
            print(f"Audio stream error for {device.ip_address}: {e}")
            return

    # Return audio stream (WAV container for browser compatibility)
    return Response(generate(), mimetype='audio/wav')


@tracking_bp.route('/api/tracking/toggle-mic/<mac_address>', methods=['POST'])
def api_toggle_mic(mac_address):
    """Toggle microphone state"""
    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)

    try:
        status_resp = _agent_http_get(
            f"http://{device.ip_address}:5002/mic_status",
            timeout=2,
            headers={'X-API-Key': SHARED_API_KEY},
        )

        if status_resp.status_code != 200:
            return _json_error('AGENT_MIC_STATUS_FAILED', 'Failed to check mic status', 502)

        is_active = bool(status_resp.json().get('active', False))
        if is_active:
            action_resp = _agent_http_get(
                f"http://{device.ip_address}:5002/stop_mic",
                timeout=2,
                headers={'X-API-Key': SHARED_API_KEY},
            )
            if action_resp.status_code != 200:
                return _json_error('AGENT_MIC_STOP_FAILED', 'Failed to stop microphone', 502)
            time.sleep(0.2)
            return jsonify({'success': True, 'action': 'stopped'})

        # Mic startup is handled when /audio_stream.wav is requested by the player.
        return jsonify({'success': True, 'action': 'ready'})

    except AgentHttpError as e:
        return _agent_error_response(e, status=503)
    except Exception as e:
        return _json_exception(
            'TOGGLE_MIC_FAILED',
            'Failed to toggle microphone state.',
            e,
        )


@tracking_bp.route('/api/tracking/stream/camera/<mac_address>')
def proxy_camera_stream(mac_address):
    """Proxy camera stream from device"""
    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return Response("Device not found or offline", 404)
        
    def generate():
        try:
            # Connect to the service's /start_camera stream
            # Note: stream=True is crucial for MJPEG
            resp = _agent_http_get(
                f"http://{device.ip_address}:5002/start_camera",
                stream=True,
                timeout=5, # Connection timeout
                headers={'X-API-Key': SHARED_API_KEY},
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
        except AgentHttpError as e:
            print(f"Camera proxy agent error for {mac_address}: {e.code}")
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + 
                   generate_placeholder_image(e.code) + 
                   b'\r\n')
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
        return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)
    
    try:
        response = _agent_http_post(
            f"http://{device.ip_address}:5002/toggle_camera",
            timeout=5,
            headers={'X-API-Key': SHARED_API_KEY},
        )
        
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return _json_error('AGENT_CAMERA_TOGGLE_FAILED', f'Device returned {response.status_code}', 502)
    except AgentHttpError as e:
        return _agent_error_response(e, status=503)
    except Exception as e:
        return _json_exception(
            'TOGGLE_CAMERA_FAILED',
            'Failed to toggle camera state.',
            e,
        )


@tracking_bp.route('/api/tracking/stop-camera/<mac_address>', methods=['POST'])
def api_stop_camera(mac_address):
    """Stop camera stream on device"""
    
    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)
    
    try:
        response = _agent_http_get(
            f"http://{device.ip_address}:5002/stop_camera",
            timeout=3,
            headers={'X-API-Key': SHARED_API_KEY},
        )
        
        if response.status_code == 200:
            return jsonify({"success": True, "message": "Camera stopped"})
        else:
            return _json_error('AGENT_CAMERA_STOP_FAILED', 'Failed to stop camera', 502)
    except AgentHttpError as e:
        return _agent_error_response(e, status=503)
    except Exception as e:
        print(f"Error stopping camera: {e}")
        return _json_exception(
            'STOP_CAMERA_FAILED',
            'Failed to stop camera stream.',
            e,
        )




# ============================================================
# DEVICE MANAGEMENT ENDPOINTS
# ============================================================

@tracking_bp.route('/api/tracking/scan', methods=['POST'])
def api_scan_devices():
    """Scan network for devices"""
    print("[DEBUG] /api/tracking/scan endpoint called!")
    
    try:
        scanner = NetworkScanner()
        devices_found = scanner.scan_for_trackable_devices()

        saved_devices = TrackedDevice.query.all()
        saved_macs = {str(device.mac_address).upper(): device for device in saved_devices if device.mac_address}

        enhanced_devices = []
        updated_ips = []
        auto_saved_devices = []

        for device in devices_found:
            mac = _normalize_mac(device.get('mac_address'))
            status = device.get('status')
            unique_client_id = (device.get('unique_client_id') or '').strip() or None
            scanned_hostname = (device.get('hostname') or '').strip() or None

            existing_by_identity = _find_tracked_device(mac_address=mac, unique_client_id=unique_client_id)
            if existing_by_identity and mac and mac not in saved_macs:
                saved_macs[mac] = existing_by_identity

            if status == 'tracking_active' and mac and mac not in saved_macs:
                try:
                    new_device = TrackedDevice(
                        mac_address=mac,
                        unique_client_id=unique_client_id,
                        device_name=scanned_hostname or f"Device_{mac[-5:].replace(':', '')}",
                        employee_name="Auto-Discovered",
                        hostname=scanned_hostname,
                        ip_address=device.get('ip'),
                        department="Unassigned",
                        notes="Auto-discovered by scanner"
                    )
                    db.session.add(new_device)
                    db.session.flush()
                    saved_macs[mac] = new_device
                    auto_saved_devices.append({
                        'device_name': new_device.device_name,
                        'mac_address': mac,
                        'ip_address': new_device.ip_address,
                    })
                    print(f"[AUTO-SAVE] Added new device: {mac} ({device.get('ip')})")
                except Exception as e:
                    print(f"[AUTO-SAVE] Error saving {mac}: {e}")

            saved_device = saved_macs.get(mac) if mac else None
            if saved_device:
                if device.get('ip') and device.get('ip') != saved_device.ip_address:
                    old_ip = saved_device.ip_address
                    saved_device.ip_address = device.get('ip')
                    saved_device.last_seen = datetime.utcnow()
                    updated_ips.append({
                        'device_name': saved_device.device_name,
                        'old_ip': old_ip,
                        'new_ip': device.get('ip')
                    })

                if scanned_hostname and scanned_hostname != saved_device.hostname:
                    saved_device.hostname = scanned_hostname

                if unique_client_id and not saved_device.unique_client_id:
                    saved_device.unique_client_id = unique_client_id

            device_dict = {
                'ip': device.get('ip'),
                'port': device.get('port', 5002),
                'status': status,
                'availability_status': device.get('availability_status', 'offline'),
                'mac_address': mac or 'N/A',
                'hostname': device.get('hostname', 'Unknown'),
                'system': device.get('system', 'Unknown'),
                'tracking_data': device.get('tracking_data'),
                'is_saved': bool(saved_device),
                'metrics_available': bool(device.get('metrics_available')),
                'probe_error_code': device.get('probe_error_code'),
                'probe_method': device.get('probe_method'),
            }

            if saved_device:
                device_dict['saved_info'] = device_to_dict(saved_device)

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
            'new_devices': len(auto_saved_devices),
            'auto_saved_devices': auto_saved_devices,
            'updated_ips': updated_ips,
        })
    except Exception as e:
        db.session.rollback()
        return _json_exception(
            'TRACKING_SCAN_FAILED',
            'Failed to complete tracking network scan.',
            e,
        )

@tracking_bp.route('/api/tracking/save-device', methods=['POST'])
def api_save_device():
    """Save/update device"""
    
    try:
        data = request.json or {}
        ip_address = (data.get('ip_address') or '').strip() or None
        hostname = (data.get('hostname') or '').strip() or None
        unique_client_id = (data.get('unique_client_id') or '').strip() or None
        mac_address = _normalize_mac(data.get('mac_address'))

        if not ip_address and not mac_address:
            return _json_error(
                'DEVICE_IDENTITY_REQUIRED',
                'Provide at least IP address or MAC address to register a device.',
                400,
            )

        # If MAC is missing and IP is provided, resolve identity from service.py endpoint.
        if not mac_address and ip_address:
            scanner = NetworkScanner()
            service_info = scanner.check_tracking_service(ip_address)
            identity = _extract_identity_from_service_info(service_info)
            mac_address = identity.get('mac_address')
            hostname = hostname or identity.get('hostname')
            unique_client_id = unique_client_id or identity.get('unique_client_id')

        if not mac_address:
            return _json_error(
                'IDENTITY_RESOLUTION_FAILED',
                'Could not resolve MAC from service. Ensure service.py is running on the target IP:5002.',
                400,
            )

        device_name = (data.get('device_name') or '').strip() or hostname or f"Device_{mac_address[-5:].replace(':', '')}"
        employee_name = (data.get('employee_name') or '').strip() or None
        department = (data.get('department') or '').strip() or None
        notes = (data.get('notes') or '').strip() or None

        device = _find_tracked_device(mac_address=mac_address, unique_client_id=unique_client_id)

        if device:
            if device.mac_address != mac_address:
                mac_collision = TrackedDevice.query.filter(
                    TrackedDevice.mac_address == mac_address,
                    TrackedDevice.id != device.id
                ).first()
                if mac_collision:
                    return _json_error(
                        'MAC_ALREADY_EXISTS',
                        f'MAC {mac_address} is already assigned to another tracked device.',
                        409,
                    )
                device.mac_address = mac_address

            device.device_name = device_name
            device.employee_name = employee_name
            device.hostname = hostname
            device.ip_address = ip_address
            device.unique_client_id = unique_client_id or device.unique_client_id
            device.department = department
            device.notes = notes
            device.updated_at = datetime.utcnow()
        else:
            device = TrackedDevice(
                mac_address=mac_address,
                unique_client_id=unique_client_id,
                device_name=device_name,
                employee_name=employee_name,
                hostname=hostname,
                ip_address=ip_address,
                department=department,
                notes=notes
            )
            db.session.add(device)

        db.session.commit()
        return jsonify({
            'success': True,
            'message': 'Device saved successfully',
            'device': device_to_dict(device)
        })
    except Exception as e:
        db.session.rollback()
        return _json_exception(
            'SAVE_DEVICE_FAILED',
            'Failed to save tracked device.',
            e,
        )

@tracking_bp.route('/api/tracking/delete-device', methods=['POST'])
def api_delete_device():
    """Delete device"""
    
    try:
        payload = request.get_json(silent=True) or {}
        mac_address = _normalize_mac(payload.get('mac_address'))
        if not mac_address:
            return _json_error('MAC_ADDRESS_REQUIRED', 'MAC address is required.', 400)
        device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
        
        if not device:
            return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)
        
        db.session.delete(device)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Device deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return _json_exception(
            'DELETE_DEVICE_FAILED',
            'Failed to delete tracked device.',
            e,
        )

@tracking_bp.route('/api/tracking/sync-ips', methods=['POST'])
def api_sync_ips():
    """Sync IP addresses for all devices"""
    
    try:
        scanner = NetworkScanner()
        devices_found = scanner.scan_for_trackable_devices()

        saved_devices = TrackedDevice.query.all()
        saved_macs = {str(device.mac_address).upper(): device for device in saved_devices if device.mac_address}

        updated_devices = []
        auto_saved_devices = []

        for device in devices_found:
            mac = _normalize_mac(device.get('mac_address'))
            status = device.get('status')
            unique_client_id = (device.get('unique_client_id') or '').strip() or None
            scanned_hostname = (device.get('hostname') or '').strip() or None
            scanned_ip = device.get('ip')

            existing_by_identity = _find_tracked_device(mac_address=mac, unique_client_id=unique_client_id)
            if existing_by_identity and mac and mac not in saved_macs:
                saved_macs[mac] = existing_by_identity

            if status == 'tracking_active' and mac and mac not in saved_macs:
                new_device = TrackedDevice(
                    mac_address=mac,
                    unique_client_id=unique_client_id,
                    device_name=scanned_hostname or f"Device_{mac[-5:].replace(':', '')}",
                    employee_name="Auto-Discovered",
                    hostname=scanned_hostname,
                    ip_address=scanned_ip,
                    department="Unassigned",
                    notes="Auto-discovered during sync"
                )
                db.session.add(new_device)
                db.session.flush()
                saved_macs[mac] = new_device
                auto_saved_devices.append({
                    'device_name': new_device.device_name,
                    'mac_address': mac,
                    'ip_address': scanned_ip
                })

            saved_device = saved_macs.get(mac) if mac else None
            if saved_device:
                if scanned_ip and scanned_ip != saved_device.ip_address:
                    old_ip = saved_device.ip_address
                    saved_device.ip_address = scanned_ip
                    saved_device.last_seen = datetime.utcnow()
                    updated_devices.append({
                        'device_name': saved_device.device_name,
                        'old_ip': old_ip,
                        'new_ip': scanned_ip
                    })

                if scanned_hostname and scanned_hostname != saved_device.hostname:
                    saved_device.hostname = scanned_hostname

                if unique_client_id and not saved_device.unique_client_id:
                    saved_device.unique_client_id = unique_client_id
        
        if updated_devices or auto_saved_devices:
            db.session.commit()
        
        return jsonify({
            'success': True,
            'updated_devices': updated_devices,
            'auto_saved_devices': auto_saved_devices,
            'message': f'Updated {len(updated_devices)} device(s), auto-saved {len(auto_saved_devices)} new device(s)'
        })
        
    except Exception as e:
        db.session.rollback()
        return _json_exception(
            'SYNC_IPS_FAILED',
            'Failed to sync tracked device IPs.',
            e,
        )


@tracking_bp.route('/api/tracking/register', methods=['GET'])
def api_tracking_register():
    """Compatibility registration endpoint for service auto-discovery."""
    auth_error = _require_tracking_api_key()
    if auth_error:
        return auth_error

    return jsonify({
        'success': True,
        'server_name': 'Device Monitoring Tactical',
        'status': 'active',
        'version': '1.0',
        'timestamp': datetime.utcnow().isoformat(),
    })


@tracking_bp.route('/api/tracking/sync', methods=['POST'])
def api_tracking_sync():
    """Compatibility sync endpoint for service agents."""
    auth_error = _require_tracking_api_key()
    if auth_error:
        return auth_error

    try:
        payload = request.get_json(silent=True) or {}
        mac_address = _normalize_mac(payload.get('mac_address'))
        if not mac_address:
            return _json_error('MAC_ADDRESS_REQUIRED', 'MAC address is required for sync.', 400)

        hostname = (payload.get('hostname') or '').strip() or None
        ip_address = (payload.get('ip_address') or request.remote_addr or '').strip() or None
        unique_client_id = (payload.get('unique_client_id') or '').strip() or None
        now_utc = datetime.utcnow()

        device = _find_tracked_device(mac_address=mac_address, unique_client_id=unique_client_id)
        if not device:
            device_name = hostname or f"Agent_{mac_address[-5:].replace(':', '')}"
            device = TrackedDevice(
                mac_address=mac_address,
                unique_client_id=unique_client_id,
                device_name=device_name,
                employee_name='Auto-Discovered',
                hostname=hostname,
                ip_address=ip_address,
                department='Unassigned',
                notes='Auto-registered by service agent sync',
                last_seen=now_utc,
            )
            db.session.add(device)
            db.session.flush()
        else:
            if ip_address:
                device.ip_address = ip_address
            if hostname:
                device.hostname = hostname
            if unique_client_id and not device.unique_client_id:
                device.unique_client_id = unique_client_id
            if not device.device_name and hostname:
                device.device_name = hostname
            device.last_seen = now_utc
            device.updated_at = now_utc

        current_stats = payload.get('current_stats')
        if isinstance(current_stats, dict):
            has_metrics = bool(
                current_stats.get('system_metrics') or
                current_stats.get('today_stats') or
                current_stats.get('current_activity')
            )
            cached_entry = real_time_data.get(mac_address, {})
            real_time_data[mac_address] = {
                'data': current_stats,
                'status': 'online' if has_metrics else 'degraded',
                'availability_status': 'online' if has_metrics else 'degraded',
                'device_info': device_to_dict(device),
                'timestamp': time.time(),
                'last_log_time': cached_entry.get('last_log_time', 0),
                'metrics_available': has_metrics,
                'metrics_stale': False,
                'probe_method': 'sync',
                'probe_error_code': None,
            }

        db.session.commit()
        return jsonify({
            'success': True,
            'message': 'Sync received',
            'device': device_to_dict(device),
            'synced_at': now_utc.isoformat(),
        })
    except Exception as e:
        db.session.rollback()
        return _json_exception(
            'TRACKING_SYNC_FAILED',
            'Failed to process tracking sync payload.',
            e,
        )

# ============================================================
# LIVE TRACKING ROUTES
# ============================================================

@tracking_bp.route('/tracking/live')
def live_tracking():
    """Live tracking page (separate from main tracking)"""
    saved_devices = TrackedDevice.query.order_by(TrackedDevice.device_name).all()
    
    # Convert devices to serializable dictionaries
    saved_devices_dicts = [device_to_dict(device) for device in saved_devices]
    
    return render_template('tracking/live_tracking.html', 
                         saved_devices=saved_devices,
                         saved_devices_dicts=saved_devices_dicts)

@tracking_bp.route('/api/tracking/live-summary')
def api_live_summary():
    """Get live summary data for all devices pulling from the background-synced DB cache"""
    
    try:
        from extensions import redis_client
        devices = TrackedDevice.query.all()
        summary_data = []
        
        # MGET High-Speed Cache
        redis_results = []
        if redis_client and devices:
            try:
                keys = [f"tracking:probe:{d.mac_address}" for d in devices]
                redis_results = redis_client.mget(keys)
            except Exception:
                redis_results = [None] * len(devices)
        else:
            redis_results = [None] * len(devices)

        for i, device in enumerate(devices):
            tracking_info = {}
            metrics_available = False
            availability_status = 'offline'
            probe_error_code = device.probe_error_code
            probe_method = device.probe_method
            last_probe_at = device.last_probe_at.isoformat() if device.last_probe_at else None
            is_from_redis = False

            # Try Redis first (High Speed Cache)
            if redis_results and i < len(redis_results) and redis_results[i]:
                try:
                    payload = json.loads(redis_results[i])
                    if isinstance(payload, dict):
                        candidate_tracking = payload.get('tracking_data')
                        if isinstance(candidate_tracking, dict):
                            tracking_info = candidate_tracking
                        elif any(key in payload for key in ('current_activity', 'today_stats', 'system_metrics')):
                            # Backward-compatible support for older payload shape
                            tracking_info = payload

                        status_from_cache = str(
                            payload.get('availability_status') or payload.get('status') or ''
                        ).strip().lower()
                        if status_from_cache in ('online', 'degraded', 'offline'):
                            availability_status = status_from_cache
                        elif tracking_info:
                            availability_status = 'online'

                        metrics_available = bool(
                            payload.get('metrics_available', False) or
                            tracking_info.get('system_metrics') or
                            tracking_info.get('today_stats') or
                            tracking_info.get('current_activity')
                        )
                        probe_error_code = payload.get('probe_error_code')
                        probe_method = payload.get('probe_method') or 'redis'
                        last_probe_at = payload.get('last_probe_at') or datetime.utcnow().isoformat()
                        is_from_redis = True
                except Exception:
                    pass

            # DB Fallback (Durable State)
            if not is_from_redis:
                if device.tracking_data:
                    try:
                        tracking_info = json.loads(device.tracking_data)
                    except Exception:
                        pass
                availability_status = str(device.availability_status or 'offline').strip().lower()
                if availability_status not in ('online', 'degraded', 'offline'):
                    availability_status = 'offline'
                metrics_available = bool(device.metrics_available)
                probe_error_code = device.probe_error_code
                probe_method = device.probe_method
                last_probe_at = device.last_probe_at.isoformat() if device.last_probe_at else None

            if not probe_error_code and availability_status == 'offline':
                probe_error_code = 'DEVICE_NO_IP' if not device.ip_address else 'AGENT_UNREACHABLE'
            
            device_data = {
                'id': device.id,
                'device_name': device.device_name,
                'employee_name': device.employee_name,
                'mac_address': device.mac_address,
                'ip_address': device.ip_address,
                'status': availability_status,
                'availability_status': availability_status,
                'probe_error_code': probe_error_code,
                'probe_method': probe_method,
                'metrics_available': metrics_available,
                'last_probe_at': last_probe_at,
                'tracking_data': tracking_info,
            }
            summary_data.append(device_data)
        
        return jsonify({
            'success': True,
            'total_devices': len(devices),
            'online_devices': len([d for d in summary_data if d['status'] == 'online']),
            'degraded_devices': len([d for d in summary_data if d['status'] == 'degraded']),
            'devices': summary_data
        })
        
    except Exception as e:
        return _json_exception(
            'LIVE_SUMMARY_FAILED',
            'Failed to load live tracking summary.',
            e,
        )

@tracking_bp.route('/api/tracking/live-status/<mac_address>')
def api_live_status(mac_address):
    """Get simplified live status for a device directly from DB cache"""
    
    try:
        from extensions import redis_client
        
        device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
        if not device or not device.ip_address:
            return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)
        
        tracking_info = {}
        availability_status = 'offline'
        metrics_available = False
        probe_error_code = device.probe_error_code
        probe_method = device.probe_method
        last_probe_at = device.last_probe_at.isoformat() if device.last_probe_at else None
        is_from_redis = False
        
        # Redis Primary Try
        if redis_client:
            try:
                val = redis_client.get(f"tracking:probe:{mac_address}")
                if val:
                    payload = json.loads(val)
                    if isinstance(payload, dict):
                        candidate_tracking = payload.get('tracking_data')
                        if isinstance(candidate_tracking, dict):
                            tracking_info = candidate_tracking
                        elif any(key in payload for key in ('current_activity', 'today_stats', 'system_metrics')):
                            # Backward-compatible support for older payload shape
                            tracking_info = payload

                        status_from_cache = str(
                            payload.get('availability_status') or payload.get('status') or ''
                        ).strip().lower()
                        if status_from_cache in ('online', 'degraded', 'offline'):
                            availability_status = status_from_cache
                        elif tracking_info:
                            availability_status = 'online'

                        metrics_available = bool(
                            payload.get('metrics_available', False) or
                            tracking_info.get('system_metrics') or
                            tracking_info.get('today_stats') or
                            tracking_info.get('current_activity')
                        )
                        probe_error_code = payload.get('probe_error_code')
                        probe_method = payload.get('probe_method') or 'redis'
                        last_probe_at = payload.get('last_probe_at') or datetime.utcnow().isoformat()
                        is_from_redis = True
            except Exception:
                pass
                
        # DB Fallback
        if not is_from_redis:
            if device.tracking_data:
                try:
                    tracking_info = json.loads(device.tracking_data)
                except Exception:
                    pass
            availability_status = str(device.availability_status or 'offline').strip().lower()
            if availability_status not in ('online', 'degraded', 'offline'):
                availability_status = 'offline'
            metrics_available = bool(device.metrics_available)
            probe_error_code = device.probe_error_code
            probe_method = device.probe_method
            last_probe_at = device.last_probe_at.isoformat() if device.last_probe_at else None

        if not probe_error_code and availability_status == 'offline':
            probe_error_code = 'DEVICE_NO_IP' if not device.ip_address else 'AGENT_UNREACHABLE'

        return jsonify({
            'success': True,
            'status': availability_status,
            'availability_status': availability_status,
            'device_name': device.device_name,
            'activity': tracking_info.get('current_activity', {}),
            'resources': tracking_info.get('system_metrics', {}),
            'metrics_available': metrics_available,
            'probe': {
                'method': probe_method,
                'error_code': probe_error_code,
            },
            'timestamp': datetime.utcnow().isoformat(),
            'last_probe_at': last_probe_at,
        })
            
    except Exception as e:
        return _json_exception(
            'LIVE_STATUS_FAILED',
            'Failed to load live status.',
            e,
        )

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
    
    try:
        devices = TrackedDevice.query.all()
        all_alerts = []
        scanner = NetworkScanner()
        scanner.timeout = 2.5

        for device in devices:
            if device.ip_address:
                service_info = scanner.check_tracking_service(device.ip_address, profile='interactive')
                availability_status = service_info.get('availability_status', 'offline') if isinstance(service_info, dict) else 'offline'
                tracking_payload = service_info.get('data') if isinstance(service_info, dict) else {}

                if availability_status in ('online', 'degraded') and isinstance(tracking_payload, dict):
                    alerts = check_live_alerts(
                        tracking_payload,
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
        return _json_exception(
            'LIVE_ALERTS_FAILED',
            'Failed to load live alerts.',
            e,
        )

@tracking_bp.route('/api/tracking/maintenance/<mac_address>', methods=['POST'])
def api_toggle_device_maintenance(mac_address):
    """Toggle maintenance mode for a tracked device."""

    data = request.get_json(silent=True) or {}
    if 'enabled' not in data:
        return _json_error('MISSING_ENABLED_FLAG', 'Missing enabled flag', 400)

    enabled = data.get('enabled')
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ('true', '1', 'yes', 'on')
    else:
        enabled = bool(enabled)

    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device:
        return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)

    try:
        device.maintenance_mode = enabled
        device.updated_at = datetime.utcnow()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return _json_exception(
            'MAINTENANCE_UPDATE_FAILED',
            'Failed to update maintenance mode.',
            e,
        )

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
        return _json_exception(
            'PRODUCTIVITY_METRICS_FAILED',
            'Failed to calculate productivity metrics.',
            e,
        )

@tracking_bp.route('/api/tracking/metrics/security')
def api_security_metrics():
    """Get security risk metrics and unusual activity alerts."""

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
        return _json_exception(
            'SECURITY_METRICS_FAILED',
            'Failed to calculate security metrics.',
            e,
        )

@tracking_bp.route('/api/tracking/metrics/performance')
def api_performance_metrics():
    """Get performance metrics (CPU heatmap data)."""

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
        return _json_exception(
            'PERFORMANCE_METRICS_FAILED',
            'Failed to calculate performance metrics.',
            e,
        )

@tracking_bp.route('/api/tracking/metrics/details/<metric_type>')
def api_metric_details(metric_type):
    """Get detailed breakdown for a specific metric"""
        
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
        return _json_exception(
            'METRIC_DETAILS_FAILED',
            'Failed to load metric details.',
            e,
        )
