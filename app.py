import os
import re
import threading
import webbrowser
from datetime import timedelta

from flask import Flask, request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine.url import make_url

from config import Config
from extensions import db, bcrypt

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


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)

    if test_config:
        app.config.update(test_config)

    # Ensure production does not pay template reload overhead.
    if app.config.get('IS_PRODUCTION'):
        app.config['TEMPLATES_AUTO_RELOAD'] = False
    app.jinja_env.auto_reload = app.config.get('TEMPLATES_AUTO_RELOAD', False)

    # Optional hard-enforcement for production deployments.
    if app.config.get('REQUIRE_POSTGRES'):
        backend = make_url(app.config.get('SQLALCHEMY_DATABASE_URI', '')).get_backend_name()
        if backend != 'postgresql':
            raise RuntimeError(
                f"REQUIRE_POSTGRES is enabled, but backend is '{backend}'. "
                "Set DATABASE_URL to a PostgreSQL DSN."
            )

    # ---------------------------
    # Session configuration
    # ---------------------------
    app.config.update(
        SECRET_KEY=os.environ.get(
            'SECRET_KEY',
            'change-this-secret-key-in-production'
        ),
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=5),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=False,   # True only if HTTPS
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_REFRESH_EACH_REQUEST=True,
        SESSION_PERMANENT=False
    )

    # ---------------------------
    # Initialize extensions
    # ---------------------------
    db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
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
            User, Device, DashboardEvent, DailyDeviceStats, 
            DeviceInterface, InterfaceTrafficHistory, DeviceSnmpConfig,
            SwitchTopology, TrackedDevice,
            DeviceScanHistory, NetworkScan, PortScanResult,
            ServerHealthLog, ServerHealthHourlyRollup, ServerHealthDailyRollup, ServerHealthRollupState
        )
        from models.discovery_config import DiscoveryConfig
        from utils.db_migrations import ensure_server_health_columns

        db.create_all()
        ensure_server_health_columns()

        # ---------------------------
        # Prime Discovery Service (Singleton)
        # ---------------------------
        from services.discovery_service import get_discovery_service
        ds = get_discovery_service()
        print(f"[OK] Discovery Service primed: {id(ds)}")

        # ---------------------------
        # Safe admin creation
        # ---------------------------
        admin_email = "gurvirdhanjal004@gmail.com"

        admin_email = "gurvirdhanjal004@gmail.com"
        
        # Check by USERNAME, not email, to ensure we update the existing admin
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
    ]

    for bp in protected_blueprints:
        setup_auth_middleware(bp)

    app.register_blueprint(auth_bp)
    for bp in protected_blueprints:
        app.register_blueprint(bp)

    return app


# ---------------------------
# Scheduler setup
# ---------------------------
from services.scheduler import MonitoringScheduler


def open_browser():
    webbrowser.open_new("http://localhost:5001")


# ---------------------------
# Main entry point
# ---------------------------
if __name__ == "__main__":
    try:
        app = create_app()
        scheduler = MonitoringScheduler(app)
        from services.interface_poller import interface_poller
        
        print("Starting Device Monitoring System...")
        print("Access URL: http://localhost:5001")
        print("Default admin: admin / admin123")



        scheduler.start_scheduled_monitoring()
        interface_poller.start_polling(app)

        if os.environ.get("DISABLE_BROWSER_OPEN", "0") != "1":
            threading.Timer(2.0, open_browser).start()



        # Hydrate collector with DB history
        from routes.monitoring import monitor
        monitor.hydrate_collector(app)

        app.run(
            host="0.0.0.0",
            port=5001,
            debug=False,          # ❗ NEVER TRUE IN EXE
            use_reloader=False
        )

    except KeyboardInterrupt:
        print("Shutting down...")
    except Exception as e:
        print(f"Startup error: {e}")
    finally:
        if 'scheduler' in locals():
            scheduler.stop_scheduled_monitoring()
            print("Scheduler stopped.")
        if 'interface_poller' in locals():
            interface_poller.stop_polling()
