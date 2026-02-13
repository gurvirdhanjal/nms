import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from sqlalchemy import text

app = create_app()

with app.app_context():
    print("Starting migration...")
    commands = [
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS location VARCHAR(100)",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS monitoring_mode VARCHAR(20) DEFAULT 'ping'",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS snmp_version VARCHAR(10) DEFAULT 'v2c'",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS snmp_port INTEGER DEFAULT 161",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS snmp_timeout INTEGER DEFAULT 2",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS snmp_retries INTEGER DEFAULT 1",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS snmp_community VARCHAR(100)",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS snmp_username VARCHAR(100)",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS snmp_auth_proto VARCHAR(10)",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS snmp_auth_password VARCHAR(100)",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS snmp_priv_proto VARCHAR(10)",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS snmp_priv_password VARCHAR(100)",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS agent_token VARCHAR(100)",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS agent_interval INTEGER DEFAULT 300",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS agent_os_type VARCHAR(20)",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS wmi_username VARCHAR(100)",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS wmi_password VARCHAR(100)",
        "ALTER TABLE device ADD COLUMN IF NOT EXISTS wmi_domain VARCHAR(100)"
    ]
    
    with db.engine.connect() as conn:
        for cmd in commands:
            try:
                conn.execute(text(cmd))
                print(f"Executed: {cmd}")
            except Exception as e:
                # Ignore duplicate column errors if IF NOT EXISTS fails for some reason or logic is slightly off
                print(f"Error executing {cmd}: {e}")
        conn.commit()
    print("Migration complete.")
