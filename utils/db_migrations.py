from sqlalchemy import inspect, text
from extensions import db


def _ensure_server_health_columns(inspector=None):
    """
    Light-weight migration to add new columns to server_health_logs
    without requiring Alembic.
    """
    try:
        if inspector is None:
            inspector = inspect(db.engine)
            
        if 'server_health_logs' not in inspector.get_table_names():
            return

        existing = {col['name'] for col in inspector.get_columns('server_health_logs')}
        statements = []

        if 'network_in_bps' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN network_in_bps DOUBLE PRECISION")
        if 'network_out_bps' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN network_out_bps DOUBLE PRECISION")
        if 'source' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN source VARCHAR(20)")
        if 'os_name' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN os_name VARCHAR(100)")
        if 'os_version' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN os_version VARCHAR(255)")
        if 'os_arch' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN os_arch VARCHAR(50)")

        if not statements:
            # All server_health_logs columns exist — skip, but still run device migration
            pass
        else:
            for stmt in statements:
                db.session.execute(text(stmt))
            db.session.commit()
            print(f"[DB] Applied server_health_logs migrations: {len(statements)} columns added.")
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (server_health_logs): {exc}")

    # Migrate enhanced server metrics columns
    _ensure_enhanced_server_metrics_columns()

    # Also migrate Device table for maintenance/health columns (always runs)
    _ensure_device_maintenance_columns()

    # Migrate DeviceResourceLog for productivity metrics (New)
    _ensure_device_resource_columns()


def _ensure_enhanced_server_metrics_columns(inspector=None):
    """Add enhanced metrics columns to server_health_logs table."""
    try:
        if inspector is None:
            inspector = inspect(db.engine)

        if 'server_health_logs' not in inspector.get_table_names():
            return

        existing = {col['name'] for col in inspector.get_columns('server_health_logs')}
        statements = []

        # Load Average
        if 'load_avg_1min' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN load_avg_1min DOUBLE PRECISION")
        if 'load_avg_5min' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN load_avg_5min DOUBLE PRECISION")
        if 'load_avg_15min' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN load_avg_15min DOUBLE PRECISION")

        # Swap Memory
        if 'swap_total_mb' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN swap_total_mb DOUBLE PRECISION")
        if 'swap_used_mb' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN swap_used_mb DOUBLE PRECISION")
        if 'swap_percent' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN swap_percent DOUBLE PRECISION")

        # Disk I/O
        if 'disk_read_bytes' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_read_bytes BIGINT")
        if 'disk_write_bytes' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_write_bytes BIGINT")
        if 'disk_read_count' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_read_count BIGINT")
        if 'disk_write_count' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_write_count BIGINT")

        # Network Connections
        if 'network_connections_total' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN network_connections_total INTEGER")
        if 'network_connections_established' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN network_connections_established INTEGER")

        # Processes
        if 'process_count' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN process_count INTEGER")
        if 'zombie_count' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN zombie_count INTEGER")

        # JSON fields
        if 'top_processes' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN top_processes JSON")
        if 'alerts' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN alerts JSON")

        # Memory detail (GB)
        if 'memory_used_gb' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN memory_used_gb DOUBLE PRECISION")
        if 'memory_total_gb' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN memory_total_gb DOUBLE PRECISION")

        # Disk detail (GB)
        if 'disk_used_gb' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_used_gb DOUBLE PRECISION")
        if 'disk_free_gb' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_free_gb DOUBLE PRECISION")
        if 'disk_total_gb' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_total_gb DOUBLE PRECISION")

        if not statements:
            return

        for stmt in statements:
            db.session.execute(text(stmt))
        db.session.commit()
        print(f"[DB] Applied enhanced server metrics migrations: {len(statements)} columns added.")
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (enhanced metrics): {exc}")


def _ensure_device_maintenance_columns(inspector=None):
    """Add maintenance_mode and health_alert_strikes to Device table."""
    try:
        if inspector is None:
            inspector = inspect(db.engine)

        if 'device' not in inspector.get_table_names():
            return

        existing = {col['name'] for col in inspector.get_columns('device')}
        statements = []

        if 'maintenance_mode' not in existing:
            statements.append("ALTER TABLE device ADD COLUMN maintenance_mode BOOLEAN DEFAULT FALSE")
        if 'health_alert_strikes' not in existing:
            statements.append("ALTER TABLE device ADD COLUMN health_alert_strikes INTEGER DEFAULT 0")

        if not statements:
            return

        for stmt in statements:
            db.session.execute(text(stmt))
        db.session.commit()
        print(f"[DB] Applied device maintenance migrations: {len(statements)} columns added.")
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (device): {exc}")

def _ensure_device_resource_columns(inspector=None):
    """Add upload_kbps and download_kbps to device_resource_logs table."""
    try:
        if inspector is None:
            inspector = inspect(db.engine)

        if 'device_resource_logs' not in inspector.get_table_names():
            return

        existing = {col['name'] for col in inspector.get_columns('device_resource_logs')}
        statements = []

        if 'upload_kbps' not in existing:
            statements.append("ALTER TABLE device_resource_logs ADD COLUMN upload_kbps DOUBLE PRECISION")
        if 'download_kbps' not in existing:
            statements.append("ALTER TABLE device_resource_logs ADD COLUMN download_kbps DOUBLE PRECISION")

        if not statements:
            return

        for stmt in statements:
            db.session.execute(text(stmt))
        db.session.commit()
        print(f"[DB] Applied resource log migrations: {len(statements)} columns added.")
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (resource logs): {exc}")

def _ensure_unique_client_id_column(inspector=None):
    """Add unique_client_id to tracked_devices table."""
    try:
        if inspector is None:
            inspector = inspect(db.engine)

        if 'tracked_devices' not in inspector.get_table_names():
            return

        existing = {col['name'] for col in inspector.get_columns('tracked_devices')}
        
        if 'unique_client_id' not in existing:
            # Add column
            db.session.execute(text("ALTER TABLE tracked_devices ADD COLUMN unique_client_id VARCHAR(36)"))
            # Create index
            db.session.execute(text("CREATE UNIQUE INDEX ix_tracked_devices_unique_client_id ON tracked_devices (unique_client_id)"))
            db.session.commit()
            print("[DB] Applied migration: Added unique_client_id to tracked_devices.")
            
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (unique_client_id): {exc}")

        print(f"[DB] Migration warning (unique_client_id): {exc}")

def _ensure_tracked_device_maintenance_columns(inspector=None):
    """Add maintenance_mode to tracked_devices table."""
    try:
        if inspector is None:
            inspector = inspect(db.engine)

        if 'tracked_devices' not in inspector.get_table_names():
            return

        existing = {col['name'] for col in inspector.get_columns('tracked_devices')}
        
        if 'maintenance_mode' not in existing:
            db.session.execute(text("ALTER TABLE tracked_devices ADD COLUMN maintenance_mode BOOLEAN DEFAULT FALSE"))
            db.session.commit()
            print("[DB] Applied migration: Added maintenance_mode to tracked_devices.")
            
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (tracked_devices maintenance): {exc}")

def ensure_server_health_columns():
    """Run all schema migrations."""
    inspector = inspect(db.engine)
    _ensure_server_health_columns(inspector)
    _ensure_device_resource_columns(inspector)
    _ensure_unique_client_id_column(inspector)
    _ensure_tracked_device_maintenance_columns(inspector)
