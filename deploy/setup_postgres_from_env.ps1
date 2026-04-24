param(
    [string]$EnvFile,
    [string]$AdminUser = "postgres",
    [string]$AdminDatabase = "postgres",
    [switch]$EnableTimescale,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Fail {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
    exit 1
}

function Resolve-EnvFilePath {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        $resolved = Resolve-Path -LiteralPath $RequestedPath -ErrorAction SilentlyContinue
        if (-not $resolved) {
            Fail "Env file not found: $RequestedPath"
        }
        return $resolved.Path
    }

    $repoRoot = Split-Path -Parent $PSScriptRoot
    $candidates = @(
        (Join-Path $repoRoot ".env"),
        (Join-Path $repoRoot "dist\web_main\.env"),
        (Join-Path $repoRoot "dist\NMSAdminServer\.env"),
        "C:\NMS\NMSAdminServer\.env"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    Fail "No .env file found. Pass one explicitly: deploy\setup_postgres_from_env.bat -EnvFile path\to\.env"
}

function Get-DotEnvValue {
    param(
        [string]$FilePath,
        [string]$Key
    )

    foreach ($rawLine in [System.IO.File]::ReadLines($FilePath)) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        $separatorIndex = $line.IndexOf("=")
        if ($separatorIndex -lt 1) {
            continue
        }
        $name = $line.Substring(0, $separatorIndex).Trim()
        if ($name -ne $Key) {
            continue
        }
        $value = $line.Substring($separatorIndex + 1).Trim()
        if (
            $value.Length -ge 2 -and
            (
                ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))
            )
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        return $value
    }

    return $null
}

function Find-PostgresBinary {
    param([string]$BinaryName)

    $command = Get-Command $BinaryName -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $paths = @(
        "C:\Program Files\PostgreSQL\17\bin\$BinaryName.exe",
        "C:\Program Files\PostgreSQL\16\bin\$BinaryName.exe",
        "C:\Program Files\PostgreSQL\15\bin\$BinaryName.exe",
        "C:\Program Files\TimescaleDB\postgresql-17\bin\$BinaryName.exe",
        "C:\Program Files\TimescaleDB\postgresql-16\bin\$BinaryName.exe",
        "C:\Program Files\TimescaleDB\postgresql-15\bin\$BinaryName.exe"
    )

    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path) {
            return $path
        }
    }

    Fail "$BinaryName.exe not found. Install PostgreSQL client tools or add them to PATH."
}

function ConvertTo-PlainText {
    param([Security.SecureString]$SecureString)

    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureString)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Get-AdminPassword {
    if ($env:PG_SUPERPASSWORD) {
        return $env:PG_SUPERPASSWORD
    }

    $prompt = "PostgreSQL admin password for $AdminUser"
    $secure = Read-Host -Prompt $prompt -AsSecureString
    return (ConvertTo-PlainText -SecureString $secure)
}

function Quote-SqlLiteral {
    param([string]$Value)
    return $Value.Replace("'", "''")
}

function Quote-SqlIdentifier {
    param([string]$Value)
    return '"' + $Value.Replace('"', '""') + '"'
}

$envFilePath = Resolve-EnvFilePath -RequestedPath $EnvFile
Write-Step "Using env file: $envFilePath"

$databaseUrl = Get-DotEnvValue -FilePath $envFilePath -Key "DATABASE_URL"
if (-not $databaseUrl) {
    Fail "DATABASE_URL is missing from $envFilePath"
}

if (-not $databaseUrl.ToLowerInvariant().StartsWith("postgresql")) {
    Fail "DATABASE_URL must point to PostgreSQL. Current value starts with: $($databaseUrl.Split('://')[0])"
}

try {
    $dbUri = [System.Uri]$databaseUrl
}
catch {
    Fail "DATABASE_URL is not a valid URI: $databaseUrl"
}

$appHost = if ($dbUri.Host) { $dbUri.Host } else { "localhost" }
$appPort = if ($dbUri.IsDefaultPort) { 5432 } else { $dbUri.Port }
$dbName = $dbUri.AbsolutePath.TrimStart("/")
if (-not $dbName) {
    Fail "Could not determine database name from DATABASE_URL"
}

$userInfoParts = $dbUri.UserInfo.Split(":", 2)
$appUser = if ($userInfoParts.Count -ge 1) { [System.Uri]::UnescapeDataString($userInfoParts[0]) } else { "" }
$appPassword = if ($userInfoParts.Count -eq 2) { [System.Uri]::UnescapeDataString($userInfoParts[1]) } else { "" }

if (-not $appUser) {
    Fail "Could not determine database user from DATABASE_URL"
}

Write-Host ""
Write-Host "PostgreSQL target derived from DATABASE_URL" -ForegroundColor White
Write-Host "  Host     : $appHost" -ForegroundColor Gray
Write-Host "  Port     : $appPort" -ForegroundColor Gray
Write-Host "  Database : $dbName" -ForegroundColor Gray
Write-Host "  App User : $appUser" -ForegroundColor Gray
Write-Host ""

if ($DryRun) {
    Write-Ok "Dry run only. No changes were made."
    exit 0
}

