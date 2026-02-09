import os
import sys
from datetime import timedelta
from dotenv import load_dotenv

# Determine if we are running in a frozen state (PyInstaller)
if getattr(sys, 'frozen', False):
    # If frozen, sys.executable is the path to the exe
    # sys._MEIPASS is the temp folder where data is unpacked
    EXEC_DIR = os.path.dirname(sys.executable)
    BASE_DIR = sys._MEIPASS
else:
    # If not frozen, standard paths apply
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    EXEC_DIR = BASE_DIR

# Load .env file from the execution directory (where the exe is)
load_dotenv(os.path.join(EXEC_DIR, '.env'))

class Config:
    # Security
    SECRET_KEY = os.environ.get('SECRET_KEY', 'change-this-secret-key-in-production')
    
    # Database - Save to EXEC_DIR/instance so data persists outside the temp folder
    # Ensure instance folder exists
    INSTANCE_DIR = os.path.join(EXEC_DIR, 'instance')
    if not os.path.exists(INSTANCE_DIR):
        try:
            os.makedirs(INSTANCE_DIR)
        except OSError:
            pass # Might fail if no write permissions, but we try
            
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'sqlite:///' + os.path.join(INSTANCE_DIR, 'device_monitoring.db')
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False
    
    # Engine options (SQLite vs Postgres)
    SQLALCHEMY_ENGINE_OPTIONS = {
        # Keep connections healthy in long-running app
        'pool_pre_ping': True
    }
    if SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
        # SQLite Options: 30s timeout to allow concurrent writes (prevents "database is locked" errors)
        SQLALCHEMY_ENGINE_OPTIONS.update({
            'connect_args': {
                'timeout': 30,
                # Needed for multi-threaded background pollers with SQLite
                'check_same_thread': False
            }
        })

    # Interface polling interval (seconds)
    INTERFACE_POLL_INTERVAL = int(os.environ.get('INTERFACE_POLL_INTERVAL', 30))
    
    # Session
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=5)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_REFRESH_EACH_REQUEST = True
    
    # Monitoring Settings
    MONITORING_INTERVAL = int(os.environ.get('MONITORING_INTERVAL', 300))
    SCAN_SAMPLES_PER_HOUR = 12

    # SNMP Defaults (Switch Discovery)
    SNMP_COMMUNITY = os.environ.get('SNMP_COMMUNITY', 'public')
    SNMP_VERSION = os.environ.get('SNMP_VERSION', '2c')
    
    # Email Settings
    SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
    
    # API Settings (Agent communication)
    API_KEY = os.environ.get('TRACKING_API_KEY', '8f42v73054r1749f8g58848be5e6502c')

    # API Key for mobile/external clients
    MOBILE_API_KEY = os.environ.get('MOBILE_API_KEY')

    # Enforce Postgres-only ingestion for agent metrics
    REQUIRE_POSTGRES_ONLY = os.environ.get('REQUIRE_POSTGRES_ONLY', 'false').lower() == 'true'
