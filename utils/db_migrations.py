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
        if 'cpu_iowait_percent' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN cpu_iowait_percent DOUBLE PRECISION")
        if 'cpu_steal_percent' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN cpu_steal_percent DOUBLE PRECISION")
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
        if 'page_faults_per_sec' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN page_faults_per_sec DOUBLE PRECISION")

        # Disk I/O
        if 'disk_read_bytes' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_read_bytes BIGINT")
        if 'disk_write_bytes' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_write_bytes BIGINT")
        if 'disk_read_count' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_read_count BIGINT")
        if 'disk_write_count' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_write_count BIGINT")
        if 'disk_read_latency_ms' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_read_latency_ms DOUBLE PRECISION")
        if 'disk_write_latency_ms' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_write_latency_ms DOUBLE PRECISION")
        if 'disk_busy_percent' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN disk_busy_percent DOUBLE PRECISION")

        # Network Connections
        if 'network_connections_total' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN network_connections_total INTEGER")
        if 'network_connections_established' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN network_connections_established INTEGER")
        if 'tcp_retransmits_delta' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN tcp_retransmits_delta BIGINT")
        if 'network_per_interface' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN network_per_interface JSON")

        # Processes
        if 'process_count' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN process_count INTEGER")
        if 'zombie_count' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN zombie_count INTEGER")
        if 'context_switches_per_sec' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN context_switches_per_sec DOUBLE PRECISION")
        if 'open_fds' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN open_fds BIGINT")
        if 'fd_limit' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN fd_limit BIGINT")
        if 'fd_percent' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN fd_percent DOUBLE PRECISION")

        # JSON fields
        if 'top_processes' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN top_processes JSON")
        if 'top_processes_cpu' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN top_processes_cpu JSON")
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


def _ensure_server_health_rollup_tables():
    """
    Create rollup tables and cursor state for server health retention.
    Uses CREATE IF NOT EXISTS so it is safe for existing databases.
    """
    statements = [
        """
        CREATE TABLE IF NOT EXISTS server_health_hourly_rollups (
            id SERIAL PRIMARY KEY,
            device_id INTEGER NOT NULL REFERENCES device(device_id) ON DELETE CASCADE,
            source VARCHAR(20) NOT NULL DEFAULT 'agent',
            bucket_hour TIMESTAMP NOT NULL,
            avg_cpu_usage DOUBLE PRECISION NULL,
            avg_memory_usage DOUBLE PRECISION NULL,
            avg_disk_usage DOUBLE PRECISION NULL,
            avg_network_in_bps DOUBLE PRECISION NULL,
            avg_network_out_bps DOUBLE PRECISION NULL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_server_health_hourly_device_source_bucket
                UNIQUE (device_id, source, bucket_hour)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS server_health_daily_rollups (
            id SERIAL PRIMARY KEY,
            device_id INTEGER NOT NULL REFERENCES device(device_id) ON DELETE CASCADE,
            source VARCHAR(20) NOT NULL DEFAULT 'agent',
            bucket_day DATE NOT NULL,
            avg_cpu_usage DOUBLE PRECISION NULL,
            avg_memory_usage DOUBLE PRECISION NULL,
            avg_disk_usage DOUBLE PRECISION NULL,
            avg_network_in_bps DOUBLE PRECISION NULL,
            avg_network_out_bps DOUBLE PRECISION NULL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_server_health_daily_device_source_bucket
                UNIQUE (device_id, source, bucket_day)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS server_health_rollup_state (
            name VARCHAR(64) PRIMARY KEY,
            rolled_until TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_server_health_hourly_bucket
            ON server_health_hourly_rollups (bucket_hour)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_server_health_daily_bucket
            ON server_health_daily_rollups (bucket_day)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_server_health_hourly_device_bucket
            ON server_health_hourly_rollups (device_id, bucket_hour)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_server_health_daily_device_bucket
            ON server_health_daily_rollups (device_id, bucket_day)
        """,
    ]

    try:
        for stmt in statements:
            db.session.execute(text(stmt))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (server health rollups): {exc}")