$psqlPath = Find-PostgresBinary -BinaryName "psql"
$pgIsReadyPath = Find-PostgresBinary -BinaryName "pg_isready"

$adminPassword = Get-AdminPassword
if (-not $adminPassword) {
    Fail "Admin password cannot be empty."
}

$env:PGPASSWORD = $adminPassword

Write-Step "Checking PostgreSQL connectivity..."
& $pgIsReadyPath -h $appHost -p $appPort -U $AdminUser | Out-Null
if ($LASTEXITCODE -ne 0) {
    Fail "Could not connect to PostgreSQL at $appHost`:$appPort as $AdminUser"
}
Write-Ok "PostgreSQL is reachable."

$appUserSql = Quote-SqlLiteral $appUser
$appPasswordSql = Quote-SqlLiteral $appPassword
$dbNameSql = Quote-SqlLiteral $dbName
$appUserIdent = Quote-SqlIdentifier $appUser
$dbNameIdent = Quote-SqlIdentifier $dbName

function Invoke-PsqlScalar {
    param(
        [string]$Database,
        [string]$Sql
    )

    $result = & $psqlPath -tA -h $appHost -p $appPort -U $AdminUser -d $Database -c $Sql 2>$null
    if ($LASTEXITCODE -ne 0) {
        Fail "psql query failed: $Sql"
    }
    return ($result | Out-String).Trim()
}

function Invoke-PsqlNonQuery {
    param(
        [string]$Database,
        [string]$Sql,
        [string]$FailureMessage
    )

    & $psqlPath -v ON_ERROR_STOP=1 -h $appHost -p $appPort -U $AdminUser -d $Database -c $Sql
    if ($LASTEXITCODE -ne 0) {
        Fail $FailureMessage
    }
}

Write-Step "Creating or updating PostgreSQL role..."
$roleExists = Invoke-PsqlScalar -Database $AdminDatabase -Sql "SELECT 1 FROM pg_roles WHERE rolname = '$appUserSql';"
if ($roleExists -eq "1") {
    Invoke-PsqlNonQuery -Database $AdminDatabase -Sql "ALTER ROLE $appUserIdent LOGIN PASSWORD '$appPasswordSql';" -FailureMessage "Failed to update role $appUser"
    Write-Ok "Updated existing role: $appUser"
}
else {
    Invoke-PsqlNonQuery -Database $AdminDatabase -Sql "CREATE ROLE $appUserIdent LOGIN PASSWORD '$appPasswordSql';" -FailureMessage "Failed to create role $appUser"
    Write-Ok "Created role: $appUser"
}

$dbExists = Invoke-PsqlScalar -Database $AdminDatabase -Sql "SELECT 1 FROM pg_database WHERE datname = '$dbNameSql';"
if ($dbExists -eq "1") {
    Invoke-PsqlNonQuery -Database $AdminDatabase -Sql "ALTER DATABASE $dbNameIdent OWNER TO $appUserIdent;" -FailureMessage "Failed to update owner for database $dbName"
    Write-Ok "Database already exists: $dbName"
}
else {
    Write-Step "Creating PostgreSQL database..."
    Invoke-PsqlNonQuery -Database $AdminDatabase -Sql "CREATE DATABASE $dbNameIdent OWNER $appUserIdent;" -FailureMessage "Failed to create database $dbName"
    Write-Ok "Created database: $dbName"
}

Invoke-PsqlNonQuery -Database $AdminDatabase -Sql "GRANT ALL PRIVILEGES ON DATABASE $dbNameIdent TO $appUserIdent;" -FailureMessage "Failed to grant privileges on $dbName"
Write-Ok "Granted database privileges to $appUser"

$timescaleCheckSql = "SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb';"
$timescaleResult = & $psqlPath -tA -h $appHost -p $appPort -U $AdminUser -d $dbName -c $timescaleCheckSql 2>$null
$timescaleAvailable = ($LASTEXITCODE -eq 0 -and ($timescaleResult | Out-String).Trim() -eq "1")

if ($EnableTimescale) {
    if ($timescaleAvailable) {
        Write-Step "Enabling TimescaleDB extension..."
        & $psqlPath -v ON_ERROR_STOP=1 -h $appHost -p $appPort -U $AdminUser -d $dbName -c "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "TimescaleDB extension is enabled."
        }
        else {
            Write-Warn "TimescaleDB extension was detected but could not be enabled."
        }
    }
    else {
        Write-Warn "TimescaleDB extension is not available on this PostgreSQL instance. The app will run on plain PostgreSQL."
    }
}
elseif ($timescaleAvailable) {
    Write-Host "TimescaleDB is available but was not enabled. That's fine for a plain PostgreSQL deployment." -ForegroundColor Gray
    Write-Host "If you want it later, rerun with: deploy\setup_postgres_from_env.bat -EnableTimescale" -ForegroundColor Gray
}
else {
    Write-Host "TimescaleDB is not installed. The app will run on plain PostgreSQL." -ForegroundColor Gray
}

Write-Host ""
Write-Ok "PostgreSQL provisioning is complete."
Write-Host "Next step: start the admin server. On first app start it will create tables and seed the default admin user." -ForegroundColor White
Write-Host ""

Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
