
import psycopg2
import socket
import sys

# Config
DB_HOST = "127.0.0.1"
DB_NAME = "monitoring_db"
DB_USER = "monitoring_man"
DB_PASS = "admin123"

def register_device():
    # 1. Get Local Info
    hostname = socket.gethostname()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except:
        local_ip = "127.0.0.1"

    print(f"Registering Agent Device: {hostname} ({local_ip})")

    # 2. Connect to DB
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS
        )
        cur = conn.cursor()
        
        # 3. Check Table Name
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        tables = [row[0] for row in cur.fetchall()]
        print(f"Tables found: {tables}")
        
        table_name = "devices" if "devices" in tables else "device"
        if table_name not in tables:
            print("CRITICAL: Neither 'device' nor 'devices' table found!")
            conn.close()
            return

        print(f"Using table: {table_name}")

        # 4. Check if device exists
        cur.execute(f"SELECT device_id FROM {table_name} WHERE device_ip = %s OR hostname = %s", (local_ip, hostname))
        existing = cur.fetchone()
        
        if existing:
            print(f"Device already exists (ID: {existing[0]}). Skipping.")
        else:
            # 5. Insert Device
            print("Inserting new device...")
            # Note: 'updated_at' might be auto-managed, but providing it is safer if column exists
            # We assume columns verify against model: device_name, device_ip, device_type, manufacturer, hostname, is_active
            
            sql = f"""
                INSERT INTO {table_name} (device_name, device_ip, device_type, manufacturer, hostname, is_active, created_at, updated_at)
                VALUES (%s, %s, 'Server', 'Self-Hosted', %s, true, NOW(), NOW())
                RETURNING device_id
            """
            cur.execute(sql, (hostname, local_ip, hostname))
            new_id = cur.fetchone()[0]
            conn.commit()
            print(f"Successfully registered device. ID: {new_id}")
        
        conn.close()

    except Exception as e:
        print(f"Database Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    register_device()
