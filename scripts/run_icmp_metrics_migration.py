"""
Migration: Add ICMP metrics and alert strike columns.

Tables affected:
    - server_health_logs: + ping_latency_ms, packet_loss_pct
    - server_health_hourly_rollups: + avg/max latency, avg/max packet_loss
    - server_health_daily_rollups: + avg/max latency, avg/max packet_loss
    - device: + latency_strikes, packet_loss_strikes

Safe to re-run — uses IF NOT EXISTS / try-except per column.
Works on both PostgreSQL and SQLite.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from sqlalchemy import text, inspect


def column_exists(inspector, table_name, column_name):
    """Check if a column already exists in a table."""
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def add_column_safe(inspector, table_name, column_name, column_type, default=None):
    """Add a column if it doesn't already exist."""
    if column_exists(inspector, table_name, column_name):
        print(f"  ✓ {table_name}.{column_name} already exists — skipping")
        return False

    default_clause = f" DEFAULT {default}" if default is not None else ""
    sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}{default_clause}"
    try:
        db.session.execute(text(sql))
        print(f"  ✓ Added {table_name}.{column_name} ({column_type})")
        return True
    except Exception as e:
        print(f"  ✗ Failed to add {table_name}.{column_name}: {e}")
        db.session.rollback()
        return False


def run_migration():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        backend = db.engine.dialect.name
        float_type = 'DOUBLE PRECISION' if backend == 'postgresql' else 'REAL'
        int_type = 'INTEGER'

        print(f"Database backend: {backend}")
        print(f"Float type: {float_type}\n")

        changes = 0

        # 1. server_health_logs — ICMP raw metrics
        print("── server_health_logs ──")
        if add_column_safe(inspector, 'server_health_logs', 'ping_latency_ms', float_type):
            changes += 1
        if add_column_safe(inspector, 'server_health_logs', 'packet_loss_pct', float_type):
            changes += 1

        # 2. server_health_hourly_rollups — ICMP aggregated
        print("\n── server_health_hourly_rollups ──")
        for col in ['avg_ping_latency_ms', 'max_ping_latency_ms',
                     'avg_packet_loss_pct', 'max_packet_loss_pct']:
            if add_column_safe(inspector, 'server_health_hourly_rollups', col, float_type):
                changes += 1

        # 3. server_health_daily_rollups — ICMP aggregated
        print("\n── server_health_daily_rollups ──")
        for col in ['avg_ping_latency_ms', 'max_ping_latency_ms',
                     'avg_packet_loss_pct', 'max_packet_loss_pct']:
            if add_column_safe(inspector, 'server_health_daily_rollups', col, float_type):
                changes += 1

        # 4. device — alert strike counters
        print("\n── device ──")
        if add_column_safe(inspector, 'device', 'latency_strikes', int_type, default=0):
            changes += 1
        if add_column_safe(inspector, 'device', 'packet_loss_strikes', int_type, default=0):
            changes += 1

        if changes > 0:
            db.session.commit()
            print(f"\n✅ Migration complete — {changes} columns added")
        else:
            print("\n✅ Nothing to migrate — all columns already exist")


if __name__ == '__main__':
    run_migration()
