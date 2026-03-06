# enhanced_tracker_client.py
import os
import sys
import psutil
import platform
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, date
import json
import sqlite3
from collections import deque
from flask import Flask, Response, jsonify, request, g
from pynput import keyboard, mouse
import socket
import numpy as np
import cv2
from PIL import ImageGrab
import uuid
from werkzeug.utils import secure_filename
import shutil
import zipfile
import tempfile
from flask import send_file
import hashlib
import hmac
import base64
from cryptography.fernet import Fernet
import requests
from functools import wraps
import wmi
import uuid
import time
import logging
import ipaddress
import re
import subprocess
from urllib.parse import urlparse



camera_active = False
camera = None
camera_lock = threading.Lock()
typed_text_lock = threading.Lock()
activity_metrics_lock = threading.Lock()
maintenance_mode = False
discovery_service = None
restricted_site_monitor = None

app = Flask(__name__)
logging.basicConfig(
    filename="service.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# ============================================================
# VERSION VERIFICATION
# ============================================================
print("\n" + "="*60)
print("   TACTICAL AGENT SERVICE - VERSION 2.1 (WITH IDENTITY)   ")
print("="*60 + "\n")
logging.info("SERVICE STARTED: VERSION 2.1 (WITH IDENTITY)")

# ============================================================
# NEW IMPORTS (Client Modules)
# ============================================================
from client_modules.system_core import NetworkMonitor, SystemMonitor as CoreMonitor
from client_modules.system_context import WindowMonitor
from client_modules.system_processes import ProcessMonitor

# ============================================================
# CONFIGURATION GATING
# ============================================================
ENABLE_WINDOW_TITLES = True  # Enabled by default
ENABLE_NET_MONITOR = True    # Enabled by default
ENABLE_TOP_PROCESSES = True  # Enabled by default

# Initialization
core_monitor = CoreMonitor()
network_monitor = NetworkMonitor()
window_monitor = WindowMonitor()
process_monitor = ProcessMonitor()

# Global Thread-Safe Stats Cache
current_stats_lock = threading.Lock()
current_secure_stats = {
    "network": {"upload_speed_kbps": 0.0, "download_speed_kbps": 0.0},
    "core": {"cpu_percent": 0.0, "memory_percent": 0.0, "used_gb": 0.0, "total_gb": 0.0},
    "window": None,
    "top_processes": []
}
RESTRICTED_SOURCE_WINDOW = 'window_title'
RESTRICTED_SOURCE_DNS = 'dns_cache'
RESTRICTED_CONFIDENCE_HIGH = 'HIGH'
RESTRICTED_CONFIDENCE_LOW = 'LOW'
DEFAULT_RESTRICTED_POLICY = {
    'enabled': False,
    'blocked_domains': [],
    'cooldown_seconds': 900,
    'dns_poll_seconds': 60,
    'window_poll_seconds': 10,
    'dns_seen_ttl_seconds': 1800,
    'policy_version': '',
}
HOSTNAME_RE = re.compile(r'(?<!@)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\b', re.IGNORECASE)
# ============================================================
# MISSING IMPORTS AND HELPER FUNCTIONS
# ============================================================

def require_api_key(f):
    """API key authentication decorator"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
# ============================================================
# MISSING IMPORTS AND HELPER FUNCTIONS
# ============================================================

def require_api_key(f):
    """API key authentication decorator"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        # Check against hardcoded OR generated key
        if api_key == "8f42v73054r1749f8g58848be5e6502c" or api_key == API_KEY:
            return f(*args, **kwargs)
        return jsonify({"error": "Invalid API key"}), 401
    return decorated_function

_CACHED_MAC = None

def get_mac_address():
    """Get MAC address on Windows using WMI (Cached)"""
    global _CACHED_MAC
    if _CACHED_MAC:
        return _CACHED_MAC
        
    try:
        c = wmi.WMI()
        # Get first active network adapter with MAC
        for interface in c.Win32_NetworkAdapterConfiguration(IPEnabled=True):
            if interface.MACAddress:
                mac = interface.MACAddress.strip()
                if mac and mac != '00:00:00:00:00:00':
                    _CACHED_MAC = mac.upper()  # Return in uppercase for consistency
                    return _CACHED_MAC
    except:
        pass
    
    # Fallback to uuid method
    _CACHED_MAC = ':'.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff) 
                    for i in range(0, 48, 8)][::-1])
    return _CACHED_MAC

_CACHED_IP = None
_CACHED_IP_TIME = 0
_CACHED_IP_SIGNATURE = ()
_CACHED_IP_SOURCE = 'unknown'

def _collect_active_ipv4_candidates():
    candidates = []
    seen = set()
    iface_stats = psutil.net_if_stats()

    for interface, addrs in psutil.net_if_addrs().items():
        stats = iface_stats.get(interface)
        if stats and not stats.isup:
            continue

        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            ip = str(addr.address or '').strip()
            if not ip or ip.startswith('127.') or ip.startswith('169.254.'):
                continue
            if ip in seen:
                continue
            seen.add(ip)
            candidates.append(ip)

    return candidates


def _build_network_signature(candidates=None):
    values = candidates if isinstance(candidates, list) else _collect_active_ipv4_candidates()
    serialized = '|'.join(sorted(str(value).strip() for value in values if str(value).strip()))
    if not serialized:
        return ''
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()

def get_local_ip(force_refresh=False, target_host=None):
    """Get best local IPv4 and refresh on network changes."""
    global _CACHED_IP, _CACHED_IP_TIME, _CACHED_IP_SIGNATURE, _CACHED_IP_SOURCE
    now = time.time()
    current_candidates = _collect_active_ipv4_candidates()
    current_signature = tuple(sorted(current_candidates))

    if (
        not force_refresh
        and _CACHED_IP
        and _CACHED_IP_TIME
        and (now - _CACHED_IP_TIME < 300)
        and current_signature == _CACHED_IP_SIGNATURE
    ):
        return _CACHED_IP

    try:
        if target_host:
            # Prefer the source IP that would be used to reach the target admin host.
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.0)
            s.connect((str(target_host), 80))
            target_ip = s.getsockname()[0]
            s.close()
            if target_ip and not target_ip.startswith('127.') and not target_ip.startswith('169.254.'):
                _CACHED_IP = target_ip
                _CACHED_IP_TIME = now
                _CACHED_IP_SIGNATURE = current_signature
                _CACHED_IP_SOURCE = 'route_to_admin'
                return _CACHED_IP
    except Exception:
        pass

    try:
        preferred_prefix = (os.getenv('TRACKING_PREFERRED_SUBNET_PREFIX') or '172.16.2.').strip()
        if preferred_prefix:
            for ip in current_candidates:
                if ip.startswith(preferred_prefix):
                    _CACHED_IP = ip
                    _CACHED_IP_TIME = now
                    _CACHED_IP_SIGNATURE = current_signature
                    _CACHED_IP_SOURCE = 'preferred_subnet'
                    return _CACHED_IP

        for ip in current_candidates:
            try:
                ip_obj = ipaddress.ip_address(ip)
            except Exception:
                continue
            if ip_obj.is_private and not ip_obj.is_loopback and not ip_obj.is_link_local:
                _CACHED_IP = ip
                _CACHED_IP_TIME = now
                _CACHED_IP_SIGNATURE = current_signature
                _CACHED_IP_SOURCE = 'private_interface'
                return _CACHED_IP

        if current_candidates:
            _CACHED_IP = current_candidates[0]
            _CACHED_IP_TIME = now
            _CACHED_IP_SIGNATURE = current_signature
            _CACHED_IP_SOURCE = 'candidate_fallback'
            return _CACHED_IP

        # Fallback to default route probing
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        _CACHED_IP = ip
        _CACHED_IP_TIME = now
        _CACHED_IP_SIGNATURE = current_signature
        _CACHED_IP_SOURCE = 'default_route'
        return _CACHED_IP
    except Exception:
        _CACHED_IP = "127.0.0.1"
        _CACHED_IP_TIME = now
        _CACHED_IP_SIGNATURE = current_signature
        _CACHED_IP_SOURCE = 'loopback_fallback'
        return _CACHED_IP


def get_local_ip_details(force_refresh=False, target_host=None):
    candidates = _collect_active_ipv4_candidates()
    ip_address = get_local_ip(force_refresh=force_refresh, target_host=target_host)
    return {
        'ip': ip_address,
        'source': _CACHED_IP_SOURCE,
        'candidates': candidates,
        'network_signature': _build_network_signature(candidates),
    }

_CACHED_HOSTNAME = None

def get_exact_hostname():
    """
    Returns the most accurate hostname across platforms with fallback mechanism (Cached):
    - Windows: FQDN if domain joined, otherwise computer name (via WMI)
    - Linux/macOS: FQDN from socket.getfqdn() or hostname command
    - Universal fallback: socket.gethostname()
    """
    global _CACHED_HOSTNAME
    if _CACHED_HOSTNAME:
        return _CACHED_HOSTNAME
        
    system = platform.system()
    
    try:
        if system == 'Windows':
            # Windows-specific WMI approach for domain-joined machines
            try:
                c = wmi.WMI()
                comp_system = c.Win32_ComputerSystem()[0]
                hostname = comp_system.Name
                
                # If domain joined, build FQDN
                if comp_system.PartOfDomain and comp_system.Domain:
                    fqdn = f"{hostname}.{comp_system.Domain}".lower()
                    print(f"[OK] Detected domain-joined hostname: {fqdn}")
                    _CACHED_HOSTNAME = fqdn
                    return _CACHED_HOSTNAME
                
                # Not domain joined, return computer name
                print(f"[OK] Detected standalone hostname: {hostname.lower()}")
                _CACHED_HOSTNAME = hostname.lower()
                return _CACHED_HOSTNAME
                
            except Exception as wmi_error:
                print(f"[WARN] WMI hostname detection failed: {wmi_error}, using fallback")
                # Fallback to socket for Windows
                return socket.gethostname().lower()
            
        elif system in ['Linux', 'Darwin']:  # Darwin = macOS
            # Try socket.getfqdn() first (reads /etc/hosts and DNS)
            try:
                fqdn = socket.getfqdn()
                
                # Validate FQDN (should contain domain and not be localhost)
                if '.' in fqdn and not fqdn.startswith('localhost'):
                    print(f"[OK] Detected FQDN: {fqdn}")
                    _CACHED_HOSTNAME = fqdn.lower()
                    return _CACHED_HOSTNAME
                
                # FQDN not valid, try short hostname
                hostname = socket.gethostname()
                print(f"[OK] Detected hostname: {hostname}")
                _CACHED_HOSTNAME = hostname.lower()
                return _CACHED_HOSTNAME
                
            except Exception as socket_error:
                print(f"[WARN] Socket hostname detection failed: {socket_error}")
                
                # Linux-specific fallback: read /etc/hostname
                if system == 'Linux':
                    try:
                        with open('/etc/hostname', 'r') as f:
                            hostname = f.read().strip()
                            if hostname:
                                print(f"[OK] Read hostname from /etc/hostname: {hostname}")
                                _CACHED_HOSTNAME = hostname.lower()
                                return _CACHED_HOSTNAME
                    except:
                        pass
                
                # Try environment variables
                for env_var in ['HOSTNAME', 'HOST', 'COMPUTERNAME']:
                    hostname = os.environ.get(env_var)
                    if hostname:
                        print(f"[OK] Got hostname from {env_var}: {hostname}")
                        _CACHED_HOSTNAME = hostname.lower()
                        return _CACHED_HOSTNAME
                
                raise  # Re-raise to trigger universal fallback
        
        else:
            # Unknown platform, use socket method
            print(f"[WARN] Unknown platform '{system}', using socket fallback")
            _CACHED_HOSTNAME = socket.gethostname().lower()
            return _CACHED_HOSTNAME
            
    except Exception as e:
        # Universal fallback for all errors
        print(f"[WARN] All hostname detection methods failed: {e}")
        try:
            fallback = socket.gethostname().lower()
            print(f"-> Using universal fallback: {fallback}")
            _CACHED_HOSTNAME = fallback
            return _CACHED_HOSTNAME
        except:
            # Absolute last resort
            print("-> Using hardcoded fallback: 'unknown-host'")
            _CACHED_HOSTNAME = "unknown-host"
            return _CACHED_HOSTNAME


