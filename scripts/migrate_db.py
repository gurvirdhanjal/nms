
import sys
import os
from sqlalchemy import text

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db

def migrate():
    app = create_app()
    with app.app_context():
        print(f"Connected to DB: {app.config['SQLALCHEMY_DATABASE_URI']}")
        
        try:
            with db.engine.connect() as conn:
                # Check if column exists
                # This query works for Postgres and SQLite (if using inspection, but let's try raw Add Column if not exists)
                # SQLite doesn't support IF NOT EXISTS in ADD COLUMN in older versions, but Postgres does.
                # Safer: Check first.
                
                # Use Inspection
                from sqlalchemy import inspect
                inspector = inspect(db.engine)
                columns = [c['name'] for c in inspector.get_columns('device')]
                
                if 'offline_strikes' not in columns:
                    print("Adding offline_strikes column...")
                    # SQLAlchemy doesn't produce DDL automatically here, so we execute raw SQL based on dialect
                    if 'sqlite' in str(db.engine.url):
                        conn.execute(text("ALTER TABLE device ADD COLUMN offline_strikes INTEGER DEFAULT 0"))
                    else:
                        conn.execute(text("ALTER TABLE device ADD COLUMN IF NOT EXISTS offline_strikes INTEGER DEFAULT 0"))
                    
                    conn.commit()
                    print("Column added successfully.")
                else:
                    print("Column already exists.")
                    
        except Exception as e:
            print(f"Migration error: {e}")

if __name__ == '__main__':
    migrate()
