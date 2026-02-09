from sqlalchemy import inspect, text
from extensions import db


def ensure_server_health_columns():
    """
    Light-weight migration to add new columns to server_health_logs
    without requiring Alembic.
    """
    try:
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
            return

        for stmt in statements:
            db.session.execute(text(stmt))
        db.session.commit()
        print(f"[DB] Applied server_health_logs migrations: {len(statements)} columns added.")
    except Exception as exc:
        db.session.rollback()
        print(f"[DB] Migration warning (server_health_logs): {exc}")
