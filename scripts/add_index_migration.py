import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from sqlalchemy import text

app = create_app()

with app.app_context():
    print("Starting index migration...")
    
    # Check if index exists (PostgreSQL specific check, or generic try/catch)
    # For SQLite/Postgres generic approach:
    try:
        with db.engine.connect() as conn:
            # We use a raw SQL command that works for both if we handle errors, 
            # but IF NOT EXISTS is cleaner. 
            # SQLite supports 'CREATE INDEX IF NOT EXISTS'
            # Postgres supports 'CREATE INDEX IF NOT EXISTS'
            
            cmd = "CREATE INDEX IF NOT EXISTS idx_device_scan_history_device_ip ON device_scan_history (device_ip)"
            
            conn.execute(text(cmd))
            conn.commit()
            print(f"Executed: {cmd}")
            
    except Exception as e:
        print(f"Error executing migration: {e}")

    print("Index migration complete.")
