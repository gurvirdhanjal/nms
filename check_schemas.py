import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL")
conn = psycopg2.connect(db_url.replace("postgresql+psycopg2://", "postgresql://"))
cur = conn.cursor()
cur.execute("SELECT table_schema, column_name FROM information_schema.columns WHERE table_name='departments';")
cols = cur.fetchall()
print("Departments cols across schemas:", cols)

cur.execute("SELECT current_schema();")
print("Current schema:", cur.fetchone()[0])