def get_system_info():
    """Get comprehensive system information"""
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "architecture": platform.architecture(),
        "processor": platform.processor(),
        "hostname": get_exact_hostname(),
        "cpu_cores": psutil.cpu_count(),
        "total_memory": round(psutil.virtual_memory().total / (1024**3), 2),
        "boot_time": datetime.fromtimestamp(psutil.boot_time()).isoformat(),
    }

def register_or_update_employee():
    """Register or update employee device in database"""
    try:
        mac = get_mac_address()
        ip = get_local_ip()
        hostname = get_exact_hostname()
        system_info = get_system_info()
        
        # Encrypt system info
        encrypted_system_info = encrypt_data(json.dumps(system_info))
        
        with db_lock:
            cursor = db_conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO employee_details 
                (employee_name, mac_address, ip_address, hostname, system_info_encrypted, last_seen, is_active)
                VALUES (?, ?, ?, ?, ?, datetime('now'), 1)
            ''', (f"Employee_{hostname}", mac, ip, hostname, encrypted_system_info))
            
            db_conn.commit()
        print(f"[OK] Device registered: {hostname} ({mac})")
        
    except Exception as e:
        print(f"Device registration error: {e}")




def verify_admin_key(admin_key):
    """Verify admin server key (simplified - implement proper verification)"""
    expected_key = hashlib.sha256(f"admin_{get_mac_address()}".encode()).hexdigest()
    return admin_key == expected_key

def build_live_stats_payload():
    """Build live statistics payload for sync/background usage."""
    current_time = time.time()
    activity_snapshot = get_activity_snapshot(current_time)
    
    with current_stats_lock:
        core_stats = current_secure_stats.get('core', {})
        cpu = core_stats.get('cpu_percent', 0.0) if core_stats else 0.0
        memory = core_stats.get('memory_percent', 0.0) if core_stats else 0.0
        net_stats = current_secure_stats.get('network', {})

    return {
        "timestamp": datetime.now().isoformat(),
        "activity": {
            "keyboard_active": activity_snapshot['keyboard_active'],
            "mouse_active": activity_snapshot['mouse_active'],
            "idle_seconds": round(activity_snapshot['idle_seconds'], 2),
            "total_active_today": activity_snapshot['total_duration']
        },
        "system": {
            "cpu": cpu,
            "memory": memory,
            "current_app": system_monitor.current_app
        },
        "network": net_stats
    }

def get_live_stats():
    """Get live statistics as Flask response."""
    return jsonify(build_live_stats_payload())

# ============================================================
# ENHANCED SECURITY - Generate keys securely
# ============================================================

def generate_secure_keys():
    """Generate secure API keys and encryption"""
    machine_id = str(uuid.getnode())
    secret_seed = f"tracker_{machine_id}_{socket.gethostname()}"
    
    # Generate API key from machine fingerprint
    api_key = hashlib.sha256(secret_seed.encode()).hexdigest()
    
    # Generate encryption key
    encryption_key = base64.urlsafe_b64encode(hashlib.sha256(secret_seed.encode()).digest())
    
    return api_key, encryption_key

API_KEY, ENCRYPTION_KEY = generate_secure_keys()
cipher_suite = Fernet(ENCRYPTION_KEY)

# Global variables with encryption
keyboard_events = []
mouse_events = []
typed_text = deque(maxlen=10000)  # Increased buffer

# Activity tracking with enhanced metrics
activity_metrics = {
    'keyboard': {'last_activity': time.time(), 'duration': 0, 'count': 0},
    'mouse': {'last_activity': time.time(), 'duration': 0, 'count': 0},
    'system': {'last_activity': time.time(), 'total_duration': 0},
    'screen_on_time': 0
}

INACTIVITY_THRESHOLD = 5
SNAPSHOT_INTERVAL_SECONDS = 300
MOUSE_MOVE_MIN_DISTANCE = 2
MOUSE_ACTIVITY_INTERVAL_SECONDS = 0.2
MOUSE_COUNT_INTERVAL_SECONDS = 1.0

last_snapshot_flush_at = time.time()
_last_mouse_update = 0.0
_last_mouse_count_update = 0.0
_last_mouse_position = None

if getattr(sys, 'frozen', False):
    # If frozen, save DB next to the executable
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(BASE_DIR, 'secure_employee_monitor.db')
AGENT_AUTH_PATH = os.path.join(BASE_DIR, 'agent_auth.json')

_agent_auth_lock = threading.Lock()
_agent_auth_cache = None


def load_agent_auth():
    global _agent_auth_cache
    with _agent_auth_lock:
        if _agent_auth_cache is not None:
            return dict(_agent_auth_cache)
        try:
            if os.path.exists(AGENT_AUTH_PATH):
                with open(AGENT_AUTH_PATH, 'r', encoding='utf-8') as auth_file:
                    data = json.load(auth_file)
                    if isinstance(data, dict):
                        key_id = str(data.get('key_id') or '').strip()
                        key_secret = str(data.get('agent_key') or '').strip()
                        if key_id and key_secret:
                            _agent_auth_cache = {'key_id': key_id, 'agent_key': key_secret}
                            return dict(_agent_auth_cache)
        except Exception as exc:
            print(f"[AgentAuth] Failed to load agent auth file: {exc}")
        _agent_auth_cache = {}
        return {}


def save_agent_auth(key_id, agent_key):
    global _agent_auth_cache
    record = {'key_id': str(key_id or '').strip(), 'agent_key': str(agent_key or '').strip()}
    if not record['key_id'] or not record['agent_key']:
        return False
    with _agent_auth_lock:
        try:
            with open(AGENT_AUTH_PATH, 'w', encoding='utf-8') as auth_file:
                json.dump(record, auth_file, ensure_ascii=True)
            if platform.system() == "Windows":
                try:
                    import ctypes
                    FILE_ATTRIBUTE_HIDDEN = 0x02
                    ctypes.windll.kernel32.SetFileAttributesW(AGENT_AUTH_PATH, FILE_ATTRIBUTE_HIDDEN)
                except Exception:
                    pass
            _agent_auth_cache = record
            return True
        except Exception as exc:
            print(f"[AgentAuth] Failed to save agent auth file: {exc}")
            return False

# ============================================================
# ENHANCED DATABASE WITH ENCRYPTION & WAL
# ============================================================

db_lock = threading.Lock()
db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
db_conn.execute("PRAGMA journal_mode=WAL;")
db_conn.execute("PRAGMA synchronous=NORMAL;")
db_conn.execute("PRAGMA temp_store=MEMORY;")
db_conn.execute("PRAGMA cache_size=-20000;")  # ~20MB cache
db_conn.commit()

def init_secure_database():
    """Initialize encrypted database"""
    with db_lock:
        cursor = db_conn.cursor()
        
        # Employee details with encryption
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS employee_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_name TEXT,
                mac_address TEXT UNIQUE,
                ip_address TEXT,
                hostname TEXT,
                system_info_encrypted TEXT,
                first_installed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            )
        ''')
        
        # Enhanced daily summary
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                mac_address TEXT,
                total_active_seconds REAL,
                keyboard_active_seconds REAL,
                mouse_active_seconds REAL,
                screen_on_seconds REAL,
                keyboard_events_count INTEGER,
                mouse_events_count INTEGER,
                typed_characters_count INTEGER,
                sessions_count INTEGER,
                applications_used TEXT,
                first_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                avg_cpu_usage REAL,
                avg_memory_usage REAL,
                max_cpu_usage REAL,
                max_memory_usage REAL
            )
        ''')
        
        # Enhanced hourly breakdown
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS hourly_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                mac_address TEXT,
                hour INTEGER,
                keyboard_events INTEGER DEFAULT 0,
                mouse_events INTEGER DEFAULT 0,
                active_seconds INTEGER DEFAULT 0,
                cpu_avg REAL,
                memory_avg REAL,
                UNIQUE(date, mac_address, hour)
            )
        ''')
        
        # Add indexes for frequent queries
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_daily_date_mac ON daily_summary (date, mac_address);')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_hourly_date_mac ON hourly_activity (date, mac_address);')
        
        # Application usage tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS application_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                mac_address TEXT,
                application_name TEXT,
                usage_seconds INTEGER,
                window_title TEXT,
                start_time TIMESTAMP,
                end_time TIMESTAMP
            )
        ''')
        
        # Encrypted typed text
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS encrypted_typed_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                mac_address TEXT,
                encrypted_text TEXT,
                character_count INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # System alerts and events
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                mac_address TEXT,
                alert_type TEXT,
                alert_message TEXT,
                severity TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS restricted_site_event_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                matched_rule TEXT NOT NULL,
                source TEXT NOT NULL,
                confidence TEXT NOT NULL,
                process_name TEXT,
                raw_evidence TEXT,
                observed_at TIMESTAMP NOT NULL,
                policy_version TEXT,
                retry_count INTEGER DEFAULT 0,
                sent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_restricted_site_queue_sent ON restricted_site_event_queue (sent_at, id);')

        db_conn.commit()
    print("[OK] Secure database initialized")

def encrypt_data(data):
    """Encrypt sensitive data"""
    if isinstance(data, str):
        data = data.encode()
    return cipher_suite.encrypt(data)

def decrypt_data(encrypted_data):
    """Decrypt sensitive data"""
    try:
        return cipher_suite.decrypt(encrypted_data).decode()
    except:
        return "[Encrypted Data]"

# ============================================================
# ENHANCED SYSTEM MONITORING
# ============================================================

class SystemMonitor:
    def __init__(self):
        self.current_app = None
        self.app_start_time = None
        self.app_usage = {}
    
    def get_active_application(self):
        """Get currently active application (Windows)"""
        try:
            import win32gui
            import win32process
            
            window = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(window)
            process = psutil.Process(pid)
            return process.name()
        except:
            return "Unknown"
    
    def track_application_usage(self):
        """Track application usage"""
        current_app = self.get_active_application()
        current_time = time.time()
        
        if current_app != self.current_app:
            # Save previous app usage
            if self.current_app and self.app_start_time:
                usage_time = current_time - self.app_start_time
                if self.current_app in self.app_usage:
                    self.app_usage[self.current_app] += usage_time
                else:
                    self.app_usage[self.current_app] = usage_time
            
            # Start tracking new app
            self.current_app = current_app
            self.app_start_time = current_time
    
    def save_application_usage(self):
        """Save application usage to database"""
        if self.app_usage:
            try:
                today = date.today().isoformat()
                mac = get_mac_address()
                
                with db_lock:
                    cursor = db_conn.cursor()
                    for app_name, usage_seconds in self.app_usage.items():
                        cursor.execute('''
                            INSERT INTO application_usage 
                            (date, mac_address, application_name, usage_seconds, start_time, end_time)
                            VALUES (?, ?, ?, ?, datetime('now', '-' || ? || ' seconds'), datetime('now'))
                        ''', (today, mac, app_name, usage_seconds, usage_seconds))
                    
                    db_conn.commit()
                self.app_usage.clear()
                
            except Exception as e:
                print(f"Application usage save error: {e}")

system_monitor = SystemMonitor()

# ============================================================
# ENHANCED ACTIVITY TRACKING
# ============================================================

def get_typed_text_length():
    with typed_text_lock:
        return len(typed_text)

def get_activity_snapshot(current_time=None):
    now = current_time if current_time is not None else time.time()
    with activity_metrics_lock:
        keyboard_last = activity_metrics['keyboard']['last_activity']
        mouse_last = activity_metrics['mouse']['last_activity']
        system_last = activity_metrics['system']['last_activity']
        snapshot = {
            'keyboard_last_activity': keyboard_last,
            'mouse_last_activity': mouse_last,
            'system_last_activity': system_last,
            'keyboard_duration': activity_metrics['keyboard']['duration'],
            'mouse_duration': activity_metrics['mouse']['duration'],
            'total_duration': activity_metrics['system']['total_duration'],
            'keyboard_count': activity_metrics['keyboard']['count'],
            'mouse_count': activity_metrics['mouse']['count'],
        }
    snapshot['keyboard_active'] = (now - keyboard_last) < INACTIVITY_THRESHOLD
    snapshot['mouse_active'] = (now - mouse_last) < INACTIVITY_THRESHOLD
    snapshot['idle_seconds'] = max(0.0, now - system_last)
    return snapshot

def update_enhanced_activity_times(delta=1.0):
    """Enhanced activity tracking with application monitoring"""
    global activity_metrics, last_snapshot_flush_at
    
    current_time = time.time()
    with activity_metrics_lock:
        keyboard_active = (current_time - activity_metrics['keyboard']['last_activity']) < INACTIVITY_THRESHOLD
        mouse_active = (current_time - activity_metrics['mouse']['last_activity']) < INACTIVITY_THRESHOLD

        if keyboard_active:
            activity_metrics['keyboard']['duration'] += delta
        if mouse_active:
            activity_metrics['mouse']['duration'] += delta
        if keyboard_active or mouse_active:
            activity_metrics['system']['total_duration'] += delta
            activity_metrics['system']['last_activity'] = current_time
    
    # Track application usage
    system_monitor.track_application_usage()
    
    # Flush snapshots on a deterministic interval.
    if current_time - last_snapshot_flush_at >= SNAPSHOT_INTERVAL_SECONDS:
        last_snapshot_flush_at = current_time
        save_enhanced_activity_snapshot()
        save_daily_summary_enhanced()
        system_monitor.save_application_usage()
        save_encrypted_typed_text()

def save_enhanced_activity_snapshot():
    """Save enhanced activity snapshot"""
    try:
        snapshot = get_activity_snapshot()
        
        with current_stats_lock:
            core_stats = current_secure_stats.get('core', {})
            cpu = core_stats.get('cpu_percent', 0.0) if core_stats else 0.0
            memory = core_stats.get('memory_percent', 0.0) if core_stats else 0.0
            
        current_hour = datetime.now().hour
        today = date.today().isoformat()
        mac = get_mac_address()
        
        with db_lock:
            cursor = db_conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO hourly_activity 
                (date, mac_address, hour, keyboard_events, mouse_events, active_seconds, cpu_avg, memory_avg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (today, mac, current_hour, 
                  snapshot['keyboard_count'], 
                  snapshot['mouse_count'],
                  1, cpu, memory))
            
            db_conn.commit()
        
    except Exception as e:
        print(f"Enhanced activity snapshot error: {e}")

def save_daily_summary_enhanced():
    """Enhanced daily summary with more metrics"""
    try:
        today = date.today().isoformat()
        mac = get_mac_address()
        snapshot = get_activity_snapshot()
        typed_characters_count = get_typed_text_length()
        
        with current_stats_lock:
            core_stats = current_secure_stats.get('core', {})
            cpu = core_stats.get('cpu_percent', 0.0) if core_stats else 0.0
            memory = core_stats.get('memory_percent', 0.0) if core_stats else 0.0
        
        apps_used = json.dumps(list(system_monitor.app_usage.keys()))
        
        with db_lock:
            cursor = db_conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO daily_summary 
                (date, mac_address, total_active_seconds, keyboard_active_seconds, 
                 mouse_active_seconds, screen_on_seconds, keyboard_events_count, 
                 mouse_events_count, typed_characters_count, sessions_count,
                 applications_used, first_activity, last_activity, 
                 avg_cpu_usage, avg_memory_usage, max_cpu_usage, max_memory_usage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 
                        datetime('now'), datetime('now'), ?, ?, ?, ?)
            ''', (today, mac, 
                  snapshot['total_duration'],
                  snapshot['keyboard_duration'],
                  snapshot['mouse_duration'],
                  snapshot['total_duration'],
                  snapshot['keyboard_count'],
                  snapshot['mouse_count'],
                  typed_characters_count,
                  apps_used,
                  cpu, memory, cpu, memory))
            
            db_conn.commit()
        
    except Exception as e:
        print(f"Enhanced daily summary error: {e}")

