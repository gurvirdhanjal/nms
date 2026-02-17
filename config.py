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
            
    # Normalize path for SQLite URI on Windows (backslashes -> forward slashes)
    _default_db_path = os.path.join(INSTANCE_DIR, 'device_monitoring.db')
    if os.name == 'nt':
        _default_db_path = _default_db_path.replace('\\', '/')

    _env_db_url = os.environ.get('DATABASE_URL')
    
    # Defensive fix: If DATABASE_URL is set but looks like a raw file path (no scheme), fix it
    if _env_db_url and '://' not in _env_db_url:
        if os.name == 'nt':
            _env_db_url = _env_db_url.replace('\\', '/')
        _env_db_url = 'sqlite:///' + _env_db_url

    SQLALCHEMY_DATABASE_URI = _env_db_url or ('sqlite:///' + _default_db_path)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False

    # Database policy
    # Set REQUIRE_POSTGRES=true in production to enforce PostgreSQL-only runtime.
    REQUIRE_POSTGRES = os.environ.get('REQUIRE_POSTGRES', 'false').lower() == 'true'
    
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

    # Server metrics retention policy
    SERVER_HEALTH_RAW_RETENTION_DAYS = int(os.environ.get('SERVER_HEALTH_RAW_RETENTION_DAYS', 7))
    SERVER_HEALTH_HOURLY_RETENTION_DAYS = int(os.environ.get('SERVER_HEALTH_HOURLY_RETENTION_DAYS', 30))
    SERVER_HEALTH_DAILY_RETENTION_DAYS = int(os.environ.get('SERVER_HEALTH_DAILY_RETENTION_DAYS', 365))
    SERVER_HEALTH_RETENTION_SCHEDULE = os.environ.get('SERVER_HEALTH_RETENTION_SCHEDULE', '02:00')

    # ─── LDAP / Active Directory ───────────────────────────
    LDAP_ENABLED = os.environ.get('LDAP_ENABLED', 'false').lower() == 'true'
    LDAP_SERVER = os.environ.get('LDAP_SERVER', '')                   # ldap://dc01.domain.local:389
    LDAP_BASE_DN = os.environ.get('LDAP_BASE_DN', '')                 # DC=domain,DC=local
    LDAP_BIND_DN = os.environ.get('LDAP_BIND_DN', '')                 # Service account DN
    LDAP_BIND_PASSWORD = os.environ.get('LDAP_BIND_PASSWORD', '')
    LDAP_USER_SEARCH_FILTER = os.environ.get('LDAP_USER_SEARCH_FILTER', '(sAMAccountName={username})')

    # TLS / Security
    LDAP_USE_SSL = os.environ.get('LDAP_USE_SSL', 'false').lower() == 'true'       # ldaps://
    LDAP_STARTTLS = os.environ.get('LDAP_STARTTLS', 'false').lower() == 'true'     # STARTTLS upgrade
    LDAP_TLS_VALIDATE = os.environ.get('LDAP_TLS_VALIDATE', 'CERT_REQUIRED')       # CERT_REQUIRED | CERT_NONE
    LDAP_CA_CERT_FILE = os.environ.get('LDAP_CA_CERT_FILE', '')                     # Path to CA bundle

    # Timeouts (seconds)
    LDAP_CONNECT_TIMEOUT = int(os.environ.get('LDAP_CONNECT_TIMEOUT', 5))
    LDAP_RECEIVE_TIMEOUT = int(os.environ.get('LDAP_RECEIVE_TIMEOUT', 5))

    # Attribute mapping
    LDAP_ATTR_EMAIL = os.environ.get('LDAP_ATTR_EMAIL', 'mail')
    LDAP_ATTR_DISPLAY_NAME = os.environ.get('LDAP_ATTR_DISPLAY_NAME', 'displayName')
    LDAP_ATTR_GUID = os.environ.get('LDAP_ATTR_GUID', 'objectGUID')

    # Role mapping
    LDAP_DEFAULT_ROLE = os.environ.get('LDAP_DEFAULT_ROLE', 'user')
    LDAP_ADMIN_GROUP = os.environ.get('LDAP_ADMIN_GROUP', '')         # CN=MonitorAdmins,OU=Groups,...
