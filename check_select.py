import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import psycopg2

load_dotenv()
db_url = os.getenv("DATABASE_URL")
print("URL:", db_url)

# Test 1: psycopg2 directly
print("--- Test 1: psycopg2 ---")
try:
    conn = psycopg2.connect(db_url.replace("postgresql+psycopg2://", "postgresql://"))
    cur = conn.cursor()
    cur.execute("SELECT created_by FROM departments LIMIT 1;")
    ret = cur.fetchone()
    print("psycopg2 success:", ret)
    conn.close()
except Exception as e:
    print("psycopg2 failed:", e)

# Test 2: sqlalchemy directly
print("--- Test 2: sqlalchemy ---")
try:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        res = conn.execute(text("SELECT created_by FROM departments LIMIT 1;"))
        print("sqlalchemy success:", res.fetchone())
except Exception as e:
    print("sqlalchemy failed:", e)
