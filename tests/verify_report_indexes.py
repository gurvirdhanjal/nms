"""
Verify required report-related indexes exist in the active database.

Usage:
    python tests/verify_report_indexes.py
"""
from pathlib import Path
import sys

from sqlalchemy import inspect

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from extensions import db


REQUIRED_INDEXED_COLUMNS = {
    'server_health_logs': ['device_id', 'timestamp', 'source'],
    'device_scan_history': ['status', 'scan_timestamp', 'device_ip'],
    'server_health_hourly_rollups': ['device_id', 'bucket_hour', 'source'],
    'server_health_daily_rollups': ['device_id', 'bucket_day', 'source'],
    'dashboard_events': ['device_id', 'timestamp', 'severity', 'resolved'],
}


def _collect_indexed_columns(inspector, table_name):
    indexed = set()

    # PK columns
    pk = inspector.get_pk_constraint(table_name) or {}
    indexed.update(pk.get('constrained_columns') or [])

    # Regular indexes
    for idx in inspector.get_indexes(table_name) or []:
        indexed.update(idx.get('column_names') or [])

    # Unique constraints
    for uq in inspector.get_unique_constraints(table_name) or []:
        indexed.update(uq.get('column_names') or [])

    return indexed


def main():
    app = create_app()
    missing = {}

    with app.app_context():
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())

        for table, required_cols in REQUIRED_INDEXED_COLUMNS.items():
            if table not in existing_tables:
                missing[table] = ['<table-missing>']
                continue

            indexed_cols = _collect_indexed_columns(inspector, table)
            missing_cols = [col for col in required_cols if col not in indexed_cols]
            if missing_cols:
                missing[table] = missing_cols

    if missing:
        print('[FAIL] Missing required report indexes:')
        for table, cols in missing.items():
            print(f'  - {table}: {", ".join(cols)}')
        raise SystemExit(1)

    print('[OK] Required report indexes are present.')


if __name__ == '__main__':
    main()
