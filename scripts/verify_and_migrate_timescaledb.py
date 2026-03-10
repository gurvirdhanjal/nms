"""
TimescaleDB Verification and Migration Helper Script
Checks current database state and assists with TimescaleDB migration
"""
import os
import sys
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config


class TimescaleDBMigrationHelper:
    def __init__(self):
        self.db_url = Config.SQLALCHEMY_DATABASE_URI
        self.engine = None
        
    def connect(self):
        """Connect to database"""
        try:
            self.engine = create_engine(self.db_url)
            with self.engine.connect() as conn:
                result = conn.execute(text("SELECT version()"))
                version = result.scalar()
                print(f"✓ Connected to database")
                print(f"  Version: {version}")
                return True
        except OperationalError as e:
            print(f"✗ Failed to connect to database: {e}")
            return False
    
    def check_timescaledb_installed(self):
        """Check if TimescaleDB extension is installed"""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT installed_version 
                    FROM pg_available_extensions 
                    WHERE name = 'timescaledb'
                """))
                version = result.scalar()
                
                if version:
                    print(f"✓ TimescaleDB is installed (version {version})")
                    return True
                else:
                    print("✗ TimescaleDB extension is not installed")
                    return False
        except Exception as e:
            print(f"✗ Error checking TimescaleDB: {e}")
            return False
    
    def check_hypertables(self):
        """Check if tables are converted to hypertables"""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT 
                        hypertable_schema,
                        hypertable_name,
                        num_chunks
                    FROM timescaledb_information.hypertables
                    ORDER BY hypertable_name
                """))
                
                hypertables = result.fetchall()
                
                if hypertables:
                    print(f"\n✓ Found {len(hypertables)} hypertables:")
                    for ht in hypertables:
                        print(f"  - {ht[1]}: {ht[2]} chunks")
                    return True
                else:
                    print("\n✗ No hypertables found (tables not yet converted)")
                    return False
        except ProgrammingError:
            print("\n✗ TimescaleDB information schema not available")
            return False
        except Exception as e:
            print(f"\n✗ Error checking hypertables: {e}")
            return False
    
    def check_compression_policies(self):
        """Check compression policies"""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT 
                        job_id,
                        application_name,
                        schedule_interval
                    FROM timescaledb_information.jobs
                    WHERE application_name LIKE '%Compression%' OR application_name LIKE '%Columnstore%'
                """))
                
                policies = result.fetchall()
                
                if policies:
                    print(f"\n✓ Found {len(policies)} compression policies:")
                    for policy in policies:
                        print(f"  - Job {policy[0]}: {policy[1]}")
                    return True
                else:
                    print("\n✗ No compression policies configured")
                    return False
        except Exception as e:
            print(f"\n✗ Error checking compression policies: {e}")
            return False
    
    def check_continuous_aggregates(self):
        """Check continuous aggregates"""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT 
                        view_schema,
                        view_name
                    FROM timescaledb_information.continuous_aggregates
                """))
                
                caggs = result.fetchall()
                
                if caggs:
                    print(f"\n✓ Found {len(caggs)} continuous aggregates:")
                    for cagg in caggs:
                        print(f"  - {cagg[1]}")
                    return True
                else:
                    print("\n✗ No continuous aggregates configured")
                    return False
        except Exception as e:
            print(f"\n✗ Error checking continuous aggregates: {e}")
            return False
    
    def check_table_sizes(self):
        """Check current table sizes"""
        tables = [
            'server_health_logs',
            'tracking_samples',
            'device_resource_logs',
            'device_activity_logs',
            'device_application_logs'
        ]
        
        print("\n📊 Current table sizes:")
        total_size = 0
        
        for table in tables:
            try:
                with self.engine.connect() as conn:
                    result = conn.execute(text(f"""
                        SELECT 
                            COUNT(*) as row_count,
                            pg_size_pretty(pg_total_relation_size('{table}')) as size,
                            pg_total_relation_size('{table}') as bytes
                        FROM {table}
                    """))
                    
                    row = result.fetchone()
                    if row:
                        print(f"  - {table}: {row[0]:,} rows, {row[1]}")
                        total_size += row[2]
            except Exception as e:
                print(f"  - {table}: Error - {e}")
        
        print(f"\n  Total size: {self._format_bytes(total_size)}")
        return total_size
    
    def estimate_compression_savings(self, current_size):
        """Estimate storage savings after compression"""
        # TimescaleDB typically achieves 85-95% compression
        estimated_compressed = current_size * 0.10  # 90% reduction
        savings = current_size - estimated_compressed
        
        print(f"\n💾 Estimated compression savings:")
        print(f"  Current size: {self._format_bytes(current_size)}")
        print(f"  After compression: {self._format_bytes(estimated_compressed)}")
        print(f"  Savings: {self._format_bytes(savings)} (90%)")
    
    def _format_bytes(self, bytes_val):
        """Format bytes to human readable"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_val < 1024.0:
                return f"{bytes_val:.2f} {unit}"
            bytes_val /= 1024.0
        return f"{bytes_val:.2f} PB"
    
    def generate_migration_report(self):
        """Generate comprehensive migration report"""
        print("\n" + "="*70)
        print("TimescaleDB Migration Status Report")
        print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70)
        
        if not self.connect():
            print("\n❌ Cannot connect to database. Check your DATABASE_URL in .env")
            return
        
        timescaledb_installed = self.check_timescaledb_installed()
        
        if not timescaledb_installed:
            print("\n" + "="*70)
            print("📋 Next Steps:")
            print("="*70)
            print("1. Install TimescaleDB extension")
            print("   See: INSTALL_TIMESCALEDB_WINDOWS.md")
            print("\n2. Run migration script:")
            print("   psql -U monitoring_man -h 127.0.0.1 -d monitoring_db -f scripts/migrate_to_timescaledb.sql")
            print("\n3. Run this script again to verify")
            return
        
        # Check migration status
        has_hypertables = self.check_hypertables()
        has_compression = self.check_compression_policies()
        has_caggs = self.check_continuous_aggregates()
        
        # Check table sizes
        current_size = self.check_table_sizes()
        
        if current_size > 0:
            self.estimate_compression_savings(current_size)
        
        # Summary
        print("\n" + "="*70)
        print("Migration Status Summary")
        print("="*70)
        
        status = {
            "TimescaleDB Installed": "✓" if timescaledb_installed else "✗",
            "Hypertables Created": "✓" if has_hypertables else "✗",
            "Compression Policies": "✓" if has_compression else "✗",
            "Continuous Aggregates": "✓" if has_caggs else "✗"
        }
        
        for item, check in status.items():
            print(f"{check} {item}")
        
        # Recommendations
        print("\n" + "="*70)
        print("Recommendations")
        print("="*70)
        
        if not has_hypertables:
            print("⚠ Run migration script to convert tables to hypertables")
            print("  Command: psql -U monitoring_man -h 127.0.0.1 -d monitoring_db -f scripts/migrate_to_timescaledb.sql")
        
        if has_hypertables and not has_compression:
            print("⚠ Compression policies not configured")
            print("  This will be set up automatically by the migration script")
        
        if has_hypertables and not has_caggs:
            print("⚠ Continuous aggregates not created")
            print("  Run the migration script to create them")
        
        if all([has_hypertables, has_compression, has_caggs]):
            print("✓ Migration complete! Your database is optimized with TimescaleDB")
            print("\n  Next steps:")
            print("  1. Update application code to use TimescaleDB queries")
            print("  2. Monitor compression ratio after 7 days")
            print("  3. Remove manual rollup jobs from scheduler")
        
        print("\n" + "="*70)


def main():
    """Main entry point"""
    helper = TimescaleDBMigrationHelper()
    helper.generate_migration_report()


if __name__ == '__main__':
    main()
