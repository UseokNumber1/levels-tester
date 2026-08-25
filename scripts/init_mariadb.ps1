# One-time initialization of the project database inside the local MariaDB
# server. Reads credentials from .env, asks for the MariaDB root password
# once (it is never stored), creates the database, the application user,
# grants privileges and applies migrations/001_initial.sql.
#
# Notes:
# - The application user is forced to mysql_native_password because the
#   Python driver (pymysql) cannot handle the GSSAPI plugin that
#   MariaDB 11.8 on Windows requests by default.
# - Passwords are passed through the MYSQL_PWD environment variable, never
#   through the command line, so any characters in them are safe.
$ErrorActionPreference = "Stop"

$rootDir = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $rootDir ".env"
if (-not (Test-Path -LiteralPath $envPath)) { throw ".env not found in project root; copy .env.example first." }

$dbUrl = ((Get-Content -LiteralPath $envPath) | Where-Object { $_ -match "^\s*DATABASE_URL\s*=" }) -replace "^\s*DATABASE_URL\s*=\s*", ""
if (-not $dbUrl) { throw "DATABASE_URL is empty in .env." }
if ($dbUrl -notmatch "^mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):(\d+)/([A-Za-z0-9_]+)$") {
    throw "DATABASE_URL must be a MariaDB URL like mysql+pymysql://user:password@127.0.0.1:3306/level_tester"
}
$appUser = $Matches[1]
$appPass = $Matches[2]
$appHost = $Matches[3]
$appPort = $Matches[4]
$database = $Matches[5]

$candidates = @(
    "C:\Program Files\MariaDB*\bin\mariadb.exe",
    "C:\Program Files\MariaDB*\bin\mysql.exe",
    "C:\Program Files\MySQL\MySQL Server*\bin\mysql.exe",
    "C:\xampp\mysql\bin\mysql.exe"
)
$mariadb = $candidates | ForEach-Object { Get-Item $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
if (-not $mariadb) { throw "MariaDB client (mariadb.exe/mysql.exe) was not found." }
$mariadb = $mariadb.FullName

$secure = Read-Host "MariaDB root password" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$rootPass = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

$sql = @"
CREATE DATABASE IF NOT EXISTS $database CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$appUser'@'localhost' IDENTIFIED VIA mysql_native_password BY '$appPass';
CREATE USER IF NOT EXISTS '$appUser'@'127.0.0.1' IDENTIFIED VIA mysql_native_password BY '$appPass';
ALTER USER '$appUser'@'localhost' IDENTIFIED VIA mysql_native_password BY '$appPass';
ALTER USER '$appUser'@'127.0.0.1' IDENTIFIED VIA mysql_native_password BY '$appPass';
GRANT ALL PRIVILEGES ON $database.* TO '$appUser'@'localhost';
GRANT ALL PRIVILEGES ON $database.* TO '$appUser'@'127.0.0.1';
FLUSH PRIVILEGES;
"@

Write-Host "Creating database '$database' and native-password user '$appUser'..."
$env:MYSQL_PWD = $rootPass
try {
    # -u root is mandatory: without it the client falls back to the
    # Windows account name instead of the MariaDB root superuser.
    $sql | & $mariadb --batch -u root
    if ($LASTEXITCODE -ne 0) { throw "Database initialization failed." }

    $schemaPath = Join-Path $rootDir "migrations\001_initial.sql"
    if (Test-Path -LiteralPath $schemaPath) {
        Write-Host "Applying schema from migrations/001_initial.sql..."
        Get-Content -LiteralPath $schemaPath -Raw | & $mariadb --batch -u root $database
        if ($LASTEXITCODE -ne 0) { throw "Schema import failed." }
    }

    Write-Host "Verifying application login..."
    "SELECT CURRENT_USER(), VERSION();" | & $mariadb --batch -u $appUser $database
    if ($LASTEXITCODE -ne 0) { throw "Application user cannot log in." }
}
finally {
    Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Done. Database '$database' lives inside the MariaDB data directory."
Write-Host "Start the application with .\run.ps1 and check http://127.0.0.1:$appPort/api/status"
