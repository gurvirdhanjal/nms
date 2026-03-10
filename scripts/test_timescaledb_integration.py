"""
Test TimescaleDB Integration
Quick test to verify application works with TimescaleDB
"""
import sys
import os
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from sqlalchemy import create_engine, text
from services.timescaledb_service import TimescaleDBService
from services.query_guardrails import QueryGuardrails


def test_database_connection():
    """Test basic database connection"""
    print("\n" + "="*70)
    print("Test 1: Database Connection")
    print("="*70)
    
    try:
        engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.scalar()
            print(f"✓ Connected to database")
            print(f"  Version: {version[:80]}...")
            return True
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return False


def test_timescaledb_enabled():
    """Test TimescaleDB is enabled"""
    print("\n" + "="*70)
    print("Test 2: TimescaleDB Extension")
    print("="*70)
    
    try:
        enabled = TimescaleDBService.is_timescaledb_enabled()
        if enabled:
            print("✓ TimescaleDB extension is enabled")
            return True
        else:
            print("✗ TimescaleDB extension is not enabled")
            return False
    except Exception as e:
        print(f"✗ Error checking TimescaleDB: {e}")
        return False


def test_hypertables():
    """Test hypertables exist"""
    print("\n" + "="*70)
    print("Test 3: Hypertables")
    print("="*70)
    
    try:
        hypertables = TimescaleDBService.get_hypertable_info()
        if hypertables:
            print(f"✓ Found {len(hypertables)} hypertables:")
            for ht in hypertables:
                print(f"  - {ht['hypertable_name']}: {ht['num_chunks']} chunks")
            return True
        else:
            print("✗ No hypertables found")
            return False
    except Exception as e:
        print(f"✗ Error checking hypertables: {e}")
        return False


def test_continuous_aggregates():
    """Test continuous aggregates exist"""
    print("\n" + "="*70)
    print("Test 4: Continuous Aggregates")
    print("="*70)
    
    try:
        caggs = TimescaleDBService.get_continuous_aggregate_stats()
        if caggs:
            print(f"✓ Found {len(caggs)} continuous aggregates:")
            for cagg in caggs:
                print(f"  - {cagg['view_name']}")
            return True
        else:
            print("✗ No continuous aggregates found")
            return False
    except Exception as e:
        print(f"✗ Error checking continuous aggregates: {e}")
        return False


def test_background_jobs():
    """Test background jobs are configured"""
    print("\n" + "="*70)
    print("Test 5: Background Jobs")
    print("="*70)
    
    try:
        jobs = TimescaleDBService.get_job_stats()
        if jobs:
            print(f"✓ Found {len(jobs)} background jobs:")
            compression_jobs = [j for j in jobs if 'Columnstore' in j['application_name']]
            retention_jobs = [j for j in jobs if 'Retention' in j['application_name']]
            refresh_jobs = [j for j in jobs if 'Refresh' in j['application_name']]
            
            print(f"  - Compression policies: {len(compression_jobs)}")
            print(f"  - Retention policies: {len(retention_jobs)}")
            print(f"  - Refresh policies: {len(refresh_jobs)}")
            return True
        else:
            print("✗ No background jobs found")
            return False
    except Exception as e:
        print(f"✗ Error checking background jobs: {e}")
        return False


