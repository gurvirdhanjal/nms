
import sqlite3
import os

# Check for both sqlite and postgres, but app seems to use postgres now based on logs
# [DB] SQLALCHEMY_DATABASE_URI=postgresql+psycopg2://monitoring_man:***@127.0.0.1:5432/monitoring_db

# However, I should check if I can connect to PG or if there is a local sqlite fallback.
# Let's try to infer from the code or just check the environment.
# Actually, the user logs showed: 
# [DB] SQLALCHEMY_DATABASE_URI=postgresql+psycopg2://monitoring_man:***@127.0.0.1:5432/monitoring_db

# I don't have pg driver installed in this environment context easily maybe, but I can try using standard SQL logic if the script runs in the user's venv.
# The user is running `python app.py` which works.

from app import app, db
from models.server_logs import ServerHealth

try:
    with app.app_context():
        count = ServerHealth.query.count()
        print(f"ServerHealth Record Count: {count}")
        
        last = ServerHealth.query.order_by(ServerHealth.timestamp.desc()).first()
        if last:
            print(f"Last Record: {last.hostname} at {last.timestamp}")
            print(f"Data: CPU={last.cpu_usage}, Mem={last.memory_usage}")
        else:
            print("No records found in ServerHealth table.")

except Exception as e:
    print(f"Error checking DB: {e}")