def _ensure_postgres_metric_indexes():
    """
    Add PostgreSQL indexes for high-volume metric tables.
    Safe for existing databases via IF NOT EXISTS.
    """
    try:
        backend = db.engine.url.get_backend_name()
        if backend != 'postgresql':
            return

        statements = [
            # server_health_logs
            """
            CREATE INDEX IF NOT EXISTS idx_server_health_source_device_id_id
            ON server_health_logs (source, device_id, id DESC)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_server_health_agent_device_timestamp
            ON server_health_logs (device_id, timestamp DESC)
            WHERE source = 'agent'
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_server_health_agent_timestamp
            ON server_health_logs (timestamp DESC)
            WHERE source = 'agent'
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_server_health_timestamp_brin
            ON server_health_logs USING BRIN (timestamp)
            """,
            # interface_traffic_history
            """
            CREATE INDEX IF NOT EXISTS idx_interface_traffic_interface_timestamp
            ON interface_traffic_history (interface_id, timestamp DESC)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_interface_traffic_timestamp_brin
            ON interface_traffic_history USING BRIN (timestamp)
            """,
            # device_interfaces
            """
            CREATE INDEX IF NOT EXISTS idx_device_interfaces_device_id
            ON device_interfaces (device_id)
            """,
        ]

        for stmt in statements:
            db.session.execute(text(stmt))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (postgres metric indexes): {exc}")


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


def _ensure_device_hardware_specs_column(inspector=None):
    """Add hardware_specs JSON column to Device table."""
    try:
        if inspector is None:
            inspector = inspect(db.engine)

        if 'device' not in inspector.get_table_names():
            return

        existing = {col['name'] for col in inspector.get_columns('device')}
        if 'hardware_specs' in existing:
            return

        backend = db.engine.url.get_backend_name()
        json_type = 'JSONB' if backend == 'postgresql' else 'JSON'
        db.session.execute(text(f"ALTER TABLE device ADD COLUMN hardware_specs {json_type}"))
        db.session.commit()
        print("[DB] Applied device migration: added hardware_specs column.")
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (device hardware_specs): {exc}")


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


def _ensure_user_ldap_columns(inspector=None):
    """
    Add LDAP-related columns to user table for existing deployments.
    Safe to run repeatedly.
    """
    try:
        if inspector is None:
            inspector = inspect(db.engine)

        if 'user' not in inspector.get_table_names():
            return

        existing = {col['name'] for col in inspector.get_columns('user')}
        backend = db.engine.url.get_backend_name()
        statements = []

        if 'auth_source' not in existing:
            statements.append('ALTER TABLE "user" ADD COLUMN auth_source VARCHAR(20) DEFAULT \'local\'')
        if 'display_name' not in existing:
            statements.append('ALTER TABLE "user" ADD COLUMN display_name VARCHAR(100)')
        if 'external_id' not in existing:
            statements.append('ALTER TABLE "user" ADD COLUMN external_id VARCHAR(100)')

        for stmt in statements:
            db.session.execute(text(stmt))

        # Backfill existing rows
        if 'auth_source' not in existing:
            db.session.execute(text('UPDATE "user" SET auth_source = \'local\' WHERE auth_source IS NULL'))

        # Commit column adds/backfill first so optional steps can't undo them.
        db.session.commit()

        # Align with current model (LDAP users may not have password/email).
        if backend == 'postgresql':
            try:
                db.session.execute(text('ALTER TABLE "user" ALTER COLUMN password DROP NOT NULL'))
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                print(f"[DB] Migration note (user.password nullable): {exc}")
            try:
                db.session.execute(text('ALTER TABLE "user" ALTER COLUMN email DROP NOT NULL'))
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                print(f"[DB] Migration note (user.email nullable): {exc}")
        if statements:
            print(f"[DB] Applied user LDAP migrations: {len(statements)} columns added.")
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (user LDAP): {exc}")

def ensure_server_health_columns():
    """Run all schema migrations."""
    inspector = inspect(db.engine)
    _ensure_server_health_columns(inspector)
    _ensure_server_health_rollup_tables()
    _ensure_postgres_metric_indexes()
    _ensure_device_hardware_specs_column(inspector)
    _ensure_device_resource_columns(inspector)
    _ensure_unique_client_id_column(inspector)
    _ensure_tracked_device_maintenance_columns(inspector)
    _ensure_user_ldap_columns(inspector)
