from sqlalchemy import inspect, text
from extensions import db


def _portable_datetime_type(backend_name=None) -> str:
    """Return a datetime column type accepted by supported backends."""
    backend = backend_name or db.engine.url.get_backend_name()
    if backend == 'postgresql':
        return 'TIMESTAMP'
    return 'DATETIME'


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

        # Uptime accuracy columns (accurate boot time + agent session tracking)
        if 'boot_time' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN boot_time TIMESTAMP")
        if 'agent_start_time' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN agent_start_time TIMESTAMP")
        if 'agent_session_id' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN agent_session_id VARCHAR(64)")
        if 'is_reboot' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN is_reboot BOOLEAN DEFAULT FALSE")
        if 'network_connections_unique_ips' not in existing:
            statements.append("ALTER TABLE server_health_logs ADD COLUMN network_connections_unique_ips INTEGER")

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


def _ensure_reporting_indexes(inspector=None):
    """
    Add indexes used heavily by reports and analytics queries.
    Safe to run repeatedly via IF NOT EXISTS.
    """
    try:
        if inspector is None:
            inspector = inspect(db.engine)

        tables = set(inspector.get_table_names())
        statements = []

        if 'dashboard_events' in tables:
            statements.extend([
                "CREATE INDEX IF NOT EXISTS idx_dashboard_events_device_timestamp ON dashboard_events (device_id, timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_dashboard_events_severity_timestamp ON dashboard_events (severity, timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_dashboard_events_resolved_timestamp ON dashboard_events (resolved, timestamp)",
            ])

        if 'server_health_logs' in tables:
            statements.extend([
                "CREATE INDEX IF NOT EXISTS idx_server_health_timestamp ON server_health_logs (timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_server_health_source_timestamp ON server_health_logs (source, timestamp)",
            ])

        if 'server_health_hourly_rollups' in tables:
            statements.extend([
                "CREATE INDEX IF NOT EXISTS idx_server_health_hourly_source_bucket ON server_health_hourly_rollups (source, bucket_hour)",
            ])

        if 'server_health_daily_rollups' in tables:
            statements.extend([
                "CREATE INDEX IF NOT EXISTS idx_server_health_daily_source_bucket ON server_health_daily_rollups (source, bucket_day)",
            ])

        if 'device_scan_history' in tables:
            statements.extend([
                "CREATE INDEX IF NOT EXISTS idx_device_scan_history_status_time ON device_scan_history (status, scan_timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_device_scan_history_ip_time ON device_scan_history (device_ip, scan_timestamp)",
            ])

        for stmt in statements:
            db.session.execute(text(stmt))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (reporting indexes): {exc}")


def _ensure_core_device_indexes_and_constraints(inspector=None):
    """
    Add defensive indexes and constraints for device/scan performance.
    Unique device_ip is applied only when existing data is clean.
    """
    try:
        if inspector is None:
            inspector = inspect(db.engine)

        tables = set(inspector.get_table_names())
        statements = []

        if 'device' in tables:
            statements.extend([
                "CREATE INDEX IF NOT EXISTS idx_device_subnet_cidr ON device (subnet_cidr)",
                "CREATE INDEX IF NOT EXISTS idx_device_device_ip ON device (device_ip)",
            ])

        if 'device_scan_history' in tables:
            statements.extend([
                "CREATE INDEX IF NOT EXISTS idx_device_scan_history_status_time ON device_scan_history (status, scan_timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_device_scan_history_ip_time ON device_scan_history (device_ip, scan_timestamp)",
            ])

        if 'poll_tasks' in tables:
            statements.append(
                "CREATE INDEX IF NOT EXISTS idx_poll_tasks_status_next_run_at ON poll_tasks (status, next_run_at)"
            )

        for stmt in statements:
            db.session.execute(text(stmt))

        # Safe unique constraint on device_ip.
        if 'device' in tables:
            dup = db.session.execute(text("""
                SELECT device_ip
                FROM device
                WHERE device_ip IS NOT NULL AND TRIM(device_ip) <> ''
                GROUP BY device_ip
                HAVING COUNT(*) > 1
                LIMIT 1
            """)).fetchone()

            if dup is None:
                db.session.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_device_device_ip ON device (device_ip)"
                ))
            else:
                print(f"[DB] Skipping unique device_ip index due to duplicates (example: {dup[0]}).")

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (core indexes/constraints): {exc}")


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
        if 'last_agent_heartbeat' not in existing:
            statements.append("ALTER TABLE device ADD COLUMN last_agent_heartbeat TIMESTAMP")
        if 'last_agent_session_id' not in existing:
            statements.append("ALTER TABLE device ADD COLUMN last_agent_session_id VARCHAR(64)")

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


