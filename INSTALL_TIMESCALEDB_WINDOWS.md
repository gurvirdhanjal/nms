# TimescaleDB Installation Guide - Windows (PostgreSQL 18)

## Current Environment
- **PostgreSQL Version**: 18.1
- **Installation Path**: `C:\Program Files\PostgreSQL\18`
- **Database**: monitoring_db
- **User**: monitoring_man
- **Status**: PostgreSQL running, TimescaleDB not installed

---

## Installation Steps

### Step 1: Download TimescaleDB for PostgreSQL 18

**IMPORTANT**: TimescaleDB 2.x officially supports PostgreSQL 12-16. PostgreSQL 18 is very new (released Nov 2024) and may not have official TimescaleDB support yet.

**Options:**

#### Option A: Use PostgreSQL 16 (Recommended - Stable)
```powershell
# Download PostgreSQL 16 installer
# URL: https://www.enterprisedb.com/downloads/postgres-postgresql-downloads
# Install alongside PostgreSQL 18 (different port, e.g., 5433)

# Then download TimescaleDB for PostgreSQL 16
# URL: https://docs.timescale.com/install/latest/self-hosted/installation-windows/
```

#### Option B: Build TimescaleDB from Source (Advanced)
```powershell
# Requires Visual Studio 2022, CMake, Git
# Follow: https://github.com/timescale/timescaledb#building-from-source
```

#### Option C: Use Docker (Easiest for Testing)
```powershell
# Install Docker Desktop for Windows
# Run TimescaleDB container
docker run -d --name timescaledb -p 5433:5432 -e POSTGRES_PASSWORD=admin123 timescale/timescaledb:latest-pg16

# Update .env to use Docker instance
DATABASE_URL=postgresql+psycopg2://postgres:admin123@127.0.0.1:5433/postgres
```

---

## Recommended Approach: PostgreSQL 16 + TimescaleDB

### Step 1: Install PostgreSQL 16

1. Download PostgreSQL 16.x installer:
   - URL: https://www.enterprisedb.com/downloads/postgres-postgresql-downloads
   - Version: 16.x (latest stable)

2. Run installer:
   - Port: `5433` (to avoid conflict with PostgreSQL 18 on 5432)
   - Password: Use same password (`admin123`)
   - Install Stack Builder: Yes

3. Verify installation:
```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" --version
# Should show: psql (PostgreSQL) 16.x
```

### Step 2: Install TimescaleDB Extension

1. Download TimescaleDB for PostgreSQL 16:
   - URL: https://docs.timescale.com/install/latest/self-hosted/installation-windows/
   - Choose: PostgreSQL 16 Windows installer

2. Run TimescaleDB installer:
   - It will detect PostgreSQL 16 installation
   - Follow prompts to install extension

3. Tune PostgreSQL for TimescaleDB:
```powershell
& "C:\Program Files\PostgreSQL\16\bin\timescaledb-tune.exe" --quiet --yes
```

4. Restart PostgreSQL 16 service:
```powershell
Restart-Service postgresql-x64-16
```

### Step 3: Create Database and Enable Extension

```powershell
# Set password
$env:PGPASSWORD="admin123"

# Connect to PostgreSQL 16
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -h 127.0.0.1 -p 5433

# In psql prompt:
CREATE DATABASE monitoring_db;
CREATE USER monitoring_man WITH PASSWORD 'admin123';
GRANT ALL PRIVILEGES ON DATABASE monitoring_db TO monitoring_man;
\c monitoring_db
CREATE EXTENSION IF NOT EXISTS timescaledb;
\q
```

### Step 4: Migrate Data from PostgreSQL 18 to 16

```powershell
# Dump from PostgreSQL 18
$env:PGPASSWORD="admin123"
& "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" -U monitoring_man -h 127.0.0.1 -p 5432 -d monitoring_db -F c -f "backup_pg18.dump"

# Restore to PostgreSQL 16
& "C:\Program Files\PostgreSQL\16\bin\pg_restore.exe" -U monitoring_man -h 127.0.0.1 -p 5433 -d monitoring_db -c "backup_pg18.dump"
```

### Step 5: Update Application Configuration

Update `.env`:
```bash
# Change port from 5432 to 5433
DATABASE_URL=postgresql+psycopg2://monitoring_man:admin123@127.0.0.1:5433/monitoring_db
```

### Step 6: Run TimescaleDB Migration

```powershell
# Add PostgreSQL 16 to PATH temporarily
$env:Path = "C:\Program Files\PostgreSQL\16\bin;" + $env:Path

# Run migration script
$env:PGPASSWORD="admin123"
psql -U monitoring_man -h 127.0.0.1 -p 5433 -d monitoring_db -f scripts/migrate_to_timescaledb.sql
```

---

## Alternative: Docker Approach (Fastest for Testing)

### Step 1: Install Docker Desktop
- Download: https://www.docker.com/products/docker-desktop/
- Install and restart Windows

### Step 2: Run TimescaleDB Container

```powershell
# Pull and run TimescaleDB
docker run -d `
  --name timescaledb `
  -p 5433:5432 `
  -e POSTGRES_PASSWORD=admin123 `
  -e POSTGRES_USER=monitoring_man `
  -e POSTGRES_DB=monitoring_db `
  -v timescaledb-data:/var/lib/postgresql/data `
  timescale/timescaledb:latest-pg16

# Verify container is running
docker ps

# Check logs
docker logs timescaledb
```

### Step 3: Migrate Data to Docker

```powershell
# Dump from PostgreSQL 18
$env:PGPASSWORD="admin123"
& "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" -U monitoring_man -h 127.0.0.1 -p 5432 -d monitoring_db -F p -f "backup.sql"

# Restore to Docker TimescaleDB
docker exec -i timescaledb psql -U monitoring_man -d monitoring_db < backup.sql
```

### Step 4: Update Configuration

```bash
# .env
DATABASE_URL=postgresql+psycopg2://monitoring_man:admin123@127.0.0.1:5433/monitoring_db
```

### Step 5: Run Migration

```powershell
# Run migration script
docker exec -i timescaledb psql -U monitoring_man -d monitoring_db < scripts/migrate_to_timescaledb.sql
```

---

## Verification

After installation, verify TimescaleDB is working:

```powershell
$env:PGPASSWORD="admin123"
psql -U monitoring_man -h 127.0.0.1 -p 5433 -d monitoring_db

# In psql:
SELECT default_version, installed_version 
FROM pg_available_extensions 
WHERE name = 'timescaledb';

# Should show:
#     name     | default_version | installed_version
# -------------+-----------------+-------------------
#  timescaledb | 2.x.x          | 2.x.x
```

---

## Recommendation

**For Production**: Use PostgreSQL 16 + TimescaleDB (native installation)
- Most stable
- Best performance
- Official support

**For Testing/Development**: Use Docker
- Fastest setup
- Easy to reset
- Isolated environment

**Current PostgreSQL 18**: Keep for other applications, but use PostgreSQL 16 for monitoring system until TimescaleDB officially supports PG 18.

---

## Next Steps

1. Choose installation method (PostgreSQL 16 or Docker)
2. Install TimescaleDB
3. Migrate data
4. Run `scripts/migrate_to_timescaledb.sql`
5. Update application code
6. Test and verify

Which approach would you like to proceed with?