def load_daily_stats():
    """Load daily stats from database"""
    try:
        today = date.today().isoformat()
        mac = get_mac_address()
        
        with db_lock:
            cursor = db_conn.cursor()
            cursor.execute('''
                SELECT total_active_seconds, keyboard_active_seconds, mouse_active_seconds,
                       keyboard_events_count, mouse_events_count
                FROM daily_summary 
                WHERE date = ? AND mac_address = ?
            ''', (today, mac))
            
            row = cursor.fetchone()
            
        if row:
            with activity_metrics_lock:
                activity_metrics['system']['total_duration'] = row[0]
                activity_metrics['keyboard']['duration'] = row[1]
                activity_metrics['mouse']['duration'] = row[2]
                activity_metrics['keyboard']['count'] = row[3]
                activity_metrics['mouse']['count'] = row[4]
            print(f"[OK] Loaded daily stats: {round(row[0]/60, 1)} min active")
            
    except Exception as e:
        print(f"Error loading daily stats: {e}")

def save_encrypted_typed_text():
    """Save encrypted typed text"""
    with typed_text_lock:
        if len(typed_text) == 0:
            return
        text_chunk = ''.join(list(typed_text))
        typed_text.clear()

    try:
        encrypted_text = encrypt_data(text_chunk)
        today = date.today().isoformat()
        mac = get_mac_address()
        
        with db_lock:
            cursor = db_conn.cursor()
            cursor.execute('''
                INSERT INTO encrypted_typed_data 
                (date, mac_address, encrypted_text, character_count)
                VALUES (?, ?, ?, ?)
            ''', (today, mac, encrypted_text, len(text_chunk)))
            
            db_conn.commit()
            
    except Exception as e:
        print(f"Encrypted text save error: {e}")

# ============================================================
# ENHANCED INPUT HANDLERS
# ============================================================

def on_key_press_enhanced(key):
    """Enhanced keyboard handler"""
    now = time.time()
    with activity_metrics_lock:
        activity_metrics['keyboard']['last_activity'] = now
        activity_metrics['keyboard']['count'] += 1
        activity_metrics['system']['last_activity'] = now
    
    try:
        char = key.char
        if char:
            with typed_text_lock:
                typed_text.append(char)
    except AttributeError:
        with typed_text_lock:
            if key == keyboard.Key.space:
                typed_text.append(' ')
            elif key == keyboard.Key.enter:
                typed_text.append('\n')
            elif key == keyboard.Key.backspace and len(typed_text) > 0:
                typed_text.pop()

def on_click_enhanced(x, y, button, pressed):
    """Enhanced mouse handler"""
    if pressed:
        now = time.time()
        with activity_metrics_lock:
            activity_metrics['mouse']['last_activity'] = now
            activity_metrics['mouse']['count'] += 1
            activity_metrics['system']['last_activity'] = now

def on_move_enhanced(x, y):
    """Enhanced mouse movement"""
    global _last_mouse_update, _last_mouse_count_update, _last_mouse_position
    now = time.time()

    with activity_metrics_lock:
        if _last_mouse_position is not None:
            delta_x = abs(x - _last_mouse_position[0])
            delta_y = abs(y - _last_mouse_position[1])
            if delta_x < MOUSE_MOVE_MIN_DISTANCE and delta_y < MOUSE_MOVE_MIN_DISTANCE:
                return

        if now - _last_mouse_update < MOUSE_ACTIVITY_INTERVAL_SECONDS:
            return

        _last_mouse_update = now
        _last_mouse_position = (x, y)
        activity_metrics['mouse']['last_activity'] = now
        activity_metrics['system']['last_activity'] = now

        if now - _last_mouse_count_update >= MOUSE_COUNT_INTERVAL_SECONDS:
            activity_metrics['mouse']['count'] += 1
            _last_mouse_count_update = now


class RestrictedSiteMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._policy = dict(DEFAULT_RESTRICTED_POLICY)
        self._policy_version = ''
        self._dns_snapshot = set()
        self._dns_seen_timestamps = {}
        self._local_cooldown_timestamps = {}
        self._browser_processes = {
            'chrome.exe',
            'msedge.exe',
            'firefox.exe',
            'opera.exe',
            'brave.exe',
        }
        raw_suffixes = os.getenv('RESTRICTED_SITE_IGNORE_SUFFIXES', '.local,.lan,.internal')
        self._ignored_suffixes = tuple(
            token.strip().lower()
            for token in str(raw_suffixes).split(',')
            if token.strip()
        ) + ('wpad',)

    def _normalize_domain(self, value):
        if value is None:
            return None
        text = str(value).strip().lower()
        if not text:
            return None
        if '://' in text:
            try:
                parsed = urlparse(text)
                text = parsed.hostname or ''
            except Exception:
                return None
        else:
            text = text.split('/', 1)[0].split('?', 1)[0]
            if ':' in text:
                text = text.split(':', 1)[0]
        text = text.strip().strip('.')
        if text.startswith('*.'):
            text = text[2:]
        if text.startswith('www.'):
            text = text[4:]
        text = text.strip().strip('.')
        if not text or '.' not in text:
            return None
        if '*' in text:
            return None
        try:
            ipaddress.ip_address(text)
            return None
        except Exception:
            pass
        labels = [label for label in text.split('.') if label]
        if len(labels) < 2 or any(len(label) > 63 for label in labels):
            return None
        allowed = set('abcdefghijklmnopqrstuvwxyz0123456789-')
        for label in labels:
            if label.startswith('-') or label.endswith('-'):
                return None
            if any(ch not in allowed for ch in label):
                return None
        return '.'.join(labels)

    def _domain_candidates_from_text(self, text):
        if not text:
            return []
        values = []
        for candidate in HOSTNAME_RE.findall(str(text).lower()):
            normalized = self._normalize_domain(candidate)
            if normalized:
                values.append(normalized)
        return sorted(set(values))

    def _is_ignored_domain(self, domain):
        if not domain:
            return True
        lowered = domain.lower()
        if lowered in ('wpad',):
            return True
        for suffix in self._ignored_suffixes:
            if lowered == suffix or lowered.endswith(suffix):
                return True
        return False

    def _match_blocked_domain(self, domain):
        blocked = self._policy.get('blocked_domains') or []
        for rule in blocked:
            if domain == rule or domain.endswith(f".{rule}"):
                return rule
        return None

    def apply_policy(self, policy_payload):
        payload = policy_payload if isinstance(policy_payload, dict) else {}
        with self._lock:
            blocked_domains = sorted(
                {
                    domain
                    for domain in (
                        self._normalize_domain(item)
                        for item in (payload.get('blocked_domains') or [])
                    )
                    if domain and not self._is_ignored_domain(domain)
                }
            )
            self._policy = {
                'enabled': bool(payload.get('enabled', False)),
                'blocked_domains': blocked_domains,
                'cooldown_seconds': max(60, int(payload.get('cooldown_seconds', 900) or 900)),
                'dns_poll_seconds': max(15, int(payload.get('dns_poll_seconds', 60) or 60)),
                'window_poll_seconds': max(5, int(payload.get('window_poll_seconds', 10) or 10)),
                'dns_seen_ttl_seconds': max(60, int(payload.get('dns_seen_ttl_seconds', 1800) or 1800)),
                'policy_version': str(payload.get('policy_version') or ''),
            }
            self._policy_version = self._policy.get('policy_version') or self._policy_version

    def get_policy_version(self):
        with self._lock:
            return self._policy_version or self._policy.get('policy_version') or ''

    def get_dns_poll_seconds(self):
        with self._lock:
            return int(self._policy.get('dns_poll_seconds') or 60)

    def get_window_poll_seconds(self):
        with self._lock:
            return int(self._policy.get('window_poll_seconds') or 10)

    def is_enabled(self):
        with self._lock:
            return bool(self._policy.get('enabled')) and bool(self._policy.get('blocked_domains'))

    def _should_emit_local(self, domain, now_ts):
        with self._lock:
            cooldown = int(self._policy.get('cooldown_seconds') or 900)
            last_seen = self._local_cooldown_timestamps.get(domain)
            if last_seen and (now_ts - last_seen) < cooldown:
                return False
            self._local_cooldown_timestamps[domain] = now_ts
            return True

    def _enqueue_event(self, domain, matched_rule, source, confidence, process_name=None, raw_evidence=None, observed_at=None):
        observed_dt = observed_at if isinstance(observed_at, datetime) else datetime.utcnow()
        policy_version = self.get_policy_version()
        with db_lock:
            cursor = db_conn.cursor()
            cursor.execute(
                '''
                INSERT INTO restricted_site_event_queue
                (domain, matched_rule, source, confidence, process_name, raw_evidence, observed_at, policy_version, retry_count, sent_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
                ''',
                (
                    domain,
                    matched_rule,
                    source,
                    confidence,
                    process_name,
                    (raw_evidence or '')[:500],
                    observed_dt.isoformat(),
                    policy_version,
                ),
            )
            db_conn.commit()

    def handle_window_event(self, window_payload):
        if not self.is_enabled() or not isinstance(window_payload, dict):
            return 0
        process_name = str(window_payload.get('app_name') or '').strip().lower()
        title = str(window_payload.get('title') or '').strip()
        if not process_name or process_name not in self._browser_processes:
            return 0
        if not title:
            return 0

        now_ts = time.time()
        emitted = 0
        for domain in self._domain_candidates_from_text(title):
            if self._is_ignored_domain(domain):
                continue
            matched_rule = self._match_blocked_domain(domain)
            if not matched_rule:
                continue
            if not self._should_emit_local(domain, now_ts):
                continue
            self._enqueue_event(
                domain=domain,
                matched_rule=matched_rule,
                source=RESTRICTED_SOURCE_WINDOW,
                confidence=RESTRICTED_CONFIDENCE_HIGH,
                process_name=process_name,
                raw_evidence=title,
                observed_at=datetime.utcnow(),
            )
            emitted += 1
        return emitted

    def poll_dns_cache(self):
        if not self.is_enabled():
            return 0

        try:
            result = subprocess.run(
                ["ipconfig", "/displaydns"],
                capture_output=True,
                text=True,
                timeout=8,
            )
        except Exception:
            return 0

        output = result.stdout or ''
        if not output:
            return 0

        current_domains = {
            domain
            for domain in (self._normalize_domain(token) for token in HOSTNAME_RE.findall(output.lower()))
            if domain and not self._is_ignored_domain(domain)
        }

        with self._lock:
            new_domains = current_domains - self._dns_snapshot
            self._dns_snapshot = current_domains
            dns_seen_ttl = int(self._policy.get('dns_seen_ttl_seconds') or 1800)

        now_ts = time.time()
        emitted = 0
        for domain in sorted(new_domains):
            with self._lock:
                last_seen_dns = self._dns_seen_timestamps.get(domain)
                if last_seen_dns and (now_ts - last_seen_dns) < dns_seen_ttl:
                    continue
                self._dns_seen_timestamps[domain] = now_ts

            matched_rule = self._match_blocked_domain(domain)
            if not matched_rule:
                continue
            if not self._should_emit_local(domain, now_ts):
                continue
            self._enqueue_event(
                domain=domain,
                matched_rule=matched_rule,
                source=RESTRICTED_SOURCE_DNS,
                confidence=RESTRICTED_CONFIDENCE_LOW,
                process_name=None,
                raw_evidence=f"dns:{domain}",
                observed_at=datetime.utcnow(),
            )
            emitted += 1

        with self._lock:
            stale_before = now_ts - max(dns_seen_ttl, 60)
            self._dns_seen_timestamps = {
                key: value for key, value in self._dns_seen_timestamps.items() if value >= stale_before
            }
        return emitted

    def get_pending_events(self, limit=50):
        with db_lock:
            cursor = db_conn.cursor()
            cursor.execute(
                '''
                SELECT id, domain, matched_rule, source, confidence, process_name, raw_evidence, observed_at
                FROM restricted_site_event_queue
                WHERE sent_at IS NULL
                ORDER BY id ASC
                LIMIT ?
                ''',
                (max(1, int(limit)),),
            )
            rows = cursor.fetchall()

        events = []
        for row in rows:
            event_id, domain, matched_rule, source, confidence, process_name, raw_evidence, observed_at = row
            events.append(
                {
                    'id': int(event_id),
                    'event': {
                        'domain': domain,
                        'matched_rule': matched_rule,
                        'source': source,
                        'confidence': confidence,
                        'process_name': process_name,
                        'raw_evidence': raw_evidence,
                        'observed_at_utc': observed_at,
                    },
                }
            )
        return events

    def mark_events_sent(self, event_ids):
        ids = [int(item) for item in event_ids if item is not None]
        if not ids:
            return
        placeholders = ','.join('?' for _ in ids)
        now_value = datetime.utcnow().isoformat()
        with db_lock:
            cursor = db_conn.cursor()
            cursor.execute(
                f"UPDATE restricted_site_event_queue SET sent_at = ? WHERE id IN ({placeholders})",
                [now_value, *ids],
            )
            db_conn.commit()

    def mark_events_failed(self, event_ids):
        ids = [int(item) for item in event_ids if item is not None]
        if not ids:
            return
        placeholders = ','.join('?' for _ in ids)
        with db_lock:
            cursor = db_conn.cursor()
            cursor.execute(
                f"UPDATE restricted_site_event_queue SET retry_count = COALESCE(retry_count, 0) + 1 WHERE id IN ({placeholders})",
                ids,
            )
            db_conn.commit()

# ============================================================
# AUTO-DISCOVERY AND SYNC SERVICE
# ============================================================

