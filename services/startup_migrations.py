"""
Idempotent index backfill for the NMS database.

The app uses db.create_all() rather than Alembic, so indexes added to models
after the initial deploy never land in production.  This module runs
CREATE INDEX IF NOT EXISTS at every startup — safe to re-run, completes in
milliseconds when the index already exists.

PostgreSQL: uses CREATE INDEX CONCURRENTLY so no table locks are held during
the build — other writes continue uninterrupted.  Requires autocommit mode.

SQLite: uses standard CREATE INDEX IF NOT EXISTS (no concurrent users).

Call run_startup_migrations_bg(app, db) once after db.create_all() to build
indexes in a background thread so the Flask app can start serving immediately.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask
    from flask_sqlalchemy import SQLAlchemy

logger = logging.getLogger(__name__)

_INDEXES: list[tuple[str, str, str]] = [
    # (index_name, table, columns)
    # network_scan — zero indexes before this fix; full table scans on every report
    ("idx_network_scan_timestamp",          "network_scan",          "scan_timestamp"),
    ("idx_network_scan_ip_range_timestamp", "network_scan",          "ip_range, scan_timestamp"),
    # port_scan_result — zero indexes before this fix
    ("idx_port_scan_result_device_ip",           "port_scan_result", "device_ip"),
    ("idx_port_scan_result_device_ip_timestamp", "port_scan_result", "device_ip, scan_timestamp"),
    # device_activity_logs — only had single-column timestamp; per-device queries full-scanned
    ("idx_device_activity_logs_device_timestamp",    "device_activity_logs",    "device_id, timestamp"),
    # device_resource_logs — same problem
    ("idx_device_resource_logs_device_timestamp",    "device_resource_logs",    "device_id, timestamp"),
    # device_application_logs — same problem
    ("idx_device_application_logs_device_timestamp", "device_application_logs", "device_id, timestamp"),
    # device — floor-plan placement lookups ("which devices are on this plan")
    ("idx_device_floor_plan_id",                     "device",                  "floor_plan_id"),
    # patch_commands — per-device status lookup (sync payload injection + admin list)
    ("ix_patch_commands_device_status",              "patch_commands",          "tracked_device_id, status"),
]

# Columns added to existing tables after the initial deploy.  db.create_all()
# creates missing TABLES but never ALTERs existing ones, so new columns must be
# backfilled here or they never land in production.
#   (table, column, type_sql)
# type_sql is portable across PostgreSQL and SQLite for these simple types.
_COLUMNS: list[tuple[str, str, str]] = [
    # Floor-plan geotagging: device placement on an uploaded plant map.
    ("device", "floor_plan_id", "INTEGER"),
    ("device", "map_x",         "DOUBLE PRECISION"),
    ("device", "map_y",         "DOUBLE PRECISION"),
    # Marker presentation metadata (reserved; no UI yet).
    ("device", "map_rotation",        "DOUBLE PRECISION"),
    ("device", "map_label_offset_x",  "DOUBLE PRECISION"),
    ("device", "map_label_offset_y",  "DOUBLE PRECISION"),
    # Placement lock — keep core devices from being dragged accidentally.
    # DEFAULT keeps the ALTER valid for existing rows under a NOT NULL column.
    ("device", "map_locked", "BOOLEAN NOT NULL DEFAULT false"),
    # Agent-reported connection type: 'wifi' | 'lan' | 'unknown'.
    ("device", "connection_type", "VARCHAR(10)"),
]

# SQLite spells some types/literals differently; normalise per dialect.
_SQLITE_TYPE_OVERRIDES = {
    "DOUBLE PRECISION": "REAL",
    "BOOLEAN NOT NULL DEFAULT false": "BOOLEAN NOT NULL DEFAULT 0",
}


def _existing_columns(conn, table: str) -> set[str]:
    """Return the set of column names on a table (SQLite PRAGMA path)."""
    from sqlalchemy import text

    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
    return {row[1] for row in rows}


def _pg_column_exists(conn, table: str, column: str) -> bool:
    from sqlalchemy import text
    row = conn.execute(
        text("SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name=:c"),
        {"t": table, "c": column},
    ).fetchone()
    return row is not None


def _pg_index_exists(conn, index_name: str) -> bool:
    from sqlalchemy import text
    row = conn.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname=:n"),
        {"n": index_name},
    ).fetchone()
    return row is not None


def _run_column_migrations(db: "SQLAlchemy", is_pg: bool) -> int:
    """Idempotently add missing columns to existing tables.  Returns error count."""
    from sqlalchemy import text

    errors = 0
    if is_pg:
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for table, column, type_sql in _COLUMNS:
                # Check via information_schema first — ALTER TABLE takes an
                # AccessExclusiveLock even when IF NOT EXISTS is a no-op, which
                # blocks every concurrent request and deadlocks all workers.
                if _pg_column_exists(conn, table, column):
                    continue
                sql = f"ALTER TABLE {table} ADD COLUMN {column} {type_sql}"
                try:
                    conn.execute(text(sql))
                    logger.info("[STARTUP MIGRATION] ensured column %s.%s", table, column)
                except Exception as exc:
                    errors += 1
                    logger.warning(
                        "[STARTUP MIGRATION] could not add column %s.%s: %s",
                        table, column, exc,
                    )
    else:
        # SQLite has no ADD COLUMN IF NOT EXISTS — check PRAGMA first.
        with db.engine.connect() as conn:
            for table, column, type_sql in _COLUMNS:
                try:
                    if column in _existing_columns(conn, table):
                        continue
                    sqlite_type = _SQLITE_TYPE_OVERRIDES.get(type_sql, type_sql)
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqlite_type}"))
                    conn.commit()
                    logger.info("[STARTUP MIGRATION] ensured column %s.%s", table, column)
                except Exception as exc:
                    errors += 1
                    logger.warning(
                        "[STARTUP MIGRATION] could not add column %s.%s: %s",
                        table, column, exc,
                    )
    return errors


def _get_hypertables(conn) -> set[str]:
    """Return the set of TimescaleDB hypertable names, or empty set if not installed."""
    from sqlalchemy import text

    try:
        rows = conn.execute(
            text("SELECT hypertable_name FROM timescaledb_information.hypertables")
        ).fetchall()
        return {row[0] for row in rows}
    except Exception:
        return set()


def run_startup_migrations(db: "SQLAlchemy") -> None:
    """Create missing indexes idempotently.

    PostgreSQL: CONCURRENTLY — no table lock, writes continue during build.
    TimescaleDB hypertables: standard CREATE INDEX (CONCURRENTLY is unsupported).
    SQLite: standard IF NOT EXISTS — fast because SQLite is single-writer.
    """
    from sqlalchemy import text

    is_pg = db.engine.dialect.name == "postgresql"
    errors = 0

    # Columns first — indexes below may reference newly-added columns.
    errors += _run_column_migrations(db, is_pg)

    if is_pg:
        # CONCURRENTLY requires the connection to be outside any transaction
        # (autocommit mode).  Each statement is its own implicit transaction.
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            hypertables = _get_hypertables(conn)
            if hypertables:
                logger.info("[STARTUP MIGRATION] TimescaleDB hypertables detected: %s", hypertables)

            for index_name, table, columns in _INDEXES:
                # Pre-check pg_indexes — CREATE INDEX (even IF NOT EXISTS) briefly
                # acquires a ShareLock on the table and blocks DDL; skip entirely
                # when the index already exists to keep startup lock-free.
                if _pg_index_exists(conn, index_name):
                    continue
                # TimescaleDB hypertables reject CONCURRENTLY — use standard path for them.
                if table in hypertables:
                    sql = f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({columns})"
                else:
                    sql = (
                        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        f"{index_name} ON {table} ({columns})"
                    )
                try:
                    conn.execute(text(sql))
                    logger.info(
                        "[STARTUP MIGRATION] created index %s ON %s (%s)",
                        index_name, table, columns,
                    )
                except Exception as exc:
                    errors += 1
                    logger.warning(
                        "[STARTUP MIGRATION] could not create index %s: %s",
                        index_name, exc,
                    )
    else:
        # SQLite path — standard CREATE INDEX IF NOT EXISTS
        with db.engine.connect() as conn:
            for index_name, table, columns in _INDEXES:
                sql = (
                    f"CREATE INDEX IF NOT EXISTS "
                    f"{index_name} ON {table} ({columns})"
                )
                try:
                    conn.execute(text(sql))
                    conn.commit()
                    logger.info(
                        "[STARTUP MIGRATION] ensured index %s ON %s (%s)",
                        index_name, table, columns,
                    )
                except Exception as exc:
                    errors += 1
                    logger.warning(
                        "[STARTUP MIGRATION] could not create index %s: %s",
                        index_name, exc,
                    )

    if errors:
        logger.warning("[STARTUP MIGRATION] done with %d errors — check logs above", errors)
    else:
        logger.info("[STARTUP MIGRATION] all indexes verified")


def _clean_mac_from_hostnames(db: "SQLAlchemy") -> None:
    """One-time cleanup: move MAC-format hostnames into the macaddress field.

    Some devices end up with a MAC address stored in the `hostname` column
    (e.g. 'AA:BB:CC:DD:EE:FF') from old scan imports.  This scans all devices
    where hostname matches the MAC pattern and, if macaddress is blank, moves
    the value across and clears the hostname so it no longer pollutes name lookups.
    """
    import re
    _MAC_RE = re.compile(r'^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$')

    try:
        from models.device import Device
        devices = db.session.query(Device).filter(Device.hostname.isnot(None)).all()
        fixed = 0
        for device in devices:
            hn = (device.hostname or '').strip()
            if not _MAC_RE.match(hn):
                continue
            # Normalise to lowercase colon format
            cleaned = re.sub(r'[^0-9A-Fa-f]', '', hn).lower()
            if len(cleaned) != 12:
                continue
            normalized = ':'.join(cleaned[i:i+2] for i in range(0, 12, 2))
            # Only move if macaddress is currently empty/invalid
            current_mac = (device.macaddress or '').strip().lower()
            if current_mac in ('', 'n/a', 'unknown', 'none'):
                device.macaddress = normalized
            device.hostname = None
            fixed += 1

        if fixed:
            db.session.commit()
            logger.info("[STARTUP MIGRATION] moved %d MAC-format hostnames to macaddress field", fixed)
    except Exception as exc:
        logger.warning("[STARTUP MIGRATION] mac-from-hostname cleanup failed: %s", exc)
        try:
            db.session.rollback()
        except Exception:
            pass


def run_startup_migrations_bg(app: "Flask", db: "SQLAlchemy") -> None:
    """Non-blocking variant: spawns a daemon thread with an app context.

    The Flask app starts serving immediately; indexes build in the background.
    On large PostgreSQL tables this can take minutes — running inline would
    delay startup past the Docker health-check start-period and kill the container.
    """
    def _run() -> None:
        with app.app_context():
            run_startup_migrations(db)
            _clean_mac_from_hostnames(db)

    t = threading.Thread(target=_run, daemon=True, name="startup-migrations")
    t.start()
    logger.info("[STARTUP MIGRATION] index backfill started in background thread")
