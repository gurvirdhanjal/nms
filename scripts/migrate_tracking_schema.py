import os
import sys

# Add the project directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from extensions import db
from sqlalchemy import text

def add_columns_to_tracked_devices():
    app = create_app()
    with app.app_context():
        try:
            print("Applying schema migrations to tracked_devices table...")
            
            # Using raw SQL to add columns safely without dropping/recreating the table
            queries = [
                "ALTER TABLE tracked_devices ADD COLUMN IF NOT EXISTS availability_status VARCHAR(20) DEFAULT 'offline';",
                "ALTER TABLE tracked_devices ADD COLUMN IF NOT EXISTS tracking_data TEXT;",
                "ALTER TABLE tracked_devices ADD COLUMN IF NOT EXISTS metrics_available BOOLEAN DEFAULT FALSE;",
                "ALTER TABLE tracked_devices ADD COLUMN IF NOT EXISTS probe_error_code VARCHAR(50);",
                "ALTER TABLE tracked_devices ADD COLUMN IF NOT EXISTS probe_method VARCHAR(50);",
                "ALTER TABLE tracked_devices ADD COLUMN IF NOT EXISTS last_probe_at TIMESTAMP WITHOUT TIME ZONE;"
            ]
            
            for query in queries:
                db.session.execute(text(query))
            
            db.session.commit()
            print("Successfully added new tracking caching columns.")
            
        except Exception as e:
            db.session.rollback()
            print(f"Error applying migrations: {e}")
            raise

if __name__ == '__main__':
    add_columns_to_tracked_devices()
