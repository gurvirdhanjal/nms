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


def _env_int(name, default, minimum=None, maximum=None):
    raw_value = os.environ.get(name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def _env_port_list(name, default):
    raw_value = str(os.environ.get(name, default) or default)
    ports = []
    for token in raw_value.split(','):
        token = token.strip()
        if not token:
            continue
        try:
            port = int(token)
        except (TypeError, ValueError):
            continue
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)
    return ports


class Config:
    # Runtime environment
    APP_ENV = os.environ.get('APP_ENV', os.environ.get('FLASK_ENV', 'development')).lower()
    DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    IS_PRODUCTION = APP_ENV == 'production' and not DEBUG

    # Template rendering
    # Disable template auto-reload in production by default.
    TEMPLATES_AUTO_RELOAD = os.environ.get(
        'TEMPLATES_AUTO_RELOAD',
        'false' if IS_PRODUCTION else 'true'
    ).lower() == 'true'

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
    else:
        SQLALCHEMY_ENGINE_OPTIONS.update({
            # pool_size: persistent connections kept open.
            # max_overflow: burst connections (closed after use, not recycled).
            # 20 base + 15 overflow = 35 max. Waitress runs 16 threads;
            # scheduler adds ~5 background threads → needs ~21 peak connections.
            'pool_size': _env_int('DB_POOL_SIZE', 20, minimum=5),
            'max_overflow': _env_int('DB_POOL_MAX_OVERFLOW', 15, minimum=0),
            # Fail fast (10s) when pool is exhausted — prevents cascade where
            # waiting requestors pile up and inflate the apparent load.
            'pool_timeout': _env_int('DB_POOL_TIMEOUT_SECONDS', 10, minimum=5),
            'pool_recycle': _env_int('DB_POOL_RECYCLE_SECONDS', 1800, minimum=60),
            'pool_use_lifo': os.environ.get('DB_POOL_USE_LIFO', 'true').lower() == 'true',
            # PostgreSQL-side safety net — server enforces these regardless of
            # Python code bugs or hung threads:
            #   lock_timeout: kill any statement waiting >5 s for a row/table lock.
            #     Prevents lock-pile-up storms when a slow writer holds a tuple lock.
            #   idle_in_transaction_session_timeout: terminate connections that open
            #     a transaction and go idle >60 s. Catches Python threads that acquire
            #     a session and then block on an in-process lock before committing.
            'connect_args': {
                'options': (
                    '-c lock_timeout={lock_ms}'
                    ' -c idle_in_transaction_session_timeout={idle_ms}'
                    ' -c statement_timeout={stmt_ms}'
                ).format(
                    lock_ms=_env_int('DB_LOCK_TIMEOUT_MS', 5000, minimum=500),
                    idle_ms=_env_int('DB_IDLE_IN_TX_TIMEOUT_MS', 60000, minimum=5000),
                    stmt_ms=_env_int('DB_STATEMENT_TIMEOUT_MS', 120000, minimum=5000),
                )
            },
        })

    # Compression (Flask-Compress)
    COMPRESS_ENABLED = os.environ.get('COMPRESS_ENABLED', 'true').lower() == 'true'
    COMPRESS_LEVEL = int(os.environ.get('COMPRESS_LEVEL', 6))
    COMPRESS_MIN_SIZE = int(os.environ.get('COMPRESS_MIN_SIZE', 512))
    COMPRESS_MIMETYPES = [
        'text/html',
        'text/css',
        'text/xml',
        'text/plain',
        'application/json',
        'application/javascript',
        'text/javascript',
        'image/svg+xml'
    ]

    # Static caching defaults.
    # JS/CSS with version query (?v=...) are upgraded to immutable in app.py.
    STATIC_MAX_AGE_SECONDS = int(os.environ.get('STATIC_MAX_AGE_SECONDS', 3600))
    STATIC_IMMUTABLE_MAX_AGE_SECONDS = int(
        os.environ.get('STATIC_IMMUTABLE_MAX_AGE_SECONDS', 31536000)
    )
    SEND_FILE_MAX_AGE_DEFAULT = STATIC_MAX_AGE_SECONDS

    # Interface polling interval (seconds)
    INTERFACE_POLL_INTERVAL = int(os.environ.get('INTERFACE_POLL_INTERVAL', 30))
    
    # Session
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=5)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'True').lower() == 'true'
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
    API_KEY = os.environ.get('TRACKING_API_KEY', '')
    AGENT_ALLOW_SHARED_TOKEN_BOOTSTRAP = (
        os.environ.get(
            'AGENT_ALLOW_SHARED_TOKEN_BOOTSTRAP',
            os.environ.get('TRACKING_ALLOW_SHARED_AGENT_KEY_BOOTSTRAP', 'true')
        ).lower() == 'true'
    )
    TRACKING_ALLOW_SHARED_AGENT_KEY_BOOTSTRAP = (
        os.environ.get('TRACKING_ALLOW_SHARED_AGENT_KEY_BOOTSTRAP', 'true').lower() == 'true'
    )
    TRACKING_AGENT_IP_REQUIRE_PRIVATE = (
        os.environ.get('TRACKING_AGENT_IP_REQUIRE_PRIVATE', 'true').lower() == 'true'
    )
    TRACKING_RECONCILE_DRYRUN = os.environ.get('TRACKING_RECONCILE_DRYRUN', 'false').lower() == 'true'
    SUPER_ADMIN_USERNAMES = os.environ.get('SUPER_ADMIN_USERNAMES', '')
    TRACKING_RAW_RETENTION_DAYS = int(os.environ.get('TRACKING_RAW_RETENTION_DAYS', 30))
    TRACKING_HOURLY_RETENTION_DAYS = int(os.environ.get('TRACKING_HOURLY_RETENTION_DAYS', 365))
    TRACKING_DAILY_RETENTION_DAYS = int(os.environ.get('TRACKING_DAILY_RETENTION_DAYS', 1095))
    TRACKING_HOURLY_ROLLUP_AT = os.environ.get('TRACKING_HOURLY_ROLLUP_AT', ':12')
    TRACKING_DAILY_ROLLUP_SCHEDULE = os.environ.get('TRACKING_DAILY_ROLLUP_SCHEDULE', '00:35')
    TRACKING_INTEGRITY_CHECK_SCHEDULE = os.environ.get('TRACKING_INTEGRITY_CHECK_SCHEDULE', '03:30')
    TRACKING_WORKSTATION_UI_V2 = os.environ.get('TRACKING_WORKSTATION_UI_V2', 'false').lower() == 'true'
    TRACKING_HEARTBEAT_INTERVAL_SECONDS = int(
        os.environ.get('TRACKING_HEARTBEAT_INTERVAL_SECONDS', 300)
    )
    TRACKING_AGENT_CHECKIN_WINDOW_SECONDS = int(
        os.environ.get('TRACKING_AGENT_CHECKIN_WINDOW_SECONDS', 180)
    )
    TRACKING_AGENT_PORT = _env_int(
        'TRACKING_AGENT_PORT',
        os.environ.get('PORT', 5002),
        minimum=1,
        maximum=65535,
    )
    TRACKING_AGENT_PORTS = _env_port_list(
        'TRACKING_AGENT_PORTS',
        str(TRACKING_AGENT_PORT),
    ) or [TRACKING_AGENT_PORT]
    TRACKING_AGENT_PORT_CACHE_TTL_SECONDS = _env_int(
        'TRACKING_AGENT_PORT_CACHE_TTL_SECONDS',
        43200,
        minimum=60,
    )
    TRACKING_DISCOVERY_CACHE_TTL_SECONDS = _env_int(
        'TRACKING_DISCOVERY_CACHE_TTL_SECONDS',
        120,
        minimum=15,
    )
    TRACKING_IDENTITY_SCAN_FRESHNESS_MINUTES = _env_int(
        'TRACKING_IDENTITY_SCAN_FRESHNESS_MINUTES',
        15,
        minimum=1,
    )
    TRACKING_WORKSTATION_STALE_MINUTES = int(
        os.environ.get('TRACKING_WORKSTATION_STALE_MINUTES', 15)
    )
    TRACKING_REPORT_MAX_DAYS = int(os.environ.get('TRACKING_REPORT_MAX_DAYS', 90))
    TRACKING_REPORT_PAGE_MAX_LIMIT = int(os.environ.get('TRACKING_REPORT_PAGE_MAX_LIMIT', 200))

    # API Key for mobile/external clients
    MOBILE_API_KEY = os.environ.get('MOBILE_API_KEY')

    # Redis configuration
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    REDIS_MAX_CONNECTIONS = _env_int(
        'REDIS_MAX_CONNECTIONS',
        30,
        minimum=1,
    )
    REDIS_BLOCKING_POOL_TIMEOUT_SECONDS = _env_int(
        'REDIS_BLOCKING_POOL_TIMEOUT_SECONDS',
        10,
        minimum=1,
    )
    REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS = _env_int(
        'REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS',
        2,
        minimum=1,
    )
    REDIS_SOCKET_TIMEOUT_SECONDS = _env_int(
        'REDIS_SOCKET_TIMEOUT_SECONDS',
        2,
        minimum=1,
    )
    REDIS_HEALTH_CHECK_INTERVAL_SECONDS = _env_int(
        'REDIS_HEALTH_CHECK_INTERVAL_SECONDS',
        30,
        minimum=5,
    )
    REDIS_SSE_ENABLED = os.environ.get('REDIS_SSE_ENABLED', 'true').lower() == 'true'

    # Enforce Postgres-only ingestion for agent metrics
    REQUIRE_POSTGRES_ONLY = os.environ.get('REQUIRE_POSTGRES_ONLY', 'false').lower() == 'true'

    # Server metrics retention policy
    SERVER_HEALTH_RAW_RETENTION_DAYS = int(os.environ.get('SERVER_HEALTH_RAW_RETENTION_DAYS', 7))
    SERVER_HEALTH_HOURLY_RETENTION_DAYS = int(os.environ.get('SERVER_HEALTH_HOURLY_RETENTION_DAYS', 30))
    SERVER_HEALTH_DAILY_RETENTION_DAYS = int(os.environ.get('SERVER_HEALTH_DAILY_RETENTION_DAYS', 365))
    DAILY_DEVICE_STATS_SCHEDULE = os.environ.get('DAILY_DEVICE_STATS_SCHEDULE', '00:15')
    SERVER_HEALTH_HOURLY_ROLLUP_AT = os.environ.get('SERVER_HEALTH_HOURLY_ROLLUP_AT', ':08')
    SERVER_HEALTH_DAILY_ROLLUP_SCHEDULE = os.environ.get('SERVER_HEALTH_DAILY_ROLLUP_SCHEDULE', '00:25')
    SERVER_HEALTH_RETENTION_SCHEDULE = os.environ.get('SERVER_HEALTH_RETENTION_SCHEDULE', '02:00')
    SERVER_HEALTH_ROLLUP_INTEGRITY_SCHEDULE = os.environ.get(
        'SERVER_HEALTH_ROLLUP_INTEGRITY_SCHEDULE',
        '03:00'
    )
    SERVER_HEALTH_ROLLUP_INTEGRITY_LOOKBACK_DAYS = int(
        os.environ.get('SERVER_HEALTH_ROLLUP_INTEGRITY_LOOKBACK_DAYS', 45)
    )

    # Feature flags
    ENABLE_PRODUCTIVITY_REPORT = os.environ.get('ENABLE_PRODUCTIVITY_REPORT', 'false').lower() == 'true'
    ENABLE_SERVER_FULLPAGE_TELEMETRY = os.environ.get('ENABLE_SERVER_FULLPAGE_TELEMETRY', 'false').lower() == 'true'

    # Report safety and performance controls
    MAX_REPORT_RANGE_DAYS = int(os.environ.get('MAX_REPORT_RANGE_DAYS', 90))
    MAX_NETWORK_REPORT_RANGE_DAYS = int(os.environ.get('MAX_NETWORK_REPORT_RANGE_DAYS', 30))
    MAX_PRODUCTIVITY_REPORT_RANGE_DAYS = int(os.environ.get('MAX_PRODUCTIVITY_REPORT_RANGE_DAYS', 30))
    MAX_REPORT_ROWS = int(os.environ.get('MAX_REPORT_ROWS', 200000))
    MAX_EXPORT_ROWS = int(os.environ.get('MAX_EXPORT_ROWS', 200000))
    REPORT_CACHE_TTL_SECONDS = int(os.environ.get('REPORT_CACHE_TTL_SECONDS', 180))
    REPORT_STATEMENT_TIMEOUT_MS = int(os.environ.get('REPORT_STATEMENT_TIMEOUT_MS', 15000))
    REPORT_TIMEOUT_ENTERPRISE_MS = int(os.environ.get('REPORT_TIMEOUT_ENTERPRISE_MS', 20000))
    # Infrastructure device types for Server Fleet reports (lowercase, comma-separated in .env)
    INFRASTRUCTURE_DEVICE_TYPES = [
        t.strip().lower().replace(' ', '_') for t in
        os.environ.get('INFRASTRUCTURE_DEVICE_TYPES', 'server,switch,access_point,router,firewall').split(',')
    ]
    # Gemini-powered insight enhancement (Layer 2, optional)
    GEMINI_REPORT_INSIGHTS_ENABLED = os.environ.get('GEMINI_REPORT_INSIGHTS_ENABLED', 'false').lower() == 'true'
    REPORT_RATE_LIMIT_PER_MINUTE = int(os.environ.get('REPORT_RATE_LIMIT_PER_MINUTE', 5))
    REPORT_EXPORT_RATE_LIMIT_PER_MINUTE = int(
        os.environ.get('REPORT_EXPORT_RATE_LIMIT_PER_MINUTE', 3)
    )
    REPORT_ASYNC_JOB_TTL_SECONDS = int(os.environ.get('REPORT_ASYNC_JOB_TTL_SECONDS', 3600))
    REPORT_MAX_CONCURRENT_EXPORT_JOBS = int(
        os.environ.get('REPORT_MAX_CONCURRENT_EXPORT_JOBS', 2)
    )
    REPORT_EXPORT_JOB_BACKEND = os.environ.get('REPORT_EXPORT_JOB_BACKEND', 'db').strip().lower() or 'db'
    REPORT_CACHE_TTL_24H_SECONDS = int(os.environ.get('REPORT_CACHE_TTL_24H_SECONDS', 60))
    REPORT_CACHE_TTL_7D_30D_SECONDS = int(os.environ.get('REPORT_CACHE_TTL_7D_30D_SECONDS', 180))
    REPORT_CACHE_TTL_LONG_RANGE_SECONDS = int(os.environ.get('REPORT_CACHE_TTL_LONG_RANGE_SECONDS', 300))
    MAX_REPORT_CACHE_ENTRIES = int(os.environ.get('MAX_REPORT_CACHE_ENTRIES', 500))
    REPORT_ESTIMATED_INTERFACES_PER_DEVICE = int(
        os.environ.get('REPORT_ESTIMATED_INTERFACES_PER_DEVICE', 4)
    )

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
    LDAP_GROUP_SEARCH_BASE = os.environ.get('LDAP_GROUP_SEARCH_BASE', '')
    LDAP_GROUP_SEARCH_FILTER = os.environ.get(
        'LDAP_GROUP_SEARCH_FILTER',
        '(|(member={user_dn})(uniqueMember={user_dn})(memberUid={username}))'
    )

    # Role mapping
    LDAP_DEFAULT_ROLE = os.environ.get('LDAP_DEFAULT_ROLE', 'user')
    LDAP_ADMIN_GROUP = os.environ.get('LDAP_ADMIN_GROUP', '')         # CN=MonitorAdmins,OU=Groups,...
