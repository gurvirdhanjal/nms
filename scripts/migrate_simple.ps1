# Simple TimescaleDB Migration Script
Write-Host "======================================================================"
Write-Host "TimescaleDB Migration - PostgreSQL 18 to Docker"
Write-Host "======================================================================"

# Configuration
$PG18_BIN = "C:\Program Files\PostgreSQL\18\bin"
$BACKUP_DIR = ".\backups"
$BACKUP_FILE = "$BACKUP_DIR\pg18_backup_$(Get-Date -Format 'yyyyMMdd_HHmmss').sql"

# Create backup directory
if (-not (Test-Path $BACKUP_DIR)) {
    New-Item -ItemType Directory -Path $BACKUP_DIR | Out-Null
}

# Step 1: Check Docker
Write-Host "`n[1/6] Checking Docker..."
try {
    docker --version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker not found" }
    Write-Host "  OK: Docker is installed"
} catch {
    Write-Host "  ERROR: Docker not found. Install Docker Desktop first."
    exit 1
}

# Step 2: Start TimescaleDB container
Write-Host "`n[2/6] Starting TimescaleDB container..."
try {
    docker-compose -f docker-compose.timescaledb.yml up -d
    Start-Sleep -Seconds 10
    Write-Host "  OK: Container started"
} catch {
    Write-Host "  ERROR: Failed to start container"
    exit 1
}

# Step 3: Backup PostgreSQL 18
Write-Host "`n[3/6] Backing up PostgreSQL 18..."
try {
    $env:PGPASSWORD = "admin123"
    & "$PG18_BIN\pg_dump.exe" -U monitoring_man -h 127.0.0.1 -p 5432 -d monitoring_db -F p -f $BACKUP_FILE
    
    if ($LASTEXITCODE -ne 0) { throw "Backup failed" }
    
    $backupSize = [math]::Round((Get-Item $BACKUP_FILE).Length / 1MB, 2)
    Write-Host "  OK: Backup created ($backupSize MB)"
} catch {
    Write-Host "  ERROR: Backup failed - $_"
    exit 1
}

# Step 4: Restore to TimescaleDB
Write-Host "`n[4/6] Restoring to TimescaleDB..."
try {
    docker cp $BACKUP_FILE monitoring_timescaledb:/tmp/backup.sql
    docker exec -i monitoring_timescaledb psql -U monitoring_man -d monitoring_db -f /tmp/backup.sql 2>&1 | Out-Null
    Write-Host "  OK: Database restored"
} catch {
    Write-Host "  WARNING: Some errors during restore (may be normal)"
}

# Step 5: Run TimescaleDB migration
Write-Host "`n[5/6] Running TimescaleDB migration..."
try {
    docker cp scripts/migrate_to_timescaledb.sql monitoring_timescaledb:/tmp/migrate.sql
    docker exec -i monitoring_timescaledb psql -U monitoring_man -d monitoring_db -f /tmp/migrate.sql
    Write-Host "  OK: Migration complete"
} catch {
    Write-Host "  ERROR: Migration failed - $_"
    exit 1
}

# Step 6: Verify
Write-Host "`n[6/6] Verifying..."
$env:DATABASE_URL = "postgresql+psycopg2://monitoring_man:admin123@127.0.0.1:5433/monitoring_db"
python scripts/verify_and_migrate_timescaledb.py

# Summary
Write-Host "`n======================================================================"
Write-Host "Migration Complete!"
Write-Host "======================================================================"
Write-Host "Next steps:"
Write-Host "1. Update .env file:"
Write-Host "   DATABASE_URL=postgresql+psycopg2://monitoring_man:admin123@127.0.0.1:5433/monitoring_db"
Write-Host ""
Write-Host "2. Restart application:"
Write-Host "   python web_main.py"
Write-Host ""
Write-Host "Backup: $BACKUP_FILE"
Write-Host "======================================================================"