def test_query_guardrails():
    """Test query guardrails"""
    print("\n" + "="*70)
    print("Test 6: Query Guardrails")
    print("="*70)
    
    try:
        # Test valid time range
        start_time = datetime.utcnow() - timedelta(days=1)
        end_time = datetime.utcnow()
        
        validated_start, validated_end = QueryGuardrails.validate_time_range(
            start_time, end_time, query_type='raw'
        )
        print("✓ Valid time range accepted")
        
        # Test invalid time range (should raise error)
        try:
            start_time = datetime.utcnow() - timedelta(days=30)
            QueryGuardrails.validate_time_range(
                start_time, end_time, query_type='raw'
            )
            print("✗ Invalid time range was not rejected")
            return False
        except ValueError:
            print("✓ Invalid time range rejected correctly")
        
        # Test query type recommendation
        start_time = datetime.utcnow() - timedelta(days=30)
        recommended = QueryGuardrails.recommend_query_type(start_time, end_time)
        print(f"✓ Query type recommendation: {recommended}")
        
        # Test bucket interval
        bucket = QueryGuardrails.get_optimal_bucket_interval(start_time, end_time)
        print(f"✓ Optimal bucket interval: {bucket}")
        
        return True
    except Exception as e:
        print(f"✗ Error testing query guardrails: {e}")
        return False


def test_time_bucket_query():
    """Test time_bucket query"""
    print("\n" + "="*70)
    print("Test 7: Time Bucket Query")
    print("="*70)
    
    try:
        # Query last 24 hours with 1-hour buckets
        start_time = datetime.utcnow() - timedelta(hours=24)
        
        results = TimescaleDBService.query_time_bucket(
            table_name='server_health_logs',
            time_column='timestamp',
            bucket_interval='1 hour',
            start_time=start_time,
            metrics=['cpu_usage', 'memory_usage']
        )
        
        print(f"✓ Time bucket query executed")
        print(f"  Returned {len(results)} buckets")
        
        if results:
            print(f"  Sample bucket: {results[0]['bucket']}")
        
        return True
    except Exception as e:
        print(f"✗ Error executing time bucket query: {e}")
        return False


def test_continuous_aggregate_query():
    """Test continuous aggregate query"""
    print("\n" + "="*70)
    print("Test 8: Continuous Aggregate Query")
    print("="*70)
    
    try:
        # Query hourly aggregate
        start_time = datetime.utcnow() - timedelta(days=7)
        
        results = TimescaleDBService.query_continuous_aggregate(
            view_name='server_health_hourly_cagg',
            start_time=start_time
        )
        
        print(f"✓ Continuous aggregate query executed")
        print(f"  Returned {len(results)} rows")
        
        if results:
            print(f"  Sample row: device_id={results[0].get('device_id')}, "
                  f"avg_cpu={results[0].get('avg_cpu_usage')}")
        
        return True
    except Exception as e:
        print(f"✗ Error executing continuous aggregate query: {e}")
        return False


def test_health_report():
    """Test health report"""
    print("\n" + "="*70)
    print("Test 9: Health Report")
    print("="*70)
    
    try:
        report = TimescaleDBService.get_health_report()
        
        if report['enabled']:
            print("✓ TimescaleDB health report generated")
            print(f"  Hypertables: {len(report['hypertables'])}")
            print(f"  Continuous aggregates: {len(report['continuous_aggregates'])}")
            print(f"  Jobs: {len(report['jobs'])}")
            return True
        else:
            print("✗ TimescaleDB not enabled")
            return False
    except Exception as e:
        print(f"✗ Error generating health report: {e}")
        return False


def main():
    """Run all tests"""
    print("\n" + "="*70)
    print("TimescaleDB Integration Test Suite")
    print("="*70)
    
    tests = [
        test_database_connection,
        test_timescaledb_enabled,
        test_hypertables,
        test_continuous_aggregates,
        test_background_jobs,
        test_query_guardrails,
        test_time_bucket_query,
        test_continuous_aggregate_query,
        test_health_report
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"\n✗ Test failed with exception: {e}")
            results.append(False)
    
    # Summary
    print("\n" + "="*70)
    print("Test Summary")
    print("="*70)
    
    passed = sum(results)
    total = len(results)
    
    print(f"Passed: {passed}/{total}")
    print(f"Failed: {total - passed}/{total}")
    
    if passed == total:
        print("\n✓ All tests passed! TimescaleDB integration is working correctly.")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) failed. Please review the errors above.")
        return 1


if __name__ == '__main__':
    sys.exit(main())
