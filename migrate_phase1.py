"""
Phase 1 MVP — Database Migration (Lightweight)

Adds new columns/tables directly using SQLAlchemy engine.
Does NOT start the Flask app (avoids chicken-egg column errors).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, text
from config import Config

DB_URL = Config.SQLALCHEMY_DATABASE_URI

MIGRATIONS = [
    # --- New tables ---
    """CREATE TABLE IF NOT EXISTS sites (
        id SERIAL PRIMARY KEY, site_name VARCHAR(200) NOT NULL UNIQUE,
        site_code VARCHAR(50) UNIQUE, address TEXT, timezone VARCHAR(50) DEFAULT 'UTC',
        contact_name VARCHAR(200), contact_email VARCHAR(200), contact_phone VARCHAR(50),
        created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS departments (
        id SERIAL PRIMARY KEY, name VARCHAR(200) NOT NULL UNIQUE, description TEXT,
        site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,
        created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS printer_metrics (
        id SERIAL PRIMARY KEY,
        device_id INTEGER NOT NULL REFERENCES device(device_id) ON DELETE CASCADE,
        timestamp TIMESTAMP DEFAULT NOW(), status VARCHAR(50), status_code INTEGER,
        toner_black INTEGER, toner_cyan INTEGER, toner_magenta INTEGER, toner_yellow INTEGER,
        paper_tray_status JSON, page_count_total BIGINT, page_count_color BIGINT,
        page_count_bw BIGINT, job_queue_length INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS print_job_audit (
        id SERIAL PRIMARY KEY,
        device_id INTEGER NOT NULL REFERENCES device(device_id) ON DELETE CASCADE,
        print_server_id INTEGER REFERENCES device(device_id) ON DELETE SET NULL,
        job_id VARCHAR(100) NOT NULL, document_name VARCHAR(500),
        user_account VARCHAR(200), source_ip VARCHAR(50),
        printer_name VARCHAR(200) NOT NULL, page_count INTEGER, size_bytes BIGINT,
        submission_time TIMESTAMP NOT NULL, completion_time TIMESTAMP,
        status VARCHAR(50), collection_source VARCHAR(50)
    )""",
    # --- ALTER existing tables ---
    "ALTER TABLE device ADD COLUMN IF NOT EXISTS site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL",
    "ALTER TABLE device ADD COLUMN IF NOT EXISTS department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL",
    """ALTER TABLE "user" ADD COLUMN IF NOT EXISTS department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL""",
    # --- Indexes ---
    "CREATE INDEX IF NOT EXISTS idx_device_site_id ON device(site_id)",
    "CREATE INDEX IF NOT EXISTS idx_device_department_id ON device(department_id)",
    "CREATE INDEX IF NOT EXISTS idx_printer_metrics_device_ts ON printer_metrics(device_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_print_job_user_time ON print_job_audit(user_account, submission_time)",
    "CREATE INDEX IF NOT EXISTS idx_print_job_ip_time ON print_job_audit(source_ip, submission_time)",
    "CREATE INDEX IF NOT EXISTS idx_print_job_printer_time ON print_job_audit(printer_name, submission_time)",
]

def main():
    print(f"DB: {DB_URL.split('@')[-1] if '@' in DB_URL else DB_URL[:40]}")
    engine = create_engine(DB_URL)
    ok = 0
    with engine.connect() as conn:
        for i, sql in enumerate(MIGRATIONS, 1):
            label = sql.strip()[:60]
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"  [{i}/{len(MIGRATIONS)}] OK: {label}")
                ok += 1
            except Exception as e:
                conn.rollback()
                msg = str(e).split('\n')[0][:80]
                if 'already exists' in msg.lower():
                    print(f"  [{i}/{len(MIGRATIONS)}] SKIP: {label}")
                    ok += 1
                else:
                    print(f"  [{i}/{len(MIGRATIONS)}] ERR: {msg}")
    print(f"\nDone: {ok}/{len(MIGRATIONS)}")

if __name__ == "__main__":
    main()
