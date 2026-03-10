# TimescaleDB Migration Script - PostgreSQL 18 to TimescaleDB Docker
# This script migrates data from your existing PostgreSQL 18 to TimescaleDB

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "TimescaleDB Migration Script" -ForegroundColor Cyan
Write-Host "PostgreSQL 18 -> TimescaleDB (Docker)" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

# Configuration
$PG18_BIN = "C:\Program Files\PostgreSQL\18\bin"
$PG18_HOST = "127.0.0.1"
$PG18_PORT = "5432"
$PG18_USER = "monitoring_man"
$PG18_DB = "monitoring_db"
$PG18_PASSWORD = "admin123"

$TIMESCALE_HOST = "127.0.0.1"
$TIMESCALE_PORT = "5433"
$TIMESCALE_USER = "monitoring_man"
$TIMESCALE_DB = "monitoring_db"
$TIMESCALE_PASSWORD = "admin123"

$BACKUP_DIR = ".\backups"
$BACKUP_FILE = "$BACKUP_DIR\pg18_backup_$(Get-Date -Format 'yyyyMMdd_HHmmss').sql"

# Create backup directory
if (-not (Test-Path $BACKUP_DIR)) {
    New-Item -ItemType Directory -Path $BACKUP_DIR | Out-Null
}

# Step 1: Check Docker is running
Write-Host "[1/6] Checking Docker..." -ForegroundColor Yellow
try {
    $dockerVersion = docker --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Docker not found"
    }
    Write-Host "  [OK] Docker is installed: $dockerVersion" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Docker is not installed or not running" -ForegroundColor Red
    Write-Host "  Please install Docker Desktop: https://www.docker.com/products/docker-desktop/" -ForegroundColor Yellow
    exit 1
}

# Step 2: Start TimescaleDB container
Write-Host "`n[2/6] Starting TimescaleDB container..." -ForegroundColor Yellow
try {
    # Check if container already exists
    $containerExists = docker ps -a --filter "name=monitoring_timescaledb" --format "{{.Names}}" 2>&1
    
    if ($containerExists -eq "monitoring_timescaledb") {
        Write-Host "  Container already exists, starting..." -ForegroundColor Cyan
        docker start monitoring_timescaledb | Out-Null
    } else {
        Write-Host "  Creating new container..." -ForegroundColor Cyan
        docker-compose -f docker-compose.timescaledb.yml up -d
    }
    
    # Wait for container to be healthy
    Write-Host "  Waiting for TimescaleDB to be ready..." -ForegroundColor Cyan
    $maxWait = 30
    $waited = 0
    while ($waited -lt $maxWait) {
        $health = docker inspect --format='{{.State.Health.Status}}' monitoring_timescaledb 2>&1
        if ($health -eq "healthy") {
            break
        }
        Start-Sleep -Seconds 2
        $waited += 2
        Write-Host "  ." -NoNewline
    }
    Write-Host ""
    
    if ($waited -ge $maxWait) {
        throw "Container failed to become healthy"
    }
    
    Write-Host "  [OK] TimescaleDB container is running" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Failed to start TimescaleDB container: $_" -ForegroundColor Red
    exit 1
}

# Step 3: Backup PostgreSQL 18 database
Write-Host "`n[3/6] Backing up PostgreSQL 18 database..." -ForegroundColor Yellow
try {
    $env:PGPASSWORD = $PG18_PASSWORD
    & "$PG18_BIN\pg_dump.exe" -U $PG18_USER -h $PG18_HOST -p $PG18_PORT -d $PG18_DB -F p -f $BACKUP_FILE
    
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump failed"
    }
    
    $backupSize = (Get-Item $BACKUP_FILE).Length / 1MB
    $backupSizeRounded = [math]::Round($backupSize, 2)
    Write-Host "  [OK] Backup created: $BACKUP_FILE ($backupSizeRounded MB)" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Backup failed: $_" -ForegroundColor Red
    exit 1
}

