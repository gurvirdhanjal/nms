
import sqlite3
import os

def migrate():
    cwd = os.getcwd()
    # Try root first
    db_path = os.path.join(cwd, 'secure_employee_monitor.db')
    
    if not os.path.exists(db_path):
        # Try instance folder
        db_path = os.path.join(cwd, 'instance', 'secure_employee_monitor.db')
        
    if not os.path.exists(db_path):
        print(f"Database not found in {cwd} or {os.path.join(cwd, 'instance')}")
        return

    print(f"Migrating database at: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Check if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='device'")
        if not cursor.fetchone():
            print("Table 'device' not found.")
            return

        cursor.execute("PRAGMA table_info(device)")
        columns = [info[1] for info in cursor.fetchall()]
        
        if 'offline_strikes' not in columns:
            print("Adding offline_strikes column to device table...")
            cursor.execute("ALTER TABLE device ADD COLUMN offline_strikes INTEGER DEFAULT 0")
            print("Column added successfully.")
        else:
            print("Column offline_strikes already exists.")

        conn.commit()
    except Exception as e:
        print(f"Migration failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == '__main__':
    migrate()