class AutoDiscoveryService:
    def __init__(self):
        self.admin_servers = []
        self.sync_interval = max(15, int(os.getenv('ADMIN_SYNC_INTERVAL_SECONDS', '60') or '60'))
        self.admin_server_url = self._normalize_admin_url(os.getenv('ADMIN_SERVER_URL'))
        self.admin_ports = self._parse_admin_ports(os.getenv('ADMIN_SERVER_PORTS', '5001,5000'))
        self.discovery_subnet = (os.getenv('ADMIN_DISCOVERY_SUBNET') or '').strip() or None
        self.shared_api_key = (
            os.getenv('TRACKING_API_KEY')
            or os.getenv('ADMIN_SERVER_API_KEY')
            or "8f42v73054r1749f8g58848be5e6502c"
        ).strip()

        discovery_enabled_raw = os.getenv('ADMIN_DISCOVERY_ENABLED')
        if discovery_enabled_raw is None:
            self.discovery_enabled = not bool(self.admin_server_url)
        else:
            self.discovery_enabled = discovery_enabled_raw.strip().lower() in ('1', 'true', 'yes', 'on')

        self.last_discovery_reason = 'not_started'
        self._last_no_server_log = 0
        self.no_server_log_interval = int(os.getenv('ADMIN_DISCOVERY_LOG_INTERVAL_SECONDS', '300') or '300')
        self.last_admin_server = None
        self.policy_refresh_interval = int(os.getenv('RESTRICTED_POLICY_REFRESH_SECONDS', '300') or '300')
        self.last_policy_refresh_at = 0
        self.network_change_debounce_seconds = int(os.getenv('NETWORK_CHANGE_SYNC_DEBOUNCE_SECONDS', '15') or '15')
        self.last_network_signature = _build_network_signature()
        self.last_network_change_sync_at = 0

    def _normalize_admin_url(self, raw_url):
        if not raw_url:
            return None
        value = str(raw_url).strip()
        if not value:
            return None
        if '://' not in value:
            value = f"http://{value}"
        parsed = urlparse(value)
        if not parsed.netloc:
            return None
        scheme = parsed.scheme or 'http'
        return f"{scheme}://{parsed.netloc}".rstrip('/')

    def _parse_admin_ports(self, raw_ports):
        ports = []
        for raw_part in str(raw_ports or '').split(','):
            part = raw_part.strip()
            if not part:
                continue
            try:
                port = int(part)
            except ValueError:
                continue
            if 1 <= port <= 65535 and port not in ports:
                ports.append(port)
        return ports or [5001, 5000]

    def _admin_auth_headers(self):
        headers = {'X-API-Key': self.shared_api_key}
        auth_data = load_agent_auth()
        key_id = str(auth_data.get('key_id') or '').strip()
        agent_key = str(auth_data.get('agent_key') or '').strip()
        if key_id and agent_key:
            headers['X-Agent-Key-Id'] = key_id
            headers['X-Agent-Key'] = agent_key
        return headers

    def _probe_admin_server(self, base_url, source='discovery'):
        target = f"{base_url.rstrip('/')}/api/tracking/register"
        try:
            response = requests.get(
                target,
                timeout=2,
                headers=self._admin_auth_headers(),
            )
            if response.status_code != 200:
                return None
            payload = {}
            if (response.headers.get('content-type') or '').lower().startswith('application/json'):
                payload = response.json()
            parsed = urlparse(base_url)
            return {
                'ip': parsed.hostname,
                'port': parsed.port,
                'name': payload.get('server_name', 'Unknown'),
                'base_url': base_url.rstrip('/'),
                'source': source,
            }
        except Exception:
            return None

    def _probe_admin_ip_port(self, ip, port):
        return self._probe_admin_server(f"http://{ip}:{port}", source='subnet-scan')

    def _build_scan_candidates(self, network_range=None):
        configured_range = (network_range or self.discovery_subnet or '').strip()
        if configured_range:
            try:
                network = ipaddress.ip_network(configured_range, strict=False)
                return [str(ip) for ip in network.hosts()], configured_range
            except Exception:
                pass

        local_ip = get_local_ip()
        ip_parts = local_ip.split('.')
        if len(ip_parts) != 4:
            return [], 'none'
        base_ip = '.'.join(ip_parts[:3]) + '.'
        return [f"{base_ip}{index}" for index in range(1, 255)], f"{base_ip}*"

    def _has_network_changed(self):
        signature = _build_network_signature()
        if not signature:
            return False
        if not self.last_network_signature:
            self.last_network_signature = signature
            return False
        if signature == self.last_network_signature:
            return False

        now_ts = time.time()
        self.last_network_signature = signature
        if (now_ts - self.last_network_change_sync_at) < self.network_change_debounce_seconds:
            return False
        self.last_network_change_sync_at = now_ts
        return True

    def _log_no_admin_servers(self, reason):
        now = time.time()
        if now - self._last_no_server_log >= self.no_server_log_interval:
            print(f"[AutoSync] No admin servers found ({reason}). Next retry in {self.sync_interval}s.")
            self._last_no_server_log = now
    
    def discover_admin_servers(self, network_range=None):
        """Discover admin servers using explicit target first, then optional subnet scan."""
        discovered_servers = []

        if self.admin_server_url:
            explicit_server = self._probe_admin_server(self.admin_server_url, source='explicit-url')
            if explicit_server:
                self.last_discovery_reason = f"explicit target reachable ({self.admin_server_url})"
                return [explicit_server]
            self.last_discovery_reason = f"explicit target unreachable ({self.admin_server_url})"
            if not self.discovery_enabled:
                return []

        if not self.discovery_enabled:
            self.last_discovery_reason = 'subnet discovery disabled'
            return []

        candidates, range_label = self._build_scan_candidates(network_range=network_range)
        if not candidates:
            self.last_discovery_reason = 'no valid subnet candidates'
            return []

        print(f"[AutoSync] Scanning range {range_label} on ports {','.join(str(port) for port in self.admin_ports)}")

        max_threads = min(20, len(candidates)) if candidates else 1
        with ThreadPoolExecutor(max_workers=max_threads) as executor:
            futures = []
            for ip in candidates:
                for port in self.admin_ports:
                    futures.append(executor.submit(self._probe_admin_ip_port, ip, port))

            for future in futures:
                server = future.result()
                if server:
                    discovered_servers.append(server)
                    # Early exit if we have found enough admin servers (e.g. 2)
                    if len(discovered_servers) >= 2:
                        break

        deduped_servers = []
        seen_base_urls = set()
        for server in discovered_servers:
            base_url = server.get('base_url')
            if not base_url or base_url in seen_base_urls:
                continue
            seen_base_urls.add(base_url)
            deduped_servers.append(server)

        if deduped_servers:
            self.last_discovery_reason = 'discovered via subnet scan'
            return deduped_servers

        self.last_discovery_reason = f"no response in {range_label} on ports {','.join(str(port) for port in self.admin_ports)}"
        return []

    def _handle_sync_response(self, response_json):
        if not isinstance(response_json, dict):
            return

        binding = response_json.get('agent_binding') if isinstance(response_json.get('agent_binding'), dict) else {}
        key_id = str(binding.get('key_id') or '').strip()
        agent_key = str(binding.get('agent_key') or '').strip()
        if key_id and agent_key:
            if save_agent_auth(key_id, agent_key):
                print("[AutoSync] Stored bound agent key from server bootstrap.")

        policy_payload = response_json.get('restricted_sites_policy')
        if isinstance(policy_payload, dict):
            if not policy_payload.get('policy_version'):
                policy_payload['policy_version'] = str(response_json.get('restricted_sites_policy_version') or '')
            if restricted_site_monitor:
                restricted_site_monitor.apply_policy(policy_payload)
        elif restricted_site_monitor and response_json.get('restricted_sites_policy_version'):
            # Only version changed but payload omitted. Force an explicit refresh.
            self.refresh_restricted_policy(force=True)

    def refresh_restricted_policy(self, force=False):
        if restricted_site_monitor is None:
            return False

        now_ts = time.time()
        if not force and (now_ts - self.last_policy_refresh_at) < self.policy_refresh_interval:
            return False
        if not self.last_admin_server:
            return False

        current_version = restricted_site_monitor.get_policy_version()
        headers = self._admin_auth_headers()
        try:
            response = requests.get(
                f"{self.last_admin_server.rstrip('/')}/api/tracking/restricted-sites/policy",
                params={'current_version': current_version},
                timeout=6,
                headers=headers,
            )
            self.last_policy_refresh_at = now_ts
            if response.status_code == 304:
                return True
            if response.status_code != 200:
                return False
            payload = response.json() if (response.headers.get('content-type') or '').lower().startswith('application/json') else {}
            policy = payload.get('policy') if isinstance(payload, dict) else None
            if isinstance(policy, dict):
                if not policy.get('policy_version'):
                    policy['policy_version'] = str(payload.get('policy_version') or '')
                restricted_site_monitor.apply_policy(policy)
                return True
            return False
        except Exception:
            return False
    
    def sync_with_admin(self, admin_server):
        """Sync data with admin server"""
        try:
            base_url = admin_server.get('base_url')
            if not base_url:
                ip = admin_server.get('ip')
                port = admin_server.get('port')
                if not ip or not port:
                    return False
                base_url = f"http://{ip}:{port}"

            parsed_target = urlparse(base_url)
            target_host = parsed_target.hostname
            ip_details = get_local_ip_details(force_refresh=True, target_host=target_host)
            selected_ip = str(ip_details.get('ip') or '').strip()
            ip_candidates = [str(value).strip() for value in (ip_details.get('candidates') or []) if str(value).strip()]
            ip_source = str(ip_details.get('source') or '').strip() or 'unknown'
            network_signature = str(ip_details.get('network_signature') or '').strip()

            pending_event_rows = []
            pending_event_ids = []
            pending_events = []
            if restricted_site_monitor:
                pending_event_rows = restricted_site_monitor.get_pending_events(limit=50)
                pending_event_ids = [row.get('id') for row in pending_event_rows]
                pending_events = [row.get('event') for row in pending_event_rows if isinstance(row.get('event'), dict)]

            sync_data = {
                'mac_address': get_mac_address(),
                'hostname': get_exact_hostname(),
                'ip_address': selected_ip or None,
                'ip_candidates': ip_candidates,
                'ip_source': ip_source,
                'network_signature': network_signature,
                'unique_client_id': get_persistent_client_id(),
                'current_stats': build_live_stats_payload(),
                'api_key': self.shared_api_key,
                'restricted_sites_policy_version': restricted_site_monitor.get_policy_version() if restricted_site_monitor else '',
                'restricted_site_events': pending_events,
                # Omitted `system_info` to dramatically shrink sync payloads per user feedback
            }

            response = requests.post(
                f"{base_url.rstrip('/')}/api/tracking/sync",
                json=sync_data,
                timeout=10,
                headers=self._admin_auth_headers()
            )

            if response.status_code == 200:
                self.last_admin_server = base_url.rstrip('/')
                if pending_event_ids and restricted_site_monitor:
                    restricted_site_monitor.mark_events_sent(pending_event_ids)
                try:
                    payload = response.json()
                except Exception:
                    payload = {}
                self._handle_sync_response(payload)
                print(f"[AutoSync] Synced with admin server: {admin_server.get('name', base_url)}")
                return True
            if pending_event_ids and restricted_site_monitor:
                restricted_site_monitor.mark_events_failed(pending_event_ids)
            print(
                f"[AutoSync] Sync failed with {admin_server.get('name', base_url)} "
                f"(HTTP {response.status_code})"
            )
            return False
        except Exception as e:
            if 'pending_event_ids' in locals() and pending_event_ids and restricted_site_monitor:
                restricted_site_monitor.mark_events_failed(pending_event_ids)
            print(f"[AutoSync] Sync error: {e}")
            return False

    def start_auto_sync(self):
        """Start automatic sync service"""
        def sync_worker():
            current_interval = self.sync_interval
            max_interval = self.sync_interval * 4 # Max backoff
            
            while True:
                if maintenance_mode:
                    time.sleep(5)
                    continue

                print(f"[AutoSync] Scanning for admin servers... (Next in {current_interval}s)")
                servers = self.discover_admin_servers()

                if servers:
                    print(f"[AutoSync] Found {len(servers)} admin server(s)")
                    for server in servers:
                        self.sync_with_admin(server)
                    self._last_no_server_log = 0
                    current_interval = self.sync_interval # Reset backoff on success
                else:
                    self._log_no_admin_servers(self.last_discovery_reason)
                    current_interval = min(current_interval * 1.5, max_interval) # Exponential backoff

                self.refresh_restricted_policy(force=False)
                sleep_remaining = float(current_interval)
                while sleep_remaining > 0:
                    if self._has_network_changed():
                        print("[AutoSync] Network change detected; triggering immediate sync.")
                        break
                    step = min(1.0, sleep_remaining)
                    time.sleep(step)
                    sleep_remaining -= step

        threading.Thread(target=sync_worker, daemon=True).start()


# ============================================================
# FILE TRANSFER ENDPOINTS FOR CLIENT
# ============================================================