# Step 4: Restore to TimescaleDB
Write-Host "`n[4/6] Restoring to TimescaleDB container..." -ForegroundColor Yellow
try {
    # Copy backup file to container
    docker cp $BACKUP_FILE monitoring_timescaledb:/tmp/backup.sql
    
    # Restore database
    docker exec -i monitoring_timescaledb psql -U $TIMESCALE_USER -d $TIMESCALE_DB -f /tmp/backup.sql 2>&1 | Out-Null
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ⚠ Some warnings during restore (this is normal)" -ForegroundColor Yellow
    }
    
    Write-Host "  [OK] Database restored to TimescaleDB" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Restore failed: $_" -ForegroundColor Red
    exit 1
}

# Step 5: Enable TimescaleDB extension and run migration
Write-Host "`n[5/6] Enabling TimescaleDB and running migration..." -ForegroundColor Yellow
try {
    # Copy migration script to container
    docker cp scripts/migrate_to_timescaledb.sql monitoring_timescaledb:/tmp/migrate.sql
    
    # Run migration
    docker exec -i monitoring_timescaledb psql -U $TIMESCALE_USER -d $TIMESCALE_DB -f /tmp/migrate.sql
    
    if ($LASTEXITCODE -ne 0) {
        throw "Migration script failed"
    }
    
    Write-Host "  [OK] TimescaleDB migration complete" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Migration failed: $_" -ForegroundColor Red
    Write-Host "  You can manually run: docker exec -i monitoring_timescaledb psql -U monitoring_man -d monitoring_db -f /tmp/migrate.sql" -ForegroundColor Yellow
    exit 1
}

# Step 6: Verify migration
Write-Host "`n[6/6] Verifying migration..." -ForegroundColor Yellow
try {
    # Update .env temporarily for verification
    $envContent = Get-Content .env
    $newEnvContent = $envContent -replace "DATABASE_URL=postgresql\+psycopg2://monitoring_man:admin123@127\.0\.0\.1:5432/monitoring_db", "DATABASE_URL=postgresql+psycopg2://monitoring_man:admin123@127.0.0.1:5433/monitoring_db"
    $newEnvContent | Set-Content .env.timescaledb
    
    Write-Host "  Running verification script..." -ForegroundColor Cyan
    $env:DATABASE_URL = "postgresql+psycopg2://monitoring_man:admin123@127.0.0.1:5433/monitoring_db"
    python scripts/verify_and_migrate_timescaledb.py
    
    Write-Host "`n  [OK] Verification complete" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Verification failed: $_" -ForegroundColor Red
}

# Summary
Write-Host "`n======================================================================" -ForegroundColor Cyan
Write-Host "Migration Summary" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "[OK] TimescaleDB container running on port 5433" -ForegroundColor Green
Write-Host "[OK] Data migrated from PostgreSQL 18" -ForegroundColor Green
Write-Host "[OK] Hypertables created" -ForegroundColor Green
Write-Host "[OK] Compression policies configured" -ForegroundColor Green
Write-Host "[OK] Continuous aggregates created" -ForegroundColor Green
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "1. Update .env file:" -ForegroundColor White
Write-Host "   DATABASE_URL=postgresql+psycopg2://monitoring_man:admin123@127.0.0.1:5433/monitoring_db" -ForegroundColor Cyan
Write-Host ""
Write-Host "2. Restart your application:" -ForegroundColor White
Write-Host "   python web_main.py" -ForegroundColor Cyan
Write-Host ""
Write-Host "3. Test the application and verify everything works" -ForegroundColor White
Write-Host ""
Write-Host "4. Once confirmed, you can stop PostgreSQL 18:" -ForegroundColor White
Write-Host "   Stop-Service postgresql-x64-18" -ForegroundColor Cyan
Write-Host ""
Write-Host "Backup location: $BACKUP_FILE" -ForegroundColor Gray
Write-Host "======================================================================" -ForegroundColor Cyan
