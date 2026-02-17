"""
Migration: Create poll_tasks table.

This is an idempotent migration script — safe to run multiple times.
It checks if the table exists before creating it.

Usage:
    python scripts/run_poll_task_migration.py
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from extensions import db
from sqlalchemy import text, inspect


def run_migration():
    """Create poll_tasks table if it doesn't exist."""
    app = create_app()

    with app.app_context():
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()

        if 'poll_tasks' in existing_tables:
            print("[MIGRATION] poll_tasks table already exists. Checking indexes...")
            _ensure_indexes(inspector)
            print("[MIGRATION] Done. No changes needed.")
            return

        print("[MIGRATION] Creating poll_tasks table...")

        # Import model so SQLAlchemy knows about it
        from models.poll_task import PollTask

        # Create only the poll_tasks table (not all tables)
        PollTask.__table__.create(db.engine, checkfirst=True)

        print("[MIGRATION] poll_tasks table created successfully.")
        _verify_table(inspector)


def _ensure_indexes(inspector):
    """Verify expected indexes exist."""
    indexes = inspector.get_indexes('poll_tasks')
    index_names = {idx['name'] for idx in indexes}

    expected = {
        'idx_poll_task_device_type_status',
        'idx_poll_task_pending_queue',
    }

    missing = expected - index_names
    if missing:
        print(f"[MIGRATION] WARNING: Missing indexes: {missing}")
        print("[MIGRATION] Consider running: db.create_all() to add missing indexes.")
    else:
        print(f"[MIGRATION] All {len(expected)} composite indexes verified.")


def _verify_table(inspector):
    """Print table structure for verification."""
    # Re-inspect after creation
    inspector = inspect(db.engine)
    columns = inspector.get_columns('poll_tasks')
    indexes = inspector.get_indexes('poll_tasks')

    print(f"\n[MIGRATION] Table 'poll_tasks' — {len(columns)} columns:")
    for col in columns:
        nullable = "NULL" if col.get('nullable') else "NOT NULL"
        print(f"  {col['name']:20s} {str(col['type']):20s} {nullable}")

    print(f"\n[MIGRATION] Indexes ({len(indexes)}):")
    for idx in indexes:
        unique = "UNIQUE" if idx.get('unique') else ""
        cols = ', '.join(idx['column_names'])
        print(f"  {idx['name']:45s} ({cols}) {unique}")


if __name__ == '__main__':
    run_migration()
