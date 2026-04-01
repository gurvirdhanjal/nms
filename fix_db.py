import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("No DATABASE_URL found in .env")
    exit(1)

print(f"Connecting to {db_url}")
engine = create_engine(db_url)
with engine.begin() as conn:
    try:
        conn.execute(text('ALTER TABLE departments ADD COLUMN created_by VARCHAR(80);'))
        print("Success: added created_by column")
    except Exception as e:
        print(f"Failed to add column: {e}")

