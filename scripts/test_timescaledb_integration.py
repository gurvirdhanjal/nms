"""
Test TimescaleDB integration against the configured application database.
"""
import os
import sys
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from config import Config
from services.query_guardrails import QueryGuardrails
from services.timescaledb_service import TimescaleDBService


def _run_test(title, fn):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)
    try:
        return bool(fn())
    except Exception as exc:  # pragma: no cover - integration script
        print(f"X Test failed with exception: {exc}")
        return False


def test_database_connection():
    try:
        engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar()
        print("OK Connected to database")
        print(f"  Version: {version[:80]}...")
        return True
    except Exception as exc:
        print(f"X Connection failed: {exc}")
        return False


def test_timescaledb_enabled():
    enabled = TimescaleDBService.is_timescaledb_enabled()
    if enabled:
        print("OK TimescaleDB extension is enabled")
        return True
    print("X TimescaleDB extension is not enabled")
    return False


def test_hypertables():
    hypertables = TimescaleDBService.get_hypertable_info()
    if not hypertables:
        print("X No hypertables found")
        return False
    print(f"OK Found {len(hypertables)} hypertables:")
    for hypertable in hypertables:
        print(f"  - {hypertable['hypertable_name']}: {hypertable['num_chunks']} chunks")
    return True


def test_continuous_aggregates():
    aggregates = TimescaleDBService.get_continuous_aggregate_stats()
    if not aggregates:
        print("X No continuous aggregates found")
        return False
    print(f"OK Found {len(aggregates)} continuous aggregates:")
    for aggregate in aggregates:
        print(f"  - {aggregate['view_name']}")
    return True


def test_background_jobs():
    jobs = TimescaleDBService.get_job_stats()
    if not jobs:
        print("X No background jobs found")
        return False
    compression_jobs = [job for job in jobs if "Columnstore" in str(job.get("application_name", ""))]
    retention_jobs = [job for job in jobs if "Retention" in str(job.get("application_name", ""))]
    refresh_jobs = [job for job in jobs if "Refresh" in str(job.get("application_name", ""))]
    print(f"OK Found {len(jobs)} background jobs:")
    print(f"  - Compression policies: {len(compression_jobs)}")
    print(f"  - Retention policies: {len(retention_jobs)}")
    print(f"  - Refresh policies: {len(refresh_jobs)}")
    return True


def test_query_guardrails():
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=1)

    QueryGuardrails.validate_time_range(start_time, end_time, query_type="raw")
    print("OK Valid time range accepted")

    try:
        QueryGuardrails.validate_time_range(end_time - timedelta(days=30), end_time, query_type="raw")
        print("X Invalid time range was not rejected")
        return False
    except ValueError:
        print("OK Invalid time range rejected correctly")

    recommended = QueryGuardrails.recommend_query_type(end_time - timedelta(days=30), end_time)
    print(f"OK Query type recommendation: {recommended}")

    bucket = QueryGuardrails.get_optimal_bucket_interval(end_time - timedelta(days=30), end_time)
    print(f"OK Optimal bucket interval: {bucket}")
    return True


def test_time_bucket_query():
    results = TimescaleDBService.query_time_bucket(
        table_name="server_health_logs",
        time_column="timestamp",
        bucket_interval="1 hour",
        start_time=datetime.utcnow() - timedelta(hours=24),
        metrics=["cpu_usage", "memory_usage"],
    )
    print("OK Time bucket query executed")
    print(f"  Returned {len(results)} buckets")
    if results:
        print(f"  Sample bucket: {results[0]['bucket']}")
    return True


def test_continuous_aggregate_query():
    results = TimescaleDBService.query_continuous_aggregate(
        view_name="server_health_hourly_cagg",
        start_time=datetime.utcnow() - timedelta(days=7),
    )
    print("OK Continuous aggregate query executed")
    print(f"  Returned {len(results)} rows")
    if results:
        print(
            f"  Sample row: device_id={results[0].get('device_id')}, "
            f"avg_cpu={results[0].get('avg_cpu_usage')}"
        )
    return True


def test_health_report():
    report = TimescaleDBService.get_health_report()
    if not report.get("enabled"):
        print("X TimescaleDB not enabled")
        return False
    print("OK TimescaleDB health report generated")
    print(f"  Hypertables: {len(report['hypertables'])}")
    print(f"  Continuous aggregates: {len(report['continuous_aggregates'])}")
    print(f"  Jobs: {len(report['jobs'])}")
    return True


def main():
    print("\n" + "=" * 70)
    print("TimescaleDB Integration Test Suite")
    print("=" * 70)

    app = create_app()
    tests = [
        ("Test 1: Database Connection", test_database_connection),
        ("Test 2: TimescaleDB Extension", test_timescaledb_enabled),
        ("Test 3: Hypertables", test_hypertables),
        ("Test 4: Continuous Aggregates", test_continuous_aggregates),
        ("Test 5: Background Jobs", test_background_jobs),
        ("Test 6: Query Guardrails", test_query_guardrails),
        ("Test 7: Time Bucket Query", test_time_bucket_query),
        ("Test 8: Continuous Aggregate Query", test_continuous_aggregate_query),
        ("Test 9: Health Report", test_health_report),
    ]

    results = []
    with app.app_context():
        for title, fn in tests:
            results.append(_run_test(title, fn))

    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")
    print(f"Failed: {total - passed}/{total}")

    if passed == total:
        print("\nOK All tests passed. TimescaleDB integration is working correctly.")
        return 0

    print(f"\nX {total - passed} test(s) failed. Review the errors above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
