import logging
import os
import re
import threading
import webbrowser
from datetime import timedelta

from flask import Flask, jsonify, render_template, request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine.url import make_url

from config import BASE_DIR, Config, ProductionConfig, TestingConfig
from extensions import db, bcrypt, limiter

try:
    from flask_compress import Compress
except Exception:
    Compress = None


def _safe_db_uri(uri: str) -> str:
    if not uri:
        return "<empty>"
    try:
        url = make_url(uri)
        if url.password:
            url = url.set(password="***")
        return str(url)
    except Exception:
        return "<unparseable>"


def _compute_asset_version() -> str:
    """Return a short build fingerprint for cache-busting static assets.

    In debug/dev mode: uses a per-restart timestamp so every server restart
    flushes browser CSS/JS caches immediately.
    In production: uses the HEAD git commit hash (first 8 chars) so the version
    only changes on deploy, minimising unnecessary cache invalidation.
    """
    import time
    # Dev mode: per-restart timestamp — always flushes browser cache on reload
    if os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true') or \
       os.environ.get('FLASK_ENV', '') == 'development':
        return hex(int(time.time()))[2:]

    try:
        import subprocess
        result = subprocess.run(
            ['git', 'rev-parse', '--short=8', 'HEAD'],
            capture_output=True, text=True, timeout=3,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    # Fallback: per-restart timestamp
    return hex(int(time.time()))[2:]


_ASSET_VERSION = _compute_asset_version()


def create_app(test_config=None):
    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, 'templates'),
        static_folder=os.path.join(BASE_DIR, 'static'),
        instance_path=Config.INSTANCE_DIR,
    )
    config_object = Config
    app_env = str(os.environ.get('APP_ENV', os.environ.get('FLASK_ENV', 'development'))).lower()
    if test_config and test_config.get('TESTING'):
        config_object = TestingConfig
    elif app_env == 'production':
        ProductionConfig.require_database_url()
        config_object = ProductionConfig
    app.config.from_object(config_object)

    if test_config:
        app.config.update(test_config)
        # When tests override SQLALCHEMY_DATABASE_URI (e.g., to SQLite), the
        # SQLALCHEMY_ENGINE_OPTIONS computed by Config may still contain
        # PostgreSQL-specific connect_args (e.g., 'options' with lock_timeout).
        # Replace those with SQLite-compatible options so db.create_all() works.
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        if db_uri.startswith('sqlite'):
            app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
                'pool_pre_ping': True,
                'connect_args': {
                    'timeout': 30,
                    'check_same_thread': False,
                },
            }

    # Ensure production does not pay template reload overhead.
    if app.config.get('IS_PRODUCTION'):
        app.config['TEMPLATES_AUTO_RELOAD'] = False
    app.jinja_env.auto_reload = app.config.get('TEMPLATES_AUTO_RELOAD', False)

    # Jinja2 filter: format UTC-naive datetimes in India Standard Time (UTC+5:30)
    try:
        from zoneinfo import ZoneInfo as _ZoneInfo
        _IST_TZ = _ZoneInfo('Asia/Kolkata')
    except ImportError:
        import pytz as _pytz
        _IST_TZ = _pytz.timezone('Asia/Kolkata')

    from datetime import timezone as _utc_tz

    def _ist_filter(dt, fmt='%d %b %Y, %H:%M:%S'):
        if dt is None:
            return 'N/A'
        try:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_utc_tz.utc)
            return dt.astimezone(_IST_TZ).strftime(fmt)
        except Exception:
            return dt.strftime(fmt)

    app.jinja_env.filters['ist'] = _ist_filter

    db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    is_testing = bool(app.config.get('TESTING'))
    if not db_uri:
        raise RuntimeError(
            "SQLALCHEMY_DATABASE_URI is not configured. "
            "Set DATABASE_URL, or supply a test database via test_config in tests."
        )
    if not is_testing:
        backend = make_url(db_uri).get_backend_name()
        if backend == 'sqlite':
            raise RuntimeError(
                "SQLite is not allowed outside tests. "
                "Set DATABASE_URL to a PostgreSQL DSN before starting the app."
            )
        if app.config.get('REQUIRE_POSTGRES') and backend != 'postgresql':
            raise RuntimeError(
                f"REQUIRE_POSTGRES is enabled, but backend is '{backend}'. "
                "Set DATABASE_URL to a PostgreSQL DSN."
            )

    # ---------------------------
    # Session configuration
    # ---------------------------
    _timeout_min = int(os.environ.get('SESSION_TIMEOUT_MINUTES', '30'))
    app.config.update(
        SECRET_KEY=os.environ.get(
            'SECRET_KEY',
            'change-this-secret-key-in-production'
        ),
        SESSION_TIMEOUT_MINUTES=_timeout_min,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=_timeout_min),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=app.config.get('SESSION_COOKIE_SECURE', False),
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_REFRESH_EACH_REQUEST=True,
        SESSION_PERMANENT=False
    )

    try:
        import reportlab  # noqa: F401
    except ImportError:
        logging.getLogger(__name__).warning(
            '[EXPORT] reportlab not installed — PDF exports will use low-quality fallback'
        )

    # ---------------------------
    # Initialize extensions
    # ---------------------------
    print(f"[DB] SQLALCHEMY_DATABASE_URI={_safe_db_uri(db_uri)}")
    try:
        url = make_url(db_uri) if db_uri else None
        if url and url.get_backend_name() == "sqlite":
            sqlite_path = url.database or ""
            if sqlite_path:
                print(f"[DB] SQLite file={sqlite_path} exists={os.path.exists(sqlite_path)}")
            else:
                print("[DB] SQLite is in-memory or uses a relative path.")
        elif url:
            print(f"[DB] Backend={url.get_backend_name()} Driver={url.get_driver_name()} Host={url.host} DB={url.database}")
    except Exception:
        pass

    db.init_app(app)
    bcrypt.init_app(app)
    app.config['RATELIMIT_STORAGE_URI'] = app.config.get('REDIS_URL') or 'memory://'
    app.config.setdefault('RATELIMIT_SWALLOW_ERRORS', True)
    limiter.init_app(app)

    # ---------------------------
    # Response compression
    # ---------------------------
    if app.config.get('COMPRESS_ENABLED'):
        if Compress is not None:
            compress = Compress()
            compress.init_app(app)
        else:
            print("[WARN] Flask-Compress is not installed; compression is disabled.")

    # ---------------------------
    # Rate limit error handler
    # ---------------------------
    from flask_limiter.errors import RateLimitExceeded

    @app.errorhandler(RateLimitExceeded)
    def handle_rate_limit(e):
        from flask import render_template, request as _req
        # Build the default 429 response to extract the Retry-After header
        default_response = e.get_response()
        try:
            retry_after = int(default_response.headers.get('Retry-After', 60))
        except (TypeError, ValueError):
            retry_after = 60
        if _req.accept_mimetypes.best == 'application/json':
            from flask import jsonify
            response = jsonify({'error': 'Too many requests', 'retry_after': retry_after})
            response.status_code = 429
            response.headers['Retry-After'] = retry_after
            return response
        response = render_template('errors/429.html', retry_after=retry_after), 429
        return response

    # ---------------------------
    # Global RBAC Write Protection
    # ---------------------------
    @app.before_request
    def global_authorization_guard():
        from middleware.rbac import enforce_write_permission
        enforce_write_permission()

    # ---------------------------
    # Static cache headers
    # ---------------------------
    @app.after_request
    def _apply_cache_headers(response):
        try:
            if request.endpoint == 'static':
                path = (request.path or '').lower()
                is_asset = path.endswith('.js') or path.endswith('.css')
                if is_asset:
                    filename = path.rsplit('/', 1)[-1]
                    is_hashed = bool(re.search(r'\.[0-9a-f]{8,}\.(js|css)$', filename))
                    versioned = bool(request.args.get('v')) or is_hashed
                    if versioned:
                        max_age = int(app.config.get('STATIC_IMMUTABLE_MAX_AGE_SECONDS', 31536000))
                        response.headers['Cache-Control'] = f'public, max-age={max_age}, immutable'
                    else:
                        max_age = int(app.config.get('STATIC_MAX_AGE_SECONDS', 3600))
                        response.headers['Cache-Control'] = f'public, max-age={max_age}'
        except Exception:
            pass
        return response

    @app.after_request
    def _apply_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('X-XSS-Protection', '1; mode=block')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        # HTTP caching: stable reference data cached 60s; writes never cached.
        if 'Cache-Control' not in response.headers:
            if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
                response.headers['Cache-Control'] = 'no-store'
            elif request.path.startswith('/api/sites') or request.path.startswith('/api/departments'):
                response.headers['Cache-Control'] = 'private, max-age=60'
        return response

    # ---------------------------
    # SQLite Performance Tuning (WAL Mode + Busy Timeout)
    # ---------------------------
    from sqlalchemy import event
    with app.app_context():
        @event.listens_for(db.engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            backend = db.engine.url.get_backend_name()
            if backend == "sqlite":
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=30000") # 30s timeout
                cursor.close()
            elif backend == "postgresql":
                # Force UTC boundaries for date_trunc/day rollups.
                cursor = dbapi_connection.cursor()
                cursor.execute("SET TIME ZONE 'UTC'")
                cursor.close()

        # One-time connectivity check to confirm DB backend is reachable.
        try:
            with db.engine.connect() as conn:
                if app.config['SQLALCHEMY_DATABASE_URI'].startswith("sqlite"):
                    version = conn.exec_driver_sql("select sqlite_version()").scalar()
                    print(f"[DB] SQLite connection OK (version {version})")
                else:
                    conn.exec_driver_sql("select 1")
                    print("[DB] Database connection OK")
        except Exception as e:
            print(f"[DB] Database connection FAILED: {e}")

    # ---------------------------
    # Database setup
    # ---------------------------
    with app.app_context():
        from models import (
            User, Device, Site, Department, PrinterMetrics, PrintJobAudit,
            DashboardEvent, DailyDeviceStats, 
            DeviceInterface, InterfaceTrafficHistory, DeviceSnmpConfig,
            SwitchTopology, TrackedDevice, TrackedDeviceIpHistory,
            RemoteDeviceScanHistory,
            AuditLog,
            DeviceScanHistory, NetworkScan, PortScanResult,
            ServerHealthLog, ServerThresholdConfig, ServerMetricThresholdState,
            ServerHealthHourlyRollup, ServerHealthDailyRollup, ServerHealthRollupState,
            Subnet,
             RestrictedSitePolicy, TrackingAgentKeyBinding, RestrictedSiteEvent, RestrictedSiteAlertState,
             RestrictedSiteDomainMeta,
             DeviceIdentityLink, DeviceIdentityLinkCandidate,
             DeviceEffectivePolicyCache, PolicyRebuildTask,
             PollTask, AlertFanoutTask, TrackingSyncEnvelope, ReportExportJob,
         )
        from models.compliance_profile import ComplianceProfile
        from models.app_settings import AppSettings
        from models.alert_channel import AlertChannel
        from models.device_classification_cache import DeviceClassificationCache
        from models.discovery_config import DiscoveryConfig
        from models.device_domain_log import DeviceDomainLog
        from models.device_location_log import DeviceLocationLog
        from models.device_patch_log import DevicePatchLog
        from utils.db_migrations import (
            ensure_server_health_columns,
            ensure_tracking_stabilization_columns,
            ensure_app_settings_table,
            ensure_device_icmp_threshold_columns,
            ensure_device_scan_history_columns,
            ensure_alert_channels_table,
            ensure_domain_location_patch_tables,
        )

        from services.discovery_service import get_discovery_service
        from services.timescaledb_service import ensure_hypertables
        ds = get_discovery_service()

        if not os.environ.get('FLASK_RUN_FROM_CLI'):
            db.create_all()
            from services.startup_migrations import run_startup_migrations_bg
            run_startup_migrations_bg(app, db)
            ensure_hypertables(db.engine)
            ensure_server_health_columns()
            ensure_tracking_stabilization_columns()
            ensure_app_settings_table()
            ensure_device_icmp_threshold_columns()
            ensure_device_scan_history_columns()
            ensure_alert_channels_table()
            ensure_domain_location_patch_tables()

            # Seed AppSettings from environment variables (non-destructive).
            _smtp_seeds = [
                ('smtp_server',     'SMTP_SERVER',     'smtp', 'SMTP server hostname',          False),
                ('smtp_port',       'SMTP_PORT',       'smtp', 'SMTP server port',              False),
                ('smtp_user',       'SMTP_USERNAME',   'smtp', 'SMTP username',                 False),
                ('smtp_password',   'SMTP_PASSWORD',   'smtp', 'SMTP password (encrypted)',     True),
                ('smtp_from',       'SMTP_FROM',       'smtp', 'From address for alert emails', False),
                ('smtp_recipients', 'SMTP_RECIPIENTS', 'smtp', 'Comma-separated alert recipients', False),
                ('smtp_use_tls',    'SMTP_USE_TLS',    'smtp', 'Use TLS (true/false)',          False),
            ]
            _monitoring_seeds = [
                ('monitoring_interval_seconds', 'MONITORING_INTERVAL', 'monitoring',
                 'Device scan interval in seconds (10–3600)', False),
            ]
            for key, env_var, category, desc, is_secret in _smtp_seeds + _monitoring_seeds:
                AppSettings.seed_from_env(key, env_var, category, desc, is_secret)
    
            # ---------------------------
            # Prime Discovery Service (Singleton)
            # ---------------------------
            print(f"[OK] Discovery Service primed: {id(ds)}")
    
            # ---------------------------
            # Safe admin creation
            # ---------------------------
            admin_email = "gurvirdhanjal004@gmail.com"
            
            # Check by USERNAME, not email, to ensure we update the existing admin
            try:
                admin_user = User.query.filter_by(username="admin").first()
                
                if admin_user:
                    # Update email if it changed
                    if admin_user.email != admin_email:
                        admin_user.email = admin_email
                        db.session.commit()
                        print(f"[OK] Updated admin email to {admin_email}")
                else:
                    # Create new admin
                    try:
                        new_admin = User(
                            username="admin",
                            email=admin_email,
                            role="admin",
                            password=bcrypt.generate_password_hash("admin123").decode("utf-8"),
                            is_active=True
                        )
                        db.session.add(new_admin)
                        db.session.commit()
                        print("[OK] Default admin user created.")
                    except IntegrityError:
                        db.session.rollback()
                        print("[WARN] Admin creation failed (IntegrityError).")
            except Exception as e:
                db.session.rollback()
                print(f"[DB] Skipping admin verification due to incomplete schema: {e}")

    # ---------------------------
    # Register blueprints
    # ---------------------------
    from routes.auth import auth_bp
    from routes.devices import devices_bp
    from routes.monitoring import monitoring_bp
    from routes.scanning import scanning_bp
    from routes.reports import reports_bp
    from routes.user_management import user_management_bp
    from routes.tracking import tracking_bp
    from routes.file_transfer import file_transfer_bp
    from routes.dashboard import dashboard_bp
    from routes.snmp import snmp_bp
    from routes.service_checks import service_checks_bp
    from routes.maintenance import maintenance_bp
    from routes.switch_discovery import switch_discovery_bp
    from routes.agent import agent_bp
    from routes.server_metrics import server_metrics_bp
    from routes.discovery_settings import discovery_settings_bp
    from routes.sse import sse_bp
    from routes.sites import sites_bp
    from routes.floor_plans import floor_plans_bp
    from routes.printer import printer_bp
    from routes.departments import departments_bp
    from routes.print_jobs import print_jobs_bp
    from routes.subnets import subnets_bp
    from routes.audit import audit_bp
    from routes.device_console import device_console_bp
    from routes.device_identity_admin import device_identity_admin_bp
    from routes.config_backup import config_backup_bp
    from routes.compliance_profiles import compliance_profiles_bp
    from routes.settings import settings_bp
    from routes.alerts import alerts_bp

    from middleware.session_middleware import setup_auth_middleware

    protected_blueprints = [
        devices_bp,
        monitoring_bp,
        scanning_bp,
        reports_bp,
        user_management_bp,
        tracking_bp,
        file_transfer_bp,
        dashboard_bp,
        snmp_bp,
        service_checks_bp,
        maintenance_bp,
        switch_discovery_bp,
        agent_bp,
        server_metrics_bp,
        discovery_settings_bp,
        sse_bp,
        sites_bp,
        floor_plans_bp,
        printer_bp,
        departments_bp,
        print_jobs_bp,
        subnets_bp,
        audit_bp,
        device_console_bp,
        device_identity_admin_bp,
        config_backup_bp,
        compliance_profiles_bp,
        settings_bp,
        alerts_bp,
    ]

    for bp in protected_blueprints:
        setup_auth_middleware(bp)

    app.register_blueprint(auth_bp)
    for bp in protected_blueprints:
        app.register_blueprint(bp)

    # API v1 — uses API key auth (not session), registered separately
    from routes.api_v1 import api_v1_bp
    app.register_blueprint(api_v1_bp)

    @app.context_processor
    def inject_rbac_context():
        from middleware.rbac import get_ui_rbac_context
        return {'rbac_context': get_ui_rbac_context()}

    @app.context_processor
    def inject_asset_version():
        return {'asset_ver': _ASSET_VERSION}

    @app.get('/health')
    def health():
        backend = 'unknown'
        try:
            db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
            backend = make_url(db_uri).get_backend_name() if db_uri else 'unknown'
        except Exception:
            pass

        try:
            # Use a non-pooled connection with a short timeout so the health
            # check never blocks when the pool is temporarily exhausted under load.
            with db.engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.exec_driver_sql('select 1')
        except Exception:
            # App is still alive even if DB is momentarily busy; report degraded
            # rather than unhealthy so the container isn't restarted mid-load.
            return jsonify({
                'status': 'healthy',
                'database': 'degraded',
                'backend': backend,
            })

        return jsonify({
            'status': 'healthy',
            'database': 'reachable',
            'backend': backend,
        })

    # ---------------------------
    # Error handlers
    # ---------------------------
    def _wants_json():
        return request.path.startswith('/api/') or \
               request.accept_mimetypes.best_match(
                   ['application/json', 'text/html']
               ) == 'application/json'

    @app.errorhandler(404)
    def not_found(e):
        if _wants_json():
            return jsonify({'error': 'Not found'}), 404
        return render_template('errors/404.html'), 404

    @app.errorhandler(403)
    def forbidden(e):
        if _wants_json():
            return jsonify({'error': 'Forbidden'}), 403
        return render_template('errors/403.html'), 403

    @app.errorhandler(500)
    def server_error(e):
        if _wants_json():
            return jsonify({'error': 'Internal server error'}), 500
        return render_template('errors/500.html'), 500

    return app

# end of file