@app.route('/api/files/list', methods=['GET'])
@require_api_key
def list_client_files():
    """List files and directories on client system"""
    path = request.args.get('path', '')
    
    # Default to user's home directory
    if not path:
        if platform.system() == 'Windows':
            path = os.path.expanduser('~')
        else:
            path = os.path.expanduser('~')
    
    # Security: prevent directory traversal
    if '..' in path or path.startswith('/') and not path.startswith(os.path.expanduser('~')):
        return jsonify({"error": "Access denied"}), 403
    
    try:
        if not os.path.exists(path):
            return jsonify({"error": "Path does not exist"}), 404
        
        items = []
        for item in os.listdir(path):
            item_path = os.path.join(path, item)
            try:
                item_info = {
                    'name': item,
                    'path': item_path,
                    'is_dir': os.path.isdir(item_path),
                    'size': os.path.getsize(item_path) if os.path.isfile(item_path) else 0,
                    'modified': os.path.getmtime(item_path),
                    'created': os.path.getctime(item_path),
                    'permissions': oct(os.stat(item_path).st_mode)[-3:],
                }
                items.append(item_info)
            except (OSError, PermissionError):
                continue
        
        # Sort: directories first, then files
        items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        
        return jsonify({
            'success': True,
            'current_path': path,
            'parent_path': os.path.dirname(path),
            'items': items
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/files/download', methods=['GET'])
@require_api_key
def download_client_file():
    """Download file from client"""
    file_path = request.args.get('path', '')
    
    if not file_path:
        return jsonify({"error": "File path is required"}), 400
    
    # Security check
    if '..' in file_path or not os.path.exists(file_path):
        return jsonify({"error": "File not found or access denied"}), 404
    
    try:
        if os.path.isdir(file_path):
            # Create zip for directories
            temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
            temp_zip.close()
            
            with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(file_path):
                    for file in files:
                        file_path_full = os.path.join(root, file)
                        arcname = os.path.relpath(file_path_full, file_path)
                        zipf.write(file_path_full, arcname)
            
            return send_file(
                temp_zip.name,
                as_attachment=True,
                download_name=f"{os.path.basename(file_path)}.zip",
                mimetype='application/zip'
            )
        else:
            return send_file(
                file_path,
                as_attachment=True,
                download_name=os.path.basename(file_path)
            )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/files/upload', methods=['POST'])
@require_api_key
def upload_to_client():
    """Upload file to client"""
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    target_path = request.form.get('path', '')
    
    # Default to Downloads folder
    if not target_path:
        if platform.system() == 'Windows':
            target_path = os.path.join(os.path.expanduser('~'), 'Downloads')
        else:
            target_path = os.path.join(os.path.expanduser('~'), 'Downloads')
    
    # Create directory if it doesn't exist
    os.makedirs(target_path, exist_ok=True)
    
    uploaded_files = []
    failed_files = []
    
    files = request.files.getlist('file')
    for file in files:
        if file.filename == '':
            continue
        
        # Secure filename
        filename = secure_filename(file.filename)
        file_path = os.path.join(target_path, filename)
        
        # Handle duplicate filenames
        counter = 1
        while os.path.exists(file_path):
            name, ext = os.path.splitext(filename)
            file_path = os.path.join(target_path, f"{name}_{counter}{ext}")
            counter += 1
        
        try:
            file.save(file_path)
            uploaded_files.append({
                'filename': os.path.basename(file_path),
                'path': file_path,
                'size': os.path.getsize(file_path)
            })
        except Exception as e:
            failed_files.append({
                'filename': file.filename,
                'error': str(e)
            })
    
    return jsonify({
        'success': True,
        'uploaded': len(uploaded_files),
        'failed': len(failed_files),
        'uploaded_files': uploaded_files,
        'failed_files': failed_files,
        'target_path': target_path
    })

@app.route('/api/files/create_folder', methods=['POST'])
@require_api_key
def create_client_folder():
    """Create folder on client"""
    data = request.get_json()
    parent_path = data.get('path', '')
    folder_name = data.get('name', '').strip()
    
    if not folder_name:
        return jsonify({"error": "Folder name is required"}), 400
    
    if not parent_path:
        parent_path = os.path.expanduser('~')
    
    folder_path = os.path.join(parent_path, folder_name)
    
    try:
        os.makedirs(folder_path, exist_ok=True)
        return jsonify({
            'success': True,
            'message': f'Folder created: {folder_path}',
            'path': folder_path
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/files/delete', methods=['POST'])
@require_api_key
def delete_client_file():
    """Delete file/folder on client"""
    data = request.get_json()
    path = data.get('path', '')
    
    if not path:
        return jsonify({"error": "Path is required"}), 400
    
    if not os.path.exists(path):
        return jsonify({"error": "Path does not exist"}), 404
    
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        
        return jsonify({
            'success': True,
            'message': f'Deleted: {path}'
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/files/system_info', methods=['GET'])
@require_api_key
def get_client_system_info():
    """Get client system information including storage"""
    try:
        # Get disk usage for all drives/partitions
        disk_info = []
        if platform.system() == 'Windows':
            import string
            for drive in string.ascii_uppercase:
                drive_path = f"{drive}:\\"
                if os.path.exists(drive_path):
                    try:
                        usage = shutil.disk_usage(drive_path)
                        disk_info.append({
                            'drive': drive_path,
                            'total': usage.total,
                            'used': usage.used,
                            'free': usage.free,
                            'percent': (usage.used / usage.total) * 100
                        })
                    except:
                        pass
        else:
            usage = shutil.disk_usage('/')
            disk_info.append({
                'drive': '/',
                'total': usage.total,
                'used': usage.used,
                'free': usage.free,
                'percent': (usage.used / usage.total) * 100
            })
        
        # Special directories
        special_dirs = {
            'home': os.path.expanduser('~'),
            'desktop': os.path.join(os.path.expanduser('~'), 'Desktop'),
            'downloads': os.path.join(os.path.expanduser('~'), 'Downloads'),
            'documents': os.path.join(os.path.expanduser('~'), 'Documents'),
            'pictures': os.path.join(os.path.expanduser('~'), 'Pictures'),
        }
        
        return jsonify({
            'success': True,
            'system': {
                'platform': platform.platform(),
                'hostname': socket.gethostname(),
                'username': os.getlogin(),
                'home_directory': os.path.expanduser('~')
            },
            'storage': disk_info,
            'directories': special_dirs
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# ENHANCED API ROUTES
# ============================================================

@app.route('/api/secure/stats', methods=['GET'])
@require_api_key
def get_secure_stats():
    """Get secure enhanced statistics"""
    current_time = time.time()
    activity_snapshot = get_activity_snapshot(current_time)
    typed_characters_count = get_typed_text_length()
    sampled_at_utc = datetime.utcnow()
    sample_uuid = str(uuid.uuid4())
    
    # Get latest cached stats (thread-safe copy)
    with current_stats_lock:
        current_stats = current_secure_stats.copy()

    app_usage_seconds = {}
    for app_name, duration_seconds in (system_monitor.app_usage or {}).items():
        try:
            parsed_duration = max(0, int(float(duration_seconds)))
        except (TypeError, ValueError):
            continue
        app_usage_seconds[str(app_name)] = parsed_duration
    
    return jsonify({
        "meta": {
            "sample_uuid": sample_uuid,
            "sampled_at_utc": sampled_at_utc.isoformat(),
            "sample_interval_seconds": 60,
            "schema_version": "2",
            "boot_time": datetime.fromtimestamp(psutil.boot_time()).isoformat(),
        },
        "device_info": {
            "mac_address": get_mac_address(),
            "hostname": socket.gethostname(),
            "ip": get_local_ip(),
            "system": platform.system(),
            "processor": platform.processor(),
            "unique_client_id": get_persistent_client_id(),
        },
        "current_activity": {
            "keyboard_active": activity_snapshot['keyboard_active'],
            "mouse_active": activity_snapshot['mouse_active'],
            "idle_seconds": round(activity_snapshot['idle_seconds'], 2),
            "current_application": system_monitor.current_app
        },
        "today_stats": {
            "total_active_hours": round(activity_snapshot['total_duration'] / 3600, 2),
            "keyboard_active_hours": round(activity_snapshot['keyboard_duration'] / 3600, 2),
            "mouse_active_hours": round(activity_snapshot['mouse_duration'] / 3600, 2),
            "keyboard_events": activity_snapshot['keyboard_count'],
            "mouse_events": activity_snapshot['mouse_count'],
            "characters_typed": typed_characters_count,
            "applications_used": list(system_monitor.app_usage.keys()),
            "app_usage_seconds": app_usage_seconds,
        },
        "system_metrics": {
            "cpu_percent": current_stats.get('core', {}).get('cpu_percent', 0),
            "memory_percent": current_stats.get('core', {}).get('memory_percent', 0),
            "used_gb": current_stats.get('core', {}).get('used_gb', 0),
            "total_gb": current_stats.get('core', {}).get('total_gb', 0),
            "disk_usage": current_stats.get('core', {}).get('disk_usage', 0),  # Use cached value
            "boot_time": datetime.fromtimestamp(psutil.boot_time()).isoformat(),
            # New Enhanced Metrics (Respecting Gating)
            "network_speed": current_stats.get('network') if ENABLE_NET_MONITOR else None, 
            "active_window": current_stats.get('window') if ENABLE_WINDOW_TITLES else None,
            "top_processes": current_stats.get('top_processes') if ENABLE_TOP_PROCESSES else []
        },
        "security": {
            "encryption_enabled": True,
            "last_sync": datetime.now().isoformat(),
            "data_retention": "Daily summaries + Encrypted chunks"
        }
    })

@app.route('/api/secure/sync', methods=['POST'])
@require_api_key  
def secure_sync_endpoint():
    """Endpoint for admin servers to sync data"""
    try:
        sync_data = request.json
        
        # Verify admin server
        admin_key = sync_data.get('admin_key')
        if not verify_admin_key(admin_key):
            return jsonify({"error": "Invalid admin key"}), 401
        
        # Prepare sync response
        response_data = {
            "mac_address": get_mac_address(),
            "sync_timestamp": datetime.now().isoformat(),
            "device_stats": get_secure_stats().get_json(),
            "sync_status": "success"
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================
# ADDITIONAL API ROUTES FOR COMPATIBILITY
# ============================================================

@app.route('/api/tracking/register', methods=['GET'])
def register_endpoint():
    """Endpoint for auto-discovery"""
    return jsonify({
        "server_name": "Enhanced Employee Tracker",
        "status": "active",
        "version": "2.0",
        "mac_address": get_mac_address()
    })

@app.route('/api/tracking/sync', methods=['POST'])
def sync_endpoint():
    """Legacy sync endpoint for compatibility"""
    return secure_sync_endpoint()

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0",
        "uptime": time.time() - psutil.boot_time()
    })

@app.route('/api/maintenance/mode', methods=['POST'])
@require_api_key
def toggle_maintenance_mode():
    """Toggle maintenance mode"""
    global maintenance_mode
    try:
        data = request.get_json()
        enabled = data.get('enabled', False)
        maintenance_mode = enabled
        
        status = "enabled" if maintenance_mode else "disabled"
        print(f"[WARN] Maintenance mode {status}")
        
        return jsonify({
            "success": True,
            "maintenance_mode": maintenance_mode,
            "message": f"Maintenance mode {status}"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500




# ============================================================
# SCREEN CAPTURE MANAGER (Background Thread)
# ============================================================

import pyaudio
import wave
import mss

# ... existing imports ...

# ============================================================
# OPTIMIZED MICROPHONE MANAGER (Background Audio Thread)
# ============================================================

class MicrophoneManager:
    """
    Background thread-based audio capture manager.
    Captures raw PCM audio efficiently using PyAudio.
    """
    def __init__(self, sample_rate=16000, channels=1, chunk_size=1024):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.bits_per_sample = 16
        self.audio = None
        self.stream = None
        self.lock = threading.Lock()
        self.is_running = False
        self.capture_thread = None
        self.active_clients = 0
        self.chunk_counter = 0
        self.last_chunk = None
        self.last_error = None
        self.last_error_time = None
        self.last_start_time = None
        self.active_sample_rate = sample_rate
        self.active_channels = channels
        self.active_input_device_index = None
        self.active_input_device_name = None
        # Circular buffer for audio chunks (store last ~2 seconds)
        self.audio_buffer = deque(maxlen=int(sample_rate / chunk_size * 2))

    def _set_error(self, message: str):
        self.last_error = message
        self.last_error_time = time.time()

    def get_input_device_summary(self):
        """Return count + default input device info for debugging."""
        summary = {
            "input_device_count": 0,
            "default_input_index": None,
            "default_input_name": None
        }
        try:
            pa = pyaudio.PyAudio()
            device_count = pa.get_device_count()
            input_devices = []
            for idx in range(device_count):
                info = pa.get_device_info_by_index(idx)
                if info.get('maxInputChannels', 0) > 0:
                    input_devices.append(info)
            summary["input_device_count"] = len(input_devices)

            try:
                default_info = pa.get_default_input_device_info()
                summary["default_input_index"] = default_info.get('index')
                summary["default_input_name"] = default_info.get('name')
            except Exception:
                pass
            pa.terminate()
        except Exception as e:
            summary["error"] = str(e)
        return summary

    def start_microphone(self):
        """Initialize microphone if not already running"""
        with self.lock:
            if self.is_running:
                return True

            try:
                self.audio = pyaudio.PyAudio()
                device_candidates = []
                seen_device_indexes = set()

                default_info = None
                try:
                    default_info = self.audio.get_default_input_device_info()
                except Exception:
                    default_info = None

                if default_info:
                    default_idx = int(default_info.get("index"))
                    seen_device_indexes.add(default_idx)
                    device_candidates.append({
                        "index": default_idx,
                        "name": default_info.get("name", f"Device {default_idx}"),
                        "max_channels": int(default_info.get("maxInputChannels", 1)),
                        "default_rate": int(default_info.get("defaultSampleRate", self.sample_rate)),
                    })

                for idx in range(self.audio.get_device_count()):
                    info = self.audio.get_device_info_by_index(idx)
                    max_input_channels = int(info.get("maxInputChannels", 0))
                    if max_input_channels <= 0 or idx in seen_device_indexes:
                        continue
                    seen_device_indexes.add(idx)
                    device_candidates.append({
                        "index": idx,
                        "name": info.get("name", f"Device {idx}"),
                        "max_channels": max_input_channels,
                        "default_rate": int(info.get("defaultSampleRate", self.sample_rate)),
                    })

                preferred_rates = [self.sample_rate, 44100, 48000, 32000, 22050]
                tested = set()

                for device in device_candidates:
                    candidate_channels = min(max(1, self.channels), max(1, device["max_channels"]))
                    rate_candidates = [device["default_rate"]] + preferred_rates

                    for rate in rate_candidates:
                        rate = int(rate)
                        key = (device["index"], candidate_channels, rate)
                        if key in tested:
                            continue
                        tested.add(key)

                        try:
                            self.audio.is_format_supported(
                                rate=rate,
                                input_device=device["index"],
                                input_channels=candidate_channels,
                                input_format=pyaudio.paInt16,
                            )
                        except Exception:
                            continue

                        try:
                            self.stream = self.audio.open(
                                format=pyaudio.paInt16,
                                channels=candidate_channels,
                                rate=rate,
                                input=True,
                                input_device_index=device["index"],
                                frames_per_buffer=self.chunk_size,
                            )
                            self.active_sample_rate = rate
                            self.active_channels = candidate_channels
                            self.active_input_device_index = device["index"]
                            self.active_input_device_name = device["name"]
                            break
                        except Exception:
                            self.stream = None

                    if self.stream:
                        break

                if not self.stream:
                    if not device_candidates:
                        raise RuntimeError("No input microphone devices detected")
                    raise RuntimeError("Unable to open any compatible microphone input format.")

                self.audio_buffer.clear()
                self.chunk_counter = 0
                self.last_chunk = None
                self.is_running = True
                self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
                self.capture_thread.start()
                self.last_error = None
                self.last_start_time = time.time()
                print(
                    "Microphone initialized successfully "
                    f"(device='{self.active_input_device_name}', "
                    f"rate={self.active_sample_rate}, channels={self.active_channels})"
                )
                return True
            except Exception as e:
                self._set_error(str(e))
                print(f"Microphone initialization error: {e}")
                self._cleanup()
                return False

    def _capture_loop(self):
        """Background loop that captures audio chunks"""
        print("[OK] Microphone capture thread started")
        while self.is_running:
            if maintenance_mode:
                time.sleep(1)
                continue

            try:
                # Read raw PCM data
                data = self.stream.read(self.chunk_size, exception_on_overflow=False)
                with self.lock:
                    self.audio_buffer.append(data)
                    self.last_chunk = data
                    self.chunk_counter += 1
            except Exception as e:
                self._set_error(str(e))
                print(f"Audio capture error: {e}")
                time.sleep(0.1)
        
        print("Microphone capture thread stopped")

    def get_audio_stream(self):
        """Generator that yields audio chunks for a client"""
        # Wait for buffer to fill slightly
        while len(self.audio_buffer) == 0 and self.is_running:
            time.sleep(0.1)

        last_seen = -1
        try:
            while self.is_running:
                if maintenance_mode:
                    time.sleep(1)
                    continue

                with self.lock:
                    current_counter = self.chunk_counter
                    if current_counter != last_seen:
                        chunk = self.last_chunk
                        last_seen = current_counter
                    else:
                        chunk = None

                if chunk:
                    yield chunk
                
                # Sleep approx duration of one chunk (1024/16000 = ~0.064s)
                # We sleep slightly less to avoid lag build-up
                time.sleep(0.05)

        except GeneratorExit:
            print("Audio client disconnected")

    def _cleanup(self):
        """Cleanup resources"""
        if self.stream:
            try:
                self.stream.stop_stream()
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:
                pass
        if self.audio:
            try:
                self.audio.terminate()
            except Exception:
                pass
        self.stream = None
        self.audio = None
        self.capture_thread = None
        self.is_running = False
        self.active_input_device_index = None
        self.active_input_device_name = None

    def stop_microphone(self):
        """Stop microphone capture"""
        self.is_running = False
        if self.capture_thread:
            self.capture_thread.join(timeout=1)
        self._cleanup()
        print("[OK] Microphone stopped")

# Global Microphone Manager
mic_manager = MicrophoneManager()

# ============================================================
# OPTIMIZED SCREEN CAPTURE MANAGER (Background Thread)
# ============================================================

class ScreenCaptureManager:
    """
    Background thread-based screen capture manager.
    Captures once, streams to many clients.
    OPTIMIZED: Uses mss for high-performance capture.
    """
    def __init__(self, target_fps=5):
        self.latest_frame = None
        self.lock = threading.Lock()
        self.is_running = False
        self.capture_thread = None
        self.target_fps = target_fps
        self.frame_interval = 1.0 / target_fps
        self.sct = None
        
        self.active_clients = 0
        self.client_lock = threading.Lock()

    def add_client(self):
        """Register a client and start thread if needed"""
        with self.client_lock:
            self.active_clients += 1
            if self.active_clients == 1:
                self.start()
            return True

    def remove_client(self):
        """Unregister a client and stop thread if none left"""
        with self.client_lock:
            self.active_clients = max(0, self.active_clients - 1)
            if self.active_clients == 0:
                self.stop()

    def start(self):
        """Start background capture thread"""
        if self.is_running:
            return
        self.is_running = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        print(f"[OK] Screen capture background thread started (Target FPS: {self.target_fps})")

    def stop(self):
        """Stop background capture thread"""
        self.is_running = False
        if self.capture_thread:
            # Short timeout so we don't block
            self.capture_thread.join(timeout=1.0)
            self.capture_thread = None
        with self.lock:
            self.latest_frame = None
        # mss uses thread-local handles; close it only from capture thread context.
        self.sct = None
        print("[OK] Screen capture stopped")

    def _capture_loop(self):
        """Background loop that captures screen frames using mss"""
        # Initialize mss in the thread
        with mss.mss() as sct:
            self.sct = sct
            # Get the primary monitor
            monitor = sct.monitors[1]
            
            while self.is_running:
                if maintenance_mode:
                    time.sleep(0.5)
                    continue

                try:
                    start_time = time.time()

                    # Capture using mss (raw pixels) - Extremely Fast
                    sct_img = sct.grab(monitor)
                    
                    # Convert to numpy array
                    img = np.array(sct_img)
                    
                    # Resize FIRST to reduce array size
                    screen_resized = cv2.resize(img, (1280, 720))
                    
                    # Then color convert the smaller array (BGRA -> BGR)
                    frame_bgr = cv2.cvtColor(screen_resized, cv2.COLOR_BGRA2BGR)

                    # Encode to JPEG (Quality 50% for speed/size)
                    ret, buffer = cv2.imencode('.jpg', frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 50])

                    if ret:
                        frame_bytes = buffer.tobytes()
                        with self.lock:
                            self.latest_frame = frame_bytes

                    # Maintain target FPS
                    elapsed = time.time() - start_time
                    sleep_time = max(0, self.frame_interval - elapsed)
                    time.sleep(sleep_time)

                except Exception as e:
                    print(f"Screen capture error: {e}")
                    time.sleep(1)

    def get_frame(self):
        """Get the latest cached frame (thread-safe)"""
        with self.lock:
            return self.latest_frame

# Global Screen Capture Manager
screen_manager = ScreenCaptureManager(target_fps=5) # 5 FPS is enough for monitoring


def generate_screen_stream():
    """Generate screen stream from cached frames (non-blocking)"""
    print("Starting screen stream generator...")
    screen_manager.add_client()
    try:
        while True:
            if maintenance_mode:
                time.sleep(0.5)
                continue
                
            frame = screen_manager.get_frame()
            if frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            time.sleep(0.2)  # Client poll rate
    except GeneratorExit:
        print("Screen stream client disconnected")
    except Exception as e:
        print(f"Screen stream error: {e}")
    finally:
        try:
            screen_manager.remove_client()
        except Exception as e:
            print(f"Screen stream cleanup error: {e}")


# ============================================================
# ENHANCED CAMERA MANAGER (Background Capture Thread)
# ============================================================

class CameraManager:
    """
    Background thread-based camera capture manager.
    Captures, encodes once, streams to many clients.
    OPTIMIZED: 15 FPS, Quality 60.
    """
    def __init__(self, target_fps=15):
        self.camera = None
        self.lock = threading.Lock()
        self.frame_lock = threading.Lock()
        self.active_clients = 0
        self.is_running = False
        self.is_capturing = False
        self.capture_thread = None
        self.latest_frame_bytes = None
        self.target_fps = target_fps
        self.frame_interval = 1.0 / target_fps

    # ... (rest of CameraManager remains mostly same, ensuring init uses FPS 15) ...
    def start_camera(self):
        """Initialize camera if not already running"""
        with self.lock:
            if self.is_running and self.camera and self.camera.isOpened():
                return True

            try:
                # Try index 0 first, then 1
                for idx in [0, 1]:
                    self.camera = cv2.VideoCapture(idx, cv2.CAP_DSHOW)  # DirectShow for Windows
                    if self.camera.isOpened():
                        break

                if self.camera and self.camera.isOpened():
                    self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    self.camera.set(cv2.CAP_PROP_FPS, self.target_fps)
                    self.camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Minimize buffer lag
                    self.is_running = True
                    
                    # Start background capture thread
                    self._start_capture_thread()
                    
                    print(f"[OK] Camera initialized successfully (Target FPS: {self.target_fps})")
                    return True
                else:
                    print("Failed to open camera")
                    return False
            except Exception as e:
                print(f"Camera initialization error: {e}")
                return False

    def _start_capture_thread(self):
        """Start the background capture thread"""
        if self.is_capturing:
            return
        self.is_capturing = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        print("[OK] Camera capture thread started")

    def _capture_loop(self):
        """Background loop that captures camera frames"""
        while self.is_capturing and self.is_running:
            if maintenance_mode:
                time.sleep(0.1)
                continue

            try:
                start_time = time.time()

                # Read frame from camera
                with self.lock:
                    if self.camera and self.camera.isOpened():
                        ret, frame = self.camera.read()
                    else:
                        ret = False
                        frame = None

                if ret and frame is not None:
                    # Encode to JPEG (Quality 60 for speed)
                    ret_enc, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                    if ret_enc:
                        with self.frame_lock:
                            self.latest_frame_bytes = buffer.tobytes()

                # Maintain target FPS
                elapsed = time.time() - start_time
                sleep_time = max(0, self.frame_interval - elapsed)
                time.sleep(sleep_time)

            except Exception as e:
                print(f"Camera capture error: {e}")
                time.sleep(0.1)

        print("Camera capture thread stopped")

    def get_latest_frame(self):
        """Get the latest cached frame bytes (thread-safe, non-blocking)"""
        with self.frame_lock:
            return self.latest_frame_bytes

    def add_client(self):
        """Register a new streaming client"""
        with self.lock:
            self.active_clients += 1
            print(f"Client added. Active clients: {self.active_clients}")
            
            if not self.is_running:
                # Need to release lock before calling start_camera
                pass
        
        # Start camera if not running (outside lock to avoid deadlock)
        if not self.is_running:
            success = self.start_camera()
            if not success:
                with self.lock:
                    self.active_clients -= 1
                return False
        return True

    def remove_client(self):
        """Unregister a streaming client"""
        with self.lock:
            self.active_clients -= 1
            print(f"Client removed. Active clients: {self.active_clients}")
            if self.active_clients <= 0:
                self.active_clients = 0
                self._stop_internal()

    def force_stop(self):
        """Force stop the camera (admin toggle)"""
        with self.lock:
            self.active_clients = 0
            self._stop_internal()

    def _stop_internal(self):
        """Internal helper to release camera"""
        self.is_capturing = False
        if self.capture_thread:
            self.capture_thread.join(timeout=1)
            self.capture_thread = None
        
        if self.camera:
            self.camera.release()
            self.camera = None
        self.is_running = False
        
        with self.frame_lock:
            self.latest_frame_bytes = None
        print("[OK] Camera released")

    def is_active(self):
        """Check if camera is active"""
        with self.lock:
            return self.is_running


# Global Camera Manager instance (15 FPS DEFAULT)
camera_manager = CameraManager(target_fps=15)


def generate_camera_stream():
    """Generate camera stream from cached frames (non-blocking)"""
    print("Starting camera stream generator...")

    # Register client
    if not camera_manager.add_client():
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + b'' + b'\r\n')
        return

    try:
        while camera_manager.is_active():
            if maintenance_mode:
                time.sleep(0.5)
                continue
                
            frame = camera_manager.get_latest_frame()

            if frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            
            time.sleep(0.033)  # ~30 FPS output

    except GeneratorExit:
        print("Camera stream client disconnected")
    except Exception as e:
        print(f"Camera stream error: {e}")
    finally:
        print("Client disconnected, removing from manager")
        camera_manager.remove_client()


# API Routes
@app.route('/stream', methods=['GET'])
@require_api_key
def stream_screen():
    """Stream screen capture"""
    return Response(generate_screen_stream(),
                   mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/start_camera', methods=['GET'])
@require_api_key
def start_camera():
    """Start camera stream"""
    return Response(generate_camera_stream(),
                   mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/stop_camera', methods=['GET'])
@require_api_key
def stop_camera():
    """Stop camera stream"""
    camera_manager.force_stop()
    return jsonify({"message": "Camera stream stopped"})


@app.route('/camera_status', methods=['GET'])
@require_api_key

def camera_status():
    """Get camera status"""
    is_active = camera_manager.is_active()
    return jsonify({
        "active": is_active,
        "clients": camera_manager.active_clients,
        "message": "Camera is active" if is_active else "Camera is inactive"
    })

# ============================================================
# AUDIO STREAMING ENDPOINTS
# ============================================================

@app.route('/audio_stream.wav', methods=['GET'])
@require_api_key
def stream_audio():
    """Stream microphone audio as WAV/PCM"""
    if not mic_manager.start_microphone():
        return jsonify({
            "error": "Microphone unavailable",
            "details": mic_manager.last_error,
        }), 503

    def audio_gen():
        try:
            for chunk in mic_manager.get_audio_stream():
                yield chunk
        except Exception as e:
            print(f"Audio stream error: {e}")

    return Response(
        audio_gen(),
        mimetype=(
            "audio/x-raw; "
            f"rate={mic_manager.active_sample_rate}; "
            f"channels={mic_manager.active_channels}; "
            "format=s16le"
        ),
        headers={
            "Cache-Control": "no-store",
            "X-Audio-Sample-Rate": str(mic_manager.active_sample_rate),
            "X-Audio-Channels": str(mic_manager.active_channels),
            "X-Audio-Bits": str(mic_manager.bits_per_sample),
        },
    )

@app.route('/mic_status', methods=['GET'])
@require_api_key
def mic_status():
    """Get microphone status"""
    return jsonify({
        "active": mic_manager.is_running,
        "sample_rate": mic_manager.sample_rate,
        "active_sample_rate": mic_manager.active_sample_rate,
        "active_channels": mic_manager.active_channels,
        "active_input_device_index": mic_manager.active_input_device_index,
        "active_input_device_name": mic_manager.active_input_device_name,
        "message": "Microphone is active" if mic_manager.is_running else "Microphone is inactive",
        "last_error": mic_manager.last_error,
        "last_error_time": datetime.fromtimestamp(mic_manager.last_error_time).isoformat() if mic_manager.last_error_time else None,
        "last_start_time": datetime.fromtimestamp(mic_manager.last_start_time).isoformat() if mic_manager.last_start_time else None,
        "input_devices": mic_manager.get_input_device_summary()
    })

@app.route('/stop_mic', methods=['GET'])
@require_api_key
def stop_mic():
    """Stop microphone capture"""
    mic_manager.stop_microphone()
    return jsonify({"message": "Microphone stopped"})



def get_persistent_client_id():
    """
    Get or create a persistent unique client ID.
    Stored in client_id.txt to survive restarts and IP changes.
    """
    client_id_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'client_id.txt')
    try:
        if os.path.exists(client_id_file):
            with open(client_id_file, 'r') as f:
                client_id = f.read().strip()
                if client_id:
                    return client_id
    except Exception as e:
        print(f"Error reading client_id: {e}")

    # Generate new ID if missing or empty
    new_id = str(uuid.uuid4())
    try:
        with open(client_id_file, 'w') as f:
            f.write(new_id)
        # Apply hidden attribute on Windows
        if platform.system() == "Windows":
             import ctypes
             FILE_ATTRIBUTE_HIDDEN = 0x02
             ctypes.windll.kernel32.SetFileAttributesW(client_id_file, FILE_ATTRIBUTE_HIDDEN)
    except Exception as e:
        print(f"Error saving client_id: {e}")
    
    return new_id

@app.route('/api/identity', methods=['GET'])
def get_identity():
    """
    Public identity endpoint for auto-discovery.
    Allows the admin scanner to identify this device as a Tactical Agent.
    """
    try:
        return jsonify({
            "hostname": get_exact_hostname(),
            "mac_address": get_mac_address(),
            "unique_client_id": get_persistent_client_id(),
            "os": f"{platform.system()} {platform.release()}",
            "agent_version": "2.2",
            "type": "Tactical Agent",
            "status": "active"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def toggle_camera_state():
    """Toggle camera state (helper for toggle button)"""
    if camera_manager.is_active():
        camera_manager.force_stop()
        return {"status": "stopped", "active": False, "message": "Camera stopped"}
    else:
        # Pre-initialize camera to ensure it works
        success = camera_manager.start_camera()
        return {
            "status": "started", 
            "active": True, 
            "init_success": success,
            "message": "Camera started" if success else "Failed to start camera"
        }

@app.route('/toggle_camera', methods=['GET', 'POST'])
def toggle_camera_route():
    """Toggle camera endpoint"""
    result = toggle_camera_state()
    return jsonify(result)


# ============================================================
# INITIALIZATION
# ============================================================

def initialize_enhanced_tracker():
    """Initialize enhanced tracking system"""
    global discovery_service, restricted_site_monitor
    print("[INIT] INITIALIZING ENHANCED TRACKING SYSTEM")
    print("=" * 60)
    
    # Initialize secure database
    init_secure_database()
    
    # Load previous daily stats
    load_daily_stats()
    
    # Register device
    register_or_update_employee()

    restricted_site_monitor = RestrictedSiteMonitor()
    restricted_site_monitor.apply_policy(DEFAULT_RESTRICTED_POLICY)
    
    # Start enhanced input listeners
    start_enhanced_listeners()
    
    # Start activity tracker
    threading.Thread(target=enhanced_activity_tracker, daemon=True).start()
    
    # Start Explicit Interval Monitor (New Loop)
    threading.Thread(target=explicit_interval_monitor, daemon=True).start()
    
    # We delay screen capture starting until a client connects
    # screen_manager.start()
    
    # Start auto-discovery service
    discovery_service = AutoDiscoveryService()
    discovery_service.start_auto_sync()
    discovery_service.refresh_restricted_policy(force=True)
    
    print("[OK] Enhanced tracking system initialized")
    print("[OK] Screen capture background thread active")
    print(f"[OK] Auto-sync service started ({discovery_service.sync_interval}s intervals)")
    print("[OK] Secure encryption enabled")
    print("[OK] Application usage tracking active")
    print(f"[INFO] Service running on: http://{get_local_ip()}:5002")
    print("=" * 60)

def enhanced_activity_tracker():
    """Enhanced background activity tracker"""
    last_loop_time = time.time()
    while True:
        if maintenance_mode:
            time.sleep(1)
            last_loop_time = time.time() # Reset delta
            continue
            
        current_time = time.time()
        delta = current_time - last_loop_time
        last_loop_time = current_time
        
        update_enhanced_activity_times(delta)
        time.sleep(1)

def explicit_interval_monitor():
    """
    Explicit scheduling loop for metrics collection.
    Prevents drift and manages different collection intervals.
    """
    next_core = 0
    next_net = 0
    next_proc = 0
    next_window = 0
    next_dns = 0
    next_policy_refresh = 0
    
    CORE_INTERVAL = 2.0    # CPU/RAM every 2s
    NET_INTERVAL = 5.0     # Network every 5s
    PROCESS_INTERVAL = 60  # Top processes every 60s
    WINDOW_INTERVAL = 10.0 # Window Title every 10s
    POLICY_REFRESH_INTERVAL = 300.0
    
    print("[OK] Started explicit interval monitor")
    
    while True:
        try:
            now = time.time()
            
            # 1. Core Metrics (High Frequency)
            if now >= next_core:
                metrics = core_monitor.get_core_metrics()
                with current_stats_lock:
                    current_secure_stats['core'] = metrics
                next_core = now + CORE_INTERVAL
                
            # 2. Network Metrics (Medium Frequency)
            if ENABLE_NET_MONITOR and now >= next_net:
                net_metrics = network_monitor.get_network_metrics()
                with current_stats_lock:
                    current_secure_stats['network'] = net_metrics
                next_net = now + NET_INTERVAL
                
            # 3. Top Processes (Low Frequency)
            if ENABLE_TOP_PROCESSES and now >= next_proc:
                procs = process_monitor.get_top_processes(limit=3)
                with current_stats_lock:
                    current_secure_stats['top_processes'] = procs
                next_proc = now + PROCESS_INTERVAL
                
            # 4. Window Title (Medium Frequency)
            if ENABLE_WINDOW_TITLES and now >= next_window:
                window = window_monitor.get_active_window(enabled=True)
                with current_stats_lock:
                    current_secure_stats['window'] = window
                if restricted_site_monitor and window:
                    restricted_site_monitor.handle_window_event(window)
                window_interval = WINDOW_INTERVAL
                if restricted_site_monitor:
                    try:
                        window_interval = float(restricted_site_monitor.get_window_poll_seconds() or WINDOW_INTERVAL)
                    except Exception:
                        window_interval = WINDOW_INTERVAL
                next_window = now + max(5.0, window_interval)

            if restricted_site_monitor and now >= next_dns:
                restricted_site_monitor.poll_dns_cache()
                try:
                    dns_interval = float(restricted_site_monitor.get_dns_poll_seconds() or 60.0)
                except Exception:
                    dns_interval = 60.0
                next_dns = now + max(15.0, dns_interval)

            if discovery_service and now >= next_policy_refresh:
                discovery_service.refresh_restricted_policy(force=False)
                next_policy_refresh = now + POLICY_REFRESH_INTERVAL
            
            time.sleep(0.5) # Resolution sleep
            
        except Exception as e:
            print(f"Interval monitor error: {e}")
            time.sleep(5)

def start_enhanced_listeners():
    """Start enhanced input listeners"""
    def start_keyboard():
        with keyboard.Listener(on_press=on_key_press_enhanced) as listener:
            listener.join()
    
    def start_mouse():
        with mouse.Listener(on_move=on_move_enhanced, on_click=on_click_enhanced) as listener:
            listener.join()
    
    threading.Thread(target=start_keyboard, daemon=True).start()
    threading.Thread(target=start_mouse, daemon=True).start()


# Global lock file handle
_lock_file = None

def ensure_single_instance():
    """
    Ensure only one instance of the service is running using file locking.
    Uses msvcrt on Windows to acquire a non-blocking lock.
    """
    global _lock_file
    import sys
    import os
    
    lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'service.lock')
    
    try:
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except:
                pass # active instance might hold it
                
        _lock_file = open(lock_path, 'w')
        
        try:
            import msvcrt
            msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            # Write PID
            _lock_file.write(str(os.getpid()))
            _lock_file.flush()
        except ImportError:
            # Fallback for non-Windows (fcntl)
            import fcntl
            fcntl.lockf(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_file.write(str(os.getpid()))
            _lock_file.flush()
            
    except IOError:
        print("\n\n[ERROR] Another instance of service.py is already running!")
        print(f"Check {lock_path} or use task manager to kill python processes.")
        sys.exit(1)
    except Exception as e:
        print(f"Warning: Could not acquire lock: {e}")

if __name__ == '__main__':
    ensure_single_instance()
    initialize_enhanced_tracker()
    
    try:
        app.run(host='0.0.0.0', port=5002, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\n\n[STOP] Shutting down enhanced tracker...")
        # Final data save
        save_daily_summary_enhanced()
        save_encrypted_typed_text()
        system_monitor.save_application_usage()
        print("[OK] All data securely saved")
