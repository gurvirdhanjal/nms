"""
Migration script to add peak columns to rollup tables.
Safe to run multiple times — uses IF NOT EXISTS (Postgres) or checks column existence (SQLite).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db

NEW_COLUMNS = {
    'server_health_hourly_rollups': [
        ('max_cpu_usage', 'FLOAT'),
        ('max_memory_usage', 'FLOAT'),
        ('online_samples', 'INTEGER DEFAULT 0'),
    ],
    'server_health_daily_rollups': [
        ('max_cpu_usage', 'FLOAT'),
        ('max_memory_usage', 'FLOAT'),
        ('online_samples', 'INTEGER DEFAULT 0'),
    ],
}


def column_exists_sqlite(conn, table, column):
    result = conn.exec_driver_sql(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in result)


def column_exists_postgres(conn, table, column):
    result = conn.exec_driver_sql(
        "SELECT 1 FROM information_schema.columns "
        f"WHERE table_name='{table}' AND column_name='{column}'"
    )
    return result.fetchone() is not None


def run_migration():
    app = create_app()
    with app.app_context():
        uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        is_sqlite = uri.startswith('sqlite')

        with db.engine.connect() as conn:
            for table, columns in NEW_COLUMNS.items():
                for col_name, col_type in columns:
                    if is_sqlite:
                        exists = column_exists_sqlite(conn, table, col_name)
                    else:
                        exists = column_exists_postgres(conn, table, col_name)

                    if exists:
                        print(f"  [SKIP] {table}.{col_name} already exists")
                        continue

                    sql = f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"
                    conn.exec_driver_sql(sql)
                    conn.commit()
                    print(f"  [OK]   {table}.{col_name} added")

    print("\n✅ Rollup migration complete.")


if __name__ == '__main__':
    run_migration()
