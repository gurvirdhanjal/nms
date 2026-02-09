import os
import argparse
from sqlalchemy import create_engine, MetaData, Table, text
from sqlalchemy.exc import SQLAlchemyError


def get_default_sqlite_url():
    # Defaults to the same path used by config.py
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    instance_dir = os.path.join(base_dir, "instance")
    return f"sqlite:///{os.path.join(instance_dir, 'device_monitoring.db')}"


def _quote_ident(name: str) -> str:
    # Minimal safe identifier quoting for PostgreSQL (double-quote + escape)
    return '"' + name.replace('"', '""') + '"'


def reset_sequence(conn, table_name, pk_name):
    # Reset PostgreSQL sequence to max(pk)
    try:
        seq_sql = text(
            "SELECT pg_get_serial_sequence(:table, :col) AS seq"
        )
        seq = conn.execute(seq_sql, {"table": table_name, "col": pk_name}).scalar()
        if seq:
            q_table = _quote_ident(table_name)
            q_pk = _quote_ident(pk_name)
            conn.execute(
                text(
                    f"SELECT setval('{seq}', (SELECT COALESCE(MAX({q_pk}), 0) FROM {q_table}))"
                )
            )
    except Exception:
        # Best-effort only
        pass


def main():
    parser = argparse.ArgumentParser(
        description="Migrate data from SQLite to PostgreSQL"
    )
    parser.add_argument(
        "--sqlite",
        default=os.environ.get("SQLITE_URL", get_default_sqlite_url()),
        help="SQLite SQLAlchemy URL (default: instance/device_monitoring.db)",
    )
    parser.add_argument(
        "--pg",
        default=os.environ.get("DATABASE_URL"),
        required=False,
        help="PostgreSQL SQLAlchemy URL (or set DATABASE_URL env var)",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Truncate target tables before migration",
    )
    args = parser.parse_args()

    if not args.pg:
        raise SystemExit("Missing Postgres URL. Use --pg or set DATABASE_URL.")

    sqlite_engine = create_engine(args.sqlite)
    pg_engine = create_engine(args.pg)

    sqlite_meta = MetaData()
    sqlite_meta.reflect(bind=sqlite_engine)

    pg_meta = MetaData()
    pg_meta.reflect(bind=pg_engine)

    table_names = [t.name for t in sqlite_meta.sorted_tables]
    if not table_names:
        raise SystemExit("No tables found in SQLite database.")

    with pg_engine.begin() as pg_conn:
        if args.truncate:
            for name in reversed(table_names):
                if name in pg_meta.tables:
                    q_name = _quote_ident(name)
                    pg_conn.execute(text(f"TRUNCATE TABLE {q_name} RESTART IDENTITY CASCADE"))

        for table in sqlite_meta.sorted_tables:
            if table.name not in pg_meta.tables:
                print(f"Skipping missing table in Postgres: {table.name}")
                continue

            pg_table = Table(table.name, pg_meta, autoload_with=pg_engine)

            rows = []
            with sqlite_engine.connect() as sqlite_conn:
                result = sqlite_conn.execute(table.select())
                rows = [dict(row._mapping) for row in result]

            if not rows:
                print(f"{table.name}: 0 rows")
                continue

            try:
                pg_conn.execute(pg_table.insert(), rows)
                print(f"{table.name}: {len(rows)} rows migrated")
            except SQLAlchemyError as e:
                raise SystemExit(f"Failed on table {table.name}: {e}")

            # Best-effort sequence reset
            for col in pg_table.columns:
                if col.primary_key and col.autoincrement:
                    reset_sequence(pg_conn, table.name, col.name)


if __name__ == "__main__":
    main()
