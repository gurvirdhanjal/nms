import os
import sys

# Add the project directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from extensions import db
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate_snapshot_table():
    """
    Creates the dashboard_snapshot table directly using SQL execution.
    This avoids complex Alembic migrations for a simple schema addition.
    """
    app = create_app()
    with app.app_context():
        # Get the database URI dialect
        dialect = db.engine.dialect.name
        
        logger.info(f"Targeting database dialect: {dialect}")
        
        table_exists = False
        try:
            if dialect == 'postgresql':
                result = db.session.execute(db.text("SELECT to_regclass('public.dashboard_snapshot')")).scalar()
                table_exists = result is not None
            elif dialect == 'sqlite':
                result = db.session.execute(db.text("SELECT name FROM sqlite_master WHERE type='table' AND name='dashboard_snapshot'")).scalar()
                table_exists = result is not None
            else:
                logger.warning(f"Unsupported dialect for manual check: {dialect}. Proceeding with CREATE TABLE IF NOT EXISTS.")
        except Exception as e:
            logger.error(f"Error checking table existence: {e}")
            
        if table_exists:
            logger.info("Table 'dashboard_snapshot' already exists. Migration step skipped.")
            return

        logger.info("Creating 'dashboard_snapshot' table...")
        
        # Dialect agnostic robust statement
        create_table_sql = """
            CREATE TABLE IF NOT EXISTS dashboard_snapshot (
                id SERIAL PRIMARY KEY,
                cache_key VARCHAR(100) UNIQUE NOT NULL,
                payload TEXT NOT NULL,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS ix_dashboard_snapshot_cache_key ON dashboard_snapshot (cache_key);
        """
        
        if dialect == 'sqlite':
            # SQLite specific syntax
            create_table_sql = """
                CREATE TABLE IF NOT EXISTS dashboard_snapshot (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key VARCHAR(100) UNIQUE NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_dashboard_snapshot_cache_key ON dashboard_snapshot (cache_key);
            """

        try:
            if dialect == 'postgresql':
                db.session.execute(db.text('CREATE TABLE IF NOT EXISTS dashboard_snapshot (id SERIAL PRIMARY KEY, cache_key VARCHAR(100) UNIQUE NOT NULL, payload TEXT NOT NULL, updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP)'))
                db.session.execute(db.text('CREATE INDEX IF NOT EXISTS ix_dashboard_snapshot_cache_key ON dashboard_snapshot (cache_key)'))
            elif dialect == 'sqlite':
                db.session.execute(db.text('CREATE TABLE IF NOT EXISTS dashboard_snapshot (id INTEGER PRIMARY KEY AUTOINCREMENT, cache_key VARCHAR(100) UNIQUE NOT NULL, payload TEXT NOT NULL, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)'))
                db.session.execute(db.text('CREATE INDEX IF NOT EXISTS ix_dashboard_snapshot_cache_key ON dashboard_snapshot (cache_key)'))
                
            db.session.commit()
            logger.info("✅ Table 'dashboard_snapshot' successfully created.")
        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Failed to create table: {e}")
            raise

if __name__ == '__main__':
    migrate_snapshot_table()
