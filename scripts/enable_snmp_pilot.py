"""
enable_snmp_pilot.py — One-time script to enable SNMP polling for a pilot set of devices.

Selects up to 5 pilot candidates (monitored devices first, then by last updated),
enables their device_snmp_config rows, and sets community_string to 'public' only
where it is NULL or empty.

Devices with a non-default community_string (custom SNMP credentials) are enabled
without overwriting their community_string.

Usage:
    python scripts/enable_snmp_pilot.py
    python scripts/enable_snmp_pilot.py --dry-run   # preview only, no DB changes
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from extensions import db
from sqlalchemy import text

PILOT_SIZE = 5


def run(dry_run: bool = False):
    app = create_app()
    with app.app_context():
        # ── Step 1: Select pilot candidates ──────────────────────────────────
        # Prefer actively monitored devices; break ties by most recently updated.
        # Must have a device_snmp_config row (all 239 do via seeding).
        candidates_sql = text("""
            SELECT d.device_id, d.hostname, d.device_ip, d.is_monitored,
                   sc.is_enabled, sc.community_string
            FROM device d
            JOIN device_snmp_config sc ON d.device_id = sc.device_id
            ORDER BY d.is_monitored DESC, d.updated_at DESC
            LIMIT :limit
        """)
        rows = db.session.execute(candidates_sql, {'limit': PILOT_SIZE}).fetchall()

        if not rows:
            print("No devices with device_snmp_config rows found. Nothing to do.")
            return

        print(f"{'DRY-RUN — ' if dry_run else ''}Pilot candidates ({len(rows)}):")
        print(f"  {'device_id':<12} {'hostname':<30} {'ip_address':<18} "
              f"{'monitored':<12} {'snmp_enabled':<14} {'community'}")
        print("  " + "-" * 100)
        for r in rows:
            print(f"  {r.device_id:<12} {(r.hostname or 'Unknown'):<30} {(r.device_ip or ''):<18} "
                  f"{str(r.is_monitored):<12} {str(r.is_enabled):<14} {r.community_string or 'NULL'}")

        if dry_run:
            print("\n[DRY-RUN] No changes written.")
            return

        print()

        # ── Step 2: Enable each candidate ────────────────────────────────────
        enabled_count = 0
        skipped_already_on = 0

        for r in rows:
            device_id = r.device_id
            current_community = r.community_string

            if r.is_enabled:
                print(f"  [{device_id}] {r.hostname or r.device_ip} — already enabled, skipped")
                skipped_already_on += 1
                continue

            # Determine whether to overwrite community_string:
            #   NULL or empty → set to 'public'
            #   Any existing value (incl. 'public' default) → preserve it
            if not current_community:
                update_sql = text("""
                    UPDATE device_snmp_config
                    SET is_enabled = true,
                        community_string = 'public'
                    WHERE device_id = :device_id
                      AND (community_string IS NULL OR community_string = '')
                """)
                db.session.execute(update_sql, {'device_id': device_id})
                community_note = "community_string set to 'public'"
            else:
                # Already has a community_string (custom or default 'public') — enable only
                update_sql = text("""
                    UPDATE device_snmp_config
                    SET is_enabled = true
                    WHERE device_id = :device_id
                """)
                db.session.execute(update_sql, {'device_id': device_id})
                community_note = f"community_string preserved ('{current_community}')"

            print(f"  [{device_id}] {r.hostname or r.device_ip} ({r.device_ip}) — "
                  f"ENABLED  ({community_note})")
            enabled_count += 1

        db.session.commit()

        # ── Step 3: Summary ───────────────────────────────────────────────────
        print()
        print(f"{enabled_count} device{'s' if enabled_count != 1 else ''} enabled for SNMP pilot.")
        if skipped_already_on:
            print(f"{skipped_already_on} device{'s' if skipped_already_on != 1 else ''} "
                  f"already enabled — no change needed.")

        # ── Step 4: Verification query hint ──────────────────────────────────
        print()
        print("Verify with:")
        print("""  SELECT d.hostname, d.device_ip, sc.is_enabled, sc.community_string
  FROM device d
  JOIN device_snmp_config sc ON d.device_id = sc.device_id
  WHERE sc.is_enabled = true;""")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Enable SNMP pilot devices')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview selected devices without writing any changes'
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
