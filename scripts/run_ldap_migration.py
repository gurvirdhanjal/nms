"""
Migration: Add LDAP-related columns to user table.

Columns added:
    - auth_source (VARCHAR 20, default 'local')
    - display_name (VARCHAR 100, nullable)
    - external_id (VARCHAR 100, nullable)

Also relaxes NOT NULL on password and email for LDAP users.

Safe to re-run — checks column existence before adding.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from sqlalchemy import text, inspect


def column_exists(inspector, table_name, column_name):
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def add_column_safe(inspector, table_name, column_name, column_type, default=None):
    if column_exists(inspector, table_name, column_name):
        print(f"  ✓ {table_name}.{column_name} already exists — skipping")
        return False

    default_clause = f" DEFAULT '{default}'" if default is not None else ""
    sql = f"ALTER TABLE \"{table_name}\" ADD COLUMN {column_name} {column_type}{default_clause}"
    try:
        db.session.execute(text(sql))
        print(f"  ✓ Added {table_name}.{column_name} ({column_type})")
        return True
    except Exception as e:
        print(f"  ✗ Failed to add {table_name}.{column_name}: {e}")
        db.session.rollback()
        return False


def relax_not_null(table_name, column_name, backend):
    """Make a column nullable (PostgreSQL only — SQLite doesn't support ALTER COLUMN)."""
    if backend == 'postgresql':
        try:
            db.session.execute(text(
                f'ALTER TABLE "{table_name}" ALTER COLUMN {column_name} DROP NOT NULL'
            ))
            print(f"  ✓ Relaxed NOT NULL on {table_name}.{column_name}")
            return True
        except Exception as e:
            print(f"  ⚠ Could not relax NOT NULL on {table_name}.{column_name}: {e}")
            db.session.rollback()
            return False
    else:
        print(f"  ⚠ Skipping NOT NULL relaxation on SQLite for {table_name}.{column_name} (not supported)")
        return False


def run_migration():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        backend = db.engine.dialect.name
        varchar20 = 'VARCHAR(20)' if backend == 'postgresql' else 'VARCHAR(20)'
        varchar100 = 'VARCHAR(100)' if backend == 'postgresql' else 'VARCHAR(100)'

        print(f"Database backend: {backend}\n")

        changes = 0

        print("── user ──")
        if add_column_safe(inspector, 'user', 'auth_source', varchar20, default='local'):
            changes += 1
        if add_column_safe(inspector, 'user', 'display_name', varchar100):
            changes += 1
        if add_column_safe(inspector, 'user', 'external_id', varchar100):
            changes += 1

        # Relax NOT NULL on password and email for LDAP users
        if backend == 'postgresql':
            print("\n── Relaxing NOT NULL constraints ──")
            if relax_not_null('user', 'password', backend):
                changes += 1
            if relax_not_null('user', 'email', backend):
                changes += 1

        # Set auth_source='local' for existing users that don't have it
        try:
            result = db.session.execute(text(
                "UPDATE \"user\" SET auth_source = 'local' WHERE auth_source IS NULL"
            ))
            if result.rowcount > 0:
                print(f"\n  ✓ Set auth_source='local' for {result.rowcount} existing user(s)")
        except Exception:
            pass

        if changes > 0:
            db.session.commit()
            print(f"\n✅ Migration complete — {changes} changes applied")
        else:
            db.session.commit()
            print("\n✅ Nothing to migrate — all columns already exist")


if __name__ == '__main__':
    run_migration()
