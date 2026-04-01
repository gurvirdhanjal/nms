import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("No DATABASE_URL found in .env")
    exit(1)

print(f"Connecting to {db_url}")
conn = psycopg2.connect(db_url.replace("postgresql+psycopg2://", "postgresql://"))
conn.autocommit = True
cur = conn.cursor()
try:
    cur.execute('ALTER TABLE departments ADD COLUMN created_by VARCHAR(80);')
    print("Success: added created_by column to departments")
except Exception as e:
    print(f"Failed to add column: {e}")
finally:
    cur.close()
    conn.close()
