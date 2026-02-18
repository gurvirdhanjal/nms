"""
Subnet Migration Script (Idempotent)
=====================================
Adds subnet_cidr column to the device table and backfills
existing devices using /24 derivation from device_ip.

Safe to run multiple times — checks column existence and
only updates rows where subnet_cidr IS NULL.

Usage:
    python run_subnet_migration.py
"""
import ipaddress
import sys


def compute_subnet_cidr(ip_str, prefix=24):
    """Derive /24 CIDR from an IPv4 address string."""
    try:
        net = ipaddress.ip_network(f"{ip_str}/{prefix}", strict=False)
        return str(net)
    except (ValueError, TypeError):
        return None


def run():
    from app import create_app
    app = create_app()

    with app.app_context():
        from extensions import db
        from sqlalchemy import text, inspect

        engine = db.engine
        inspector = inspect(engine)
        columns = [c["name"] for c in inspector.get_columns("device")]

        # ── Step 1: Add column if missing ──
        if "subnet_cidr" not in columns:
            dialect = engine.dialect.name
            if dialect == "postgresql":
                db.session.execute(text(
                    "ALTER TABLE device ADD COLUMN subnet_cidr VARCHAR(50)"
                ))
            else:
                db.session.execute(text(
                    "ALTER TABLE device ADD COLUMN subnet_cidr VARCHAR(50)"
                ))
            db.session.commit()
            print("[Migration] Added subnet_cidr column to device table.")
        else:
            print("[Migration] subnet_cidr column already exists — skipping ADD.")

        # ── Step 2: Create index if missing ──
        indexes = inspector.get_indexes("device")
        idx_names = [idx["name"] for idx in indexes]
        if "ix_device_subnet_cidr" not in idx_names:
            try:
                db.session.execute(text(
                    "CREATE INDEX ix_device_subnet_cidr ON device (subnet_cidr)"
                ))
                db.session.commit()
                print("[Migration] Created index ix_device_subnet_cidr.")
            except Exception as e:
                print(f"[Migration] Index creation skipped: {e}")
                db.session.rollback()
        else:
            print("[Migration] Index ix_device_subnet_cidr already exists — skipping.")

        # ── Step 3: Backfill existing devices ──
        from models.device import Device
        devices = Device.query.filter(Device.subnet_cidr.is_(None)).all()
        backfilled = 0
        for dev in devices:
            cidr = compute_subnet_cidr(dev.device_ip)
            if cidr:
                dev.subnet_cidr = cidr
                backfilled += 1

        if backfilled:
            db.session.commit()
        print(f"[Migration] Backfilled subnet_cidr for {backfilled} device(s).")
        print("[Migration] Done.")


if __name__ == "__main__":
    run()
