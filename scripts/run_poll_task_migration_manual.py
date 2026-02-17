"""
Manual Migration: Create poll_tasks table bypassing app factory.

Usage:
    python scripts/run_poll_task_migration_manual.py
"""
import sys
import os
import logging

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from flask import Flask
from extensions import db
from config import Config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_manual_migration():
    print("[MANUAL_MIGRATION] initializing minimal app...")
    
    app = Flask(__name__)
    
    # Load config directly
    app.config.from_object(Config)
    
    # Check URI
    uri = app.config.get('SQLALCHEMY_DATABASE_URI')
    print(f"[MANUAL_MIGRATION] URI: {uri}")
    
    # Initialize DB
    db.init_app(app)
    
    with app.app_context():
        # Import model to register it
        from models.poll_task import PollTask
        
        print("[MANUAL_MIGRATION] Creating tables...")
        try:
            db.create_all()
            print("[MANUAL_MIGRATION] Success! poll_tasks table created.")
        except Exception as e:
            print(f"[MANUAL_MIGRATION] Error creating tables: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    run_manual_migration()