def _ensure_tracking_history_tables():
    """Create tracking history tables that may not exist in old deployments."""
    try:
        from models.tracked_device import (
            TrackingSample,
            TrackingHistoryIntegrityAudit,
            TrackedDeviceAvailabilityEvent,
            TrackedDeviceIpHistory,
            TrackingHourlyRollup,
            TrackingDailyRollup,
        )

        TrackingSample.__table__.create(bind=db.engine, checkfirst=True)
        TrackingHistoryIntegrityAudit.__table__.create(bind=db.engine, checkfirst=True)
        TrackedDeviceAvailabilityEvent.__table__.create(bind=db.engine, checkfirst=True)
        TrackedDeviceIpHistory.__table__.create(bind=db.engine, checkfirst=True)
        TrackingHourlyRollup.__table__.create(bind=db.engine, checkfirst=True)
        TrackingDailyRollup.__table__.create(bind=db.engine, checkfirst=True)
    except Exception as exc:
        print(f"[DB] Migration warning (tracking history tables): {exc}")


def _ensure_tracking_history_columns_and_indexes(inspector=None):
    """Add tracking-history hardening columns and indexes for existing databases."""
    try:
        if inspector is None:
            inspector = inspect(db.engine)

        tables = set(inspector.get_table_names())
        statements = []

        if 'tracked_devices' in tables:
            tracked_cols = {col['name'] for col in inspector.get_columns('tracked_devices')}
            if 'is_archived' not in tracked_cols:
                statements.append("ALTER TABLE tracked_devices ADD COLUMN is_archived BOOLEAN DEFAULT FALSE")
            if 'archived_at' not in tracked_cols:
                statements.append("ALTER TABLE tracked_devices ADD COLUMN archived_at TIMESTAMP")
            if 'archived_reason' not in tracked_cols:
                statements.append("ALTER TABLE tracked_devices ADD COLUMN archived_reason TEXT")
            if 'archived_by' not in tracked_cols:
                statements.append("ALTER TABLE tracked_devices ADD COLUMN archived_by VARCHAR(100)")
            if 'site_id' not in tracked_cols:
                statements.append("ALTER TABLE tracked_devices ADD COLUMN site_id INTEGER")
            if 'department_id' not in tracked_cols:
                statements.append("ALTER TABLE tracked_devices ADD COLUMN department_id INTEGER")
            if 'last_agent_sync_at' not in tracked_cols:
                statements.append("ALTER TABLE tracked_devices ADD COLUMN last_agent_sync_at TIMESTAMP")
            if 'last_agent_sync_ip' not in tracked_cols:
                statements.append("ALTER TABLE tracked_devices ADD COLUMN last_agent_sync_ip VARCHAR(45)")

        if 'tracking_samples' in tables:
            ts_cols = {col['name'] for col in inspector.get_columns('tracking_samples')}
            if 'integrity_status' not in ts_cols:
                statements.append(
                    "ALTER TABLE tracking_samples ADD COLUMN integrity_status VARCHAR(20) NOT NULL DEFAULT 'verified'"
                )
            if 'integrity_notes' not in ts_cols:
                statements.append("ALTER TABLE tracking_samples ADD COLUMN integrity_notes TEXT")
            if 'received_minute_bucket' not in ts_cols:
                statements.append("ALTER TABLE tracking_samples ADD COLUMN received_minute_bucket TIMESTAMP")
            if 'payload_hash' not in ts_cols:
                statements.append("ALTER TABLE tracking_samples ADD COLUMN payload_hash VARCHAR(64)")
            if 'previous_sample_id' not in ts_cols:
                statements.append("ALTER TABLE tracking_samples ADD COLUMN previous_sample_id INTEGER")

        for table_name in ('device_activity_logs', 'device_resource_logs', 'device_application_logs'):
            if table_name not in tables:
                continue
            cols = {col['name'] for col in inspector.get_columns(table_name)}
            if 'sample_id' not in cols:
                statements.append(f"ALTER TABLE {table_name} ADD COLUMN sample_id INTEGER")

        if statements:
            for stmt in statements:
                db.session.execute(text(stmt))
            db.session.commit()
            print(f"[DB] Applied tracking history column migrations: {len(statements)} change(s).")
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (tracking history columns): {exc}")

    try:
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        index_statements = []

        if 'tracked_devices' in tables:
            index_statements.append(
                "CREATE INDEX IF NOT EXISTS idx_tracked_devices_is_archived ON tracked_devices (is_archived)"
            )
            index_statements.append(
                "CREATE INDEX IF NOT EXISTS idx_tracked_devices_site_id ON tracked_devices (site_id)"
            )
            index_statements.append(
                "CREATE INDEX IF NOT EXISTS idx_tracked_devices_department_id ON tracked_devices (department_id)"
            )
            index_statements.append(
                "CREATE INDEX IF NOT EXISTS idx_tracked_devices_last_agent_sync_at ON tracked_devices (last_agent_sync_at)"
            )

        if 'tracking_samples' in tables:
            index_statements.extend([
                "CREATE INDEX IF NOT EXISTS idx_tracking_samples_device_sampled_id ON tracking_samples (device_id, sampled_at DESC, id DESC)",
                "CREATE INDEX IF NOT EXISTS idx_tracking_samples_device_received_id ON tracking_samples (device_id, received_at DESC, id DESC)",
                "CREATE INDEX IF NOT EXISTS idx_tracking_samples_source_received ON tracking_samples (source, received_at DESC)",
                "CREATE INDEX IF NOT EXISTS idx_tracking_samples_received_minute_bucket ON tracking_samples (received_minute_bucket)",
            ])

        if 'device_activity_logs' in tables:
            index_statements.extend([
                "CREATE INDEX IF NOT EXISTS idx_device_activity_logs_device_ts_id ON device_activity_logs (device_id, timestamp DESC, id DESC)",
                "CREATE INDEX IF NOT EXISTS idx_device_activity_logs_sample_id ON device_activity_logs (sample_id)",
            ])
        if 'device_resource_logs' in tables:
            index_statements.extend([
                "CREATE INDEX IF NOT EXISTS idx_device_resource_logs_device_ts_id ON device_resource_logs (device_id, timestamp DESC, id DESC)",
                "CREATE INDEX IF NOT EXISTS idx_device_resource_logs_sample_id ON device_resource_logs (sample_id)",
            ])
        if 'device_application_logs' in tables:
            index_statements.extend([
                "CREATE INDEX IF NOT EXISTS idx_device_application_logs_device_ts_id ON device_application_logs (device_id, timestamp DESC, id DESC)",
                "CREATE INDEX IF NOT EXISTS idx_device_application_logs_sample_id ON device_application_logs (sample_id)",
            ])
        if 'tracked_device_availability_events' in tables:
            index_statements.extend([
                "CREATE INDEX IF NOT EXISTS idx_tracking_availability_device_observed_id ON tracked_device_availability_events (device_id, observed_at DESC, id DESC)",
                "CREATE INDEX IF NOT EXISTS idx_tracking_availability_device_status_observed ON tracked_device_availability_events (device_id, status, observed_at DESC)",
                "CREATE INDEX IF NOT EXISTS idx_tracking_availability_observed_at ON tracked_device_availability_events (observed_at DESC)",
            ])
        if 'tracked_device_ip_history' in tables:
            index_statements.extend([
                "CREATE INDEX IF NOT EXISTS idx_tracking_ip_history_device_changed ON tracked_device_ip_history (device_id, changed_at_utc DESC)",
                "CREATE INDEX IF NOT EXISTS idx_tracking_ip_history_agent_key_changed ON tracked_device_ip_history (agent_key_id, changed_at_utc DESC)",
            ])

        backend = db.engine.url.get_backend_name()
        if backend == 'postgresql':
            if 'device_activity_logs' in tables:
                index_statements.append(
                    "CREATE INDEX IF NOT EXISTS idx_device_activity_logs_timestamp_brin ON device_activity_logs USING BRIN (timestamp)"
                )
            if 'device_resource_logs' in tables:
                index_statements.append(
                    "CREATE INDEX IF NOT EXISTS idx_device_resource_logs_timestamp_brin ON device_resource_logs USING BRIN (timestamp)"
                )
            if 'device_application_logs' in tables:
                index_statements.append(
                    "CREATE INDEX IF NOT EXISTS idx_device_application_logs_timestamp_brin ON device_application_logs USING BRIN (timestamp)"
                )

        for stmt in index_statements:
            db.session.execute(text(stmt))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (tracking history indexes): {exc}")


