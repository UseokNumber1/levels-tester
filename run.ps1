$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating virtual environment (one-time)..."
    python -m venv (Join-Path $root ".venv")
}

# Dependencies are installed only once: the check imports the full application,
# so any missing package triggers a single reinstall instead of every start.
& $venvPython -c "import level_tester.api.app" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dependencies (one-time)..."
    Push-Location $root
    & $venvPython -m pip install -e ".[dev]"
    Pop-Location
}

Write-Host "Starting Levels Tester at http://127.0.0.1:8080/"
& $venvPython -m uvicorn level_tester.api.app:app --host 127.0.0.1 --port 8080