def _ensure_user_ldap_columns(inspector=None):
    """
    Add missing user columns for existing deployments.
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
        index_statements = []

        def _add_user_column(definition: str) -> str:
            if backend == 'postgresql':
                return f'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS {definition}'
            return f'ALTER TABLE "user" ADD COLUMN {definition}'

        if 'last_login' not in existing:
            statements.append(_add_user_column(f'last_login {_portable_datetime_type()}'))
        if 'created_by' not in existing:
            statements.append(_add_user_column('created_by VARCHAR(80)'))

        if 'auth_source' not in existing:
            statements.append(_add_user_column('auth_source VARCHAR(20) DEFAULT \'local\''))
        if 'display_name' not in existing:
            statements.append(_add_user_column('display_name VARCHAR(100)'))
        if 'external_id' not in existing:
            statements.append(_add_user_column('external_id VARCHAR(100)'))
        if 'site_id' not in existing:
            statements.append(_add_user_column('site_id INTEGER'))
        if 'department_id' not in existing:
            statements.append(_add_user_column('department_id INTEGER'))

        index_statements.extend([
            'CREATE INDEX IF NOT EXISTS ix_user_site_id ON "user" (site_id)',
            'CREATE INDEX IF NOT EXISTS ix_user_department_id ON "user" (department_id)',
        ])

        for stmt in statements:
            db.session.execute(text(stmt))

        # Backfill existing rows
        if 'auth_source' not in existing:
            db.session.execute(text('UPDATE "user" SET auth_source = \'local\' WHERE auth_source IS NULL'))

        for stmt in index_statements:
            db.session.execute(text(stmt))

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
            print(f"[DB] Applied user schema migrations: {len(statements)} columns added.")
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (user schema): {exc}")


def _ensure_scope_metadata_columns(inspector=None):
    """Backfill metadata columns for sites/departments on upgraded databases."""
    try:
        if inspector is None:
            inspector = inspect(db.engine)

        backend = db.engine.url.get_backend_name()

        def _add_scope_column(table_name: str, definition: str) -> str:
            if backend == 'postgresql':
                return f'ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {definition}'
            return f'ALTER TABLE {table_name} ADD COLUMN {definition}'

        table_column_map = {
            'sites': [
                ('created_by', _add_scope_column('sites', 'created_by VARCHAR(80)')),
            ],
            'departments': [
                ('created_by', _add_scope_column('departments', 'created_by VARCHAR(80)')),
            ],
        }

        total_added = 0
        for table_name, definitions in table_column_map.items():
            if table_name not in inspector.get_table_names():
                continue
            existing = {col['name'] for col in inspector.get_columns(table_name)}
            statements = [statement for column_name, statement in definitions if column_name not in existing]
            for stmt in statements:
                db.session.execute(text(stmt))
            if statements:
                total_added += len(statements)

        db.session.commit()
        if total_added:
            print(f"[DB] Applied scope metadata migrations: {total_added} columns added.")
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (scope metadata): {exc}")


def _ensure_restricted_site_tables():
    """Create restricted-site policy, event, and key-binding tables."""
    try:
        from models.restricted_site_policy import (
            RestrictedSiteAlertState,
            RestrictedSiteEvent,
            RestrictedSitePolicy,
            TrackingAgentKeyBinding,
        )

        RestrictedSitePolicy.__table__.create(bind=db.engine, checkfirst=True)
        TrackingAgentKeyBinding.__table__.create(bind=db.engine, checkfirst=True)
        RestrictedSiteEvent.__table__.create(bind=db.engine, checkfirst=True)
        RestrictedSiteAlertState.__table__.create(bind=db.engine, checkfirst=True)

        policy = RestrictedSitePolicy.query.get(1)
        if policy is None:
            policy = RestrictedSitePolicy(id=1)
            policy.apply_domains([])
            policy.recompute_version()
            db.session.add(policy)
            db.session.commit()
        elif not policy.policy_version:
            policy.recompute_version()
            db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (restricted-site tables): {exc}")


def _ensure_tracking_stabilization_columns(inspector=None):
    try:
        if inspector is None:
            inspector = inspect(db.engine)

        if 'tracked_devices' in inspector.get_table_names():
            existing = {col['name'] for col in inspector.get_columns('tracked_devices')}
            statements = []
            if 'last_policy_version_seen' not in existing:
                statements.append('ALTER TABLE tracked_devices ADD COLUMN last_policy_version_seen VARCHAR(128)')
            if 'last_policy_sync_at' not in existing:
                statements.append(f'ALTER TABLE tracked_devices ADD COLUMN last_policy_sync_at {_portable_datetime_type()}')
            for stmt in statements:
                db.session.execute(text(stmt))
            if statements:
                db.session.commit()
                print(f"[DB] Applied tracking stabilization migrations: {len(statements)} tracked_devices columns added.")
            else:
                db.session.rollback()

        from models.alert_fanout_task import AlertFanoutTask
        from models.device_effective_policy_cache import DeviceEffectivePolicyCache
        from models.device_identity_link import DeviceIdentityLink
        from models.device_identity_link_candidate import DeviceIdentityLinkCandidate
        from models.policy_rebuild_task import PolicyRebuildTask
        from models.tracking_sync_envelope import TrackingSyncEnvelope

        DeviceIdentityLink.__table__.create(bind=db.engine, checkfirst=True)
        DeviceIdentityLinkCandidate.__table__.create(bind=db.engine, checkfirst=True)
        DeviceEffectivePolicyCache.__table__.create(bind=db.engine, checkfirst=True)
        PolicyRebuildTask.__table__.create(bind=db.engine, checkfirst=True)
        AlertFanoutTask.__table__.create(bind=db.engine, checkfirst=True)
        TrackingSyncEnvelope.__table__.create(bind=db.engine, checkfirst=True)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (tracking stabilization): {exc}")


def _ensure_server_threshold_tables():
    try:
        from models.server_metric_threshold_state import ServerMetricThresholdState
        from models.server_threshold_config import ServerThresholdConfig
        from services.server_thresholds import build_default_thresholds

        ServerThresholdConfig.__table__.create(bind=db.engine, checkfirst=True)
        ServerMetricThresholdState.__table__.create(bind=db.engine, checkfirst=True)

        config_row = db.session.get(ServerThresholdConfig, 1)
        if config_row is None:
            db.session.add(
                ServerThresholdConfig(
                    id=1,
                    version=1,
                    thresholds_json=build_default_thresholds(),
                )
            )
        else:
            changed = False
            if not int(getattr(config_row, "version", 0) or 0):
                config_row.version = 1
                changed = True
            if not isinstance(getattr(config_row, "thresholds_json", None), dict) or "metrics" not in (config_row.thresholds_json or {}):
                config_row.thresholds_json = build_default_thresholds()
                changed = True
            if changed:
                db.session.add(config_row)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (server threshold tables): {exc}")


def ensure_report_export_job_tables():
    try:
        from models.report_export_job import ReportExportJob

        ReportExportJob.__table__.create(bind=db.engine, checkfirst=True)
        statements = [
            "CREATE INDEX IF NOT EXISTS ix_report_export_jobs_owner_created ON report_export_jobs (owner_key, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_report_export_jobs_status_created ON report_export_jobs (status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_report_export_jobs_report_created ON report_export_jobs (report_type, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_report_export_jobs_scope_created ON report_export_jobs (scope_type, scope_id, created_at)",
        ]
        for stmt in statements:
            db.session.execute(text(stmt))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (report export jobs): {exc}")


def _ensure_device_ip_nullable():
    """
    Drop the NOT NULL constraint on device.device_ip so that stale IP-scan-only
    records can have their IP cleared when a MAC-identified device claims the address.
    Safe to run multiple times — skipped when the column is already nullable.
    PostgreSQL only; SQLite does not enforce NOT NULL in the same way.
    """
    try:
        backend = db.engine.url.get_backend_name()
        if backend != 'postgresql':
            return
        inspector = inspect(db.engine)
        if 'device' not in inspector.get_table_names():
            return
        cols = {c['name']: c for c in inspector.get_columns('device')}
        col = cols.get('device_ip')
        if col is None or col.get('nullable'):
            return  # already nullable or missing — nothing to do
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE device ALTER COLUMN device_ip DROP NOT NULL"))
        print("[DB] device.device_ip is now nullable (stale IP-scan record support).")
    except Exception as exc:
        print(f"[DB] Migration warning (device_ip nullable): {exc}")


def _widen_snmp_community_column():
    """
    Widen device.snmp_community from VARCHAR(100) to VARCHAR(200) to accommodate
    Fernet-encrypted tokens (enc: prefix + ~136 bytes of base64 token).
    Safe to run multiple times — only executes when the column is still narrow.
    """
    try:
        backend = db.engine.url.get_backend_name()
        inspector = inspect(db.engine)
        if 'device' not in inspector.get_table_names():
            return
        cols = {c['name']: c for c in inspector.get_columns('device')}
        col = cols.get('snmp_community')
        if col is None:
            return
        # Check current length; skip if already wide enough
        current_length = getattr(col['type'], 'length', None)
        if current_length is not None and current_length >= 200:
            return
        if backend == 'postgresql':
            sql = "ALTER TABLE device ALTER COLUMN snmp_community TYPE VARCHAR(200)"
        else:
            # SQLite does not support ALTER COLUMN type — no-op (VARCHAR limit is unenforced)
            return
        with db.engine.begin() as conn:
            conn.execute(text(sql))
        print("[DB] Widened device.snmp_community to VARCHAR(200) for Fernet encryption.")
    except Exception as exc:
        print(f"[DB] Migration warning (snmp_community widen): {exc}")


def _ensure_compliance_profile_tables(inspector=None):
    """
    Create compliance_profiles table, then add compliance_profile_id FK to device.

    Run order matters: compliance_profiles must exist before the FK column is added.
    Safe to call on existing databases (checks before altering).
    """
    try:
        from models.compliance_profile import ComplianceProfile

        # Step 1 — create compliance_profiles if absent
        ComplianceProfile.__table__.create(bind=db.engine, checkfirst=True)

        # Step 2 — add compliance_profile_id column to device if absent
        if inspector is None:
            inspector = inspect(db.engine)

        if 'device' in inspector.get_table_names():
            existing = {col['name'] for col in inspector.get_columns('device')}
            if 'compliance_profile_id' not in existing:
                db.session.execute(text(
                    "ALTER TABLE device ADD COLUMN compliance_profile_id INTEGER "
                    "REFERENCES compliance_profiles(id) ON DELETE SET NULL"
                ))
                db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (compliance_profiles): {exc}")


def _ensure_device_config_snapshot_table():
    """Create device_config_snapshots table and its composite index if absent."""
    try:
        from models.config_snapshot import DeviceConfigSnapshot

        DeviceConfigSnapshot.__table__.create(bind=db.engine, checkfirst=True)

        # Composite index for history queries (device_id, captured_at DESC).
        # Created separately so it can be added to existing tables that were
        # created before this migration ran.
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_device_config_snapshots_device_captured "
            "ON device_config_snapshots (device_id, captured_at DESC)"
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (device_config_snapshots): {exc}")


def _ensure_activity_log_current_app_column(inspector=None):
    """Add current_application column to device_activity_logs (Option A — avoids JSON parsing)."""
    try:
        if inspector is None:
            inspector = inspect(db.engine)
        if 'device_activity_logs' not in inspector.get_table_names():
            return
        existing = {col['name'] for col in inspector.get_columns('device_activity_logs')}
        if 'current_application' not in existing:
            db.session.execute(text(
                "ALTER TABLE device_activity_logs ADD COLUMN current_application TEXT"
            ))
            db.session.commit()
            print("[DB] Added current_application column to device_activity_logs.")
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (device_activity_logs.current_application): {exc}")


def _ensure_behavioral_indexes():
    """
    Composite indexes for behavioral reporting and live-view queries.
    All indexes use IF NOT EXISTS — safe to run on startup every time.
    """
    stmts = [
        # Rollup tables — range queries by device + time bucket
        ("CREATE INDEX IF NOT EXISTS ix_tracking_daily_rollups_device_day "
         "ON tracking_daily_rollups (device_id, bucket_day)"),
        ("CREATE INDEX IF NOT EXISTS ix_tracking_hourly_rollups_device_hour "
         "ON tracking_hourly_rollups (device_id, bucket_hour)"),
        # App + activity logs — used in behavioral metrics and focus score
        ("CREATE INDEX IF NOT EXISTS ix_device_application_logs_device_ts "
         "ON device_application_logs (device_id, timestamp)"),
        ("CREATE INDEX IF NOT EXISTS ix_device_activity_logs_device_ts "
         "ON device_activity_logs (device_id, timestamp)"),
        # Restricted site events — queried by agent_key_id + time range
        ("CREATE INDEX IF NOT EXISTS ix_restricted_site_events_agent_key_ts "
         "ON restricted_site_events (agent_key_id, observed_at_utc)"),
    ]
    try:
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        table_map = {
            "tracking_daily_rollups": stmts[0],
            "tracking_hourly_rollups": stmts[1],
            "device_application_logs": stmts[2],
            "device_activity_logs": stmts[3],
            "restricted_site_events": stmts[4],
        }
        applied = 0
        for table, stmt in table_map.items():
            if table in tables:
                db.session.execute(text(stmt))
                applied += 1
        db.session.commit()
        if applied:
            print(f"[DB] Applied behavioral index guards ({applied} tables).")
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (behavioral indexes): {exc}")


def _ensure_typed_text_policy_alert_table():
    """Create typed_text_policy_alerts table and its indexes if absent."""
    try:
        from models.typed_text_policy_alert import TypedTextPolicyAlert
        TypedTextPolicyAlert.__table__.create(bind=db.engine, checkfirst=True)
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_typed_text_policy_alerts_device_ts "
            "ON typed_text_policy_alerts (device_id, detected_at)"
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (typed_text_policy_alerts): {exc}")


def _ensure_app_category_cache_table():
    """Create app_category_cache table if absent."""
    try:
        from models.app_category_cache import AppCategoryCache
        AppCategoryCache.__table__.create(bind=db.engine, checkfirst=True)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (app_category_cache): {exc}")


def _ensure_device_classification_cache_table():
    """Create device_classification_cache table if absent (idempotent)."""
    try:
        from models.device_classification_cache import DeviceClassificationCache
        DeviceClassificationCache.__table__.create(bind=db.engine, checkfirst=True)
        db.session.commit()
        print("[DB] device_classification_cache table verified.")
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (device_classification_cache): {exc}")


def _ensure_performance_indexes():
    """
    Composite and partial indexes for high-frequency query paths.
    All use IF NOT EXISTS — idempotent, safe on every startup.
    PostgreSQL-only; returns immediately on SQLite.
    """
    try:
        backend = db.engine.url.get_backend_name()
        if backend != 'postgresql':
            return

        statements = [
            # dashboard_events: covers GROUP BY severity query + alert list queries
            # (device_id IN (...), resolved=false, GROUP BY severity)
            """
            CREATE INDEX IF NOT EXISTS idx_dashboard_events_device_sev_res_ts
            ON dashboard_events (device_id, severity, resolved, timestamp DESC)
            """,
            # device: partial index for server device filter — only active 'server' rows
            """
            CREATE INDEX IF NOT EXISTS idx_device_type_active
            ON device (device_type, is_active)
            WHERE is_active = true
            """,
            # device: partial index for maintenance bulk UPDATE WHERE maintenance_mode=true
            # Typically 0–3 rows — near-zero storage, near-zero scan cost
            """
            CREATE INDEX IF NOT EXISTS idx_device_maintenance_mode
            ON device (device_id)
            WHERE maintenance_mode = true
            """,
            # device: compliance_profile_id FK is unindexed — AlertManager queries it per health check
            """
            CREATE INDEX IF NOT EXISTS idx_device_compliance_profile_id
            ON device (compliance_profile_id)
            WHERE compliance_profile_id IS NOT NULL
            """,
            # port_scan_result: no indexes at all — add composite for IP + time lookups
            """
            CREATE INDEX IF NOT EXISTS idx_port_scan_result_ip_ts
            ON port_scan_result (device_ip, scan_timestamp DESC)
            """,
            # audit_logs: composite for combined filter + ORDER BY timestamp DESC
            """
            CREATE INDEX IF NOT EXISTS idx_audit_logs_ts_entity_action
            ON audit_logs (timestamp DESC, entity_type, action)
            """,
            # device_scan_history_remote: no indexes on this table
            """
            CREATE INDEX IF NOT EXISTS idx_device_scan_history_remote_mac_ts
            ON device_scan_history_remote (mac_address, scan_timestamp DESC)
            """,
        ]

        for stmt in statements:
            db.session.execute(text(stmt))
        db.session.commit()
        print(f"[DB] Performance indexes applied ({len(statements)} statements).")

    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (performance indexes): {exc}")


def ensure_server_health_columns():
    """Run all schema migrations."""
    inspector = inspect(db.engine)
    _ensure_server_health_columns(inspector)
    _ensure_server_health_rollup_tables()
    _ensure_postgres_metric_indexes()
    _ensure_reporting_indexes(inspector)
    _ensure_core_device_indexes_and_constraints(inspector)
    _ensure_device_hardware_specs_column(inspector)
    _ensure_device_resource_columns(inspector)
    _ensure_unique_client_id_column(inspector)
    _ensure_tracked_device_maintenance_columns(inspector)
    _ensure_tracking_history_tables()
    _ensure_tracking_history_columns_and_indexes(inspector)
    _ensure_restricted_site_tables()
    _ensure_server_threshold_tables()
    ensure_report_export_job_tables()
    _ensure_scope_metadata_columns(inspector)
    _ensure_user_ldap_columns(inspector)
    _widen_snmp_community_column()
    _ensure_device_ip_nullable()
    _ensure_compliance_profile_tables()
    _ensure_device_config_snapshot_table()
    _ensure_activity_log_current_app_column(inspector)
    _ensure_behavioral_indexes()
    _ensure_typed_text_policy_alert_table()
    _ensure_app_category_cache_table()
    _ensure_device_classification_cache_table()
    _ensure_performance_indexes()


def ensure_tracking_stabilization_columns():
    inspector = inspect(db.engine)
    _ensure_tracking_stabilization_columns(inspector)
