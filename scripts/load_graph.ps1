# load_graph.ps1
# PowerShell script to load environment variables and ingest dependency metadata into Neo4j.

param (
    [string]$JsonPath = "impactgraph_schema_sample.json"
)

# 1. Check for .env file
$EnvFile = Join-Path $PSScriptRoot "..\.env"
$EnvExampleFile = Join-Path $PSScriptRoot "..\.env.example"

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExampleFile) {
        Write-Host "Creating .env file from .env.example..." -ForegroundColor Yellow
        Copy-Item $EnvExampleFile $EnvFile
    } else {
        Write-Error "No .env or .env.example file found. Please create .env with Neo4j credentials."
        exit 1
    }
}

# 2. Parse environment variables into PowerShell session env
Write-Host "Loading environment variables..." -ForegroundColor Cyan
Get-Content $EnvFile | Where-Object { $_ -match '=' -and -not $_.StartsWith('#') } | ForEach-Object {
    $parts = $_ -split '=', 2
    $key = $parts[0].Trim()
    $value = $parts[1].Trim()
    [System.Environment]::SetEnvironmentVariable($key, $value)
}

# 3. Check JSON file existence
$FullPath = Resolve-Path $JsonPath -ErrorAction SilentlyContinue
if (-not $FullPath) {
    # Try resolving relative to workspace root
    $FullPath = Resolve-Path (Join-Path $PSScriptRoot "..\" $JsonPath) -ErrorAction SilentlyContinue
}

if (-not $FullPath) {
    Write-Error "JSON input file not found: $JsonPath"
    exit 1
}

Write-Host "Found input file: $FullPath" -ForegroundColor Green

# 4. Trigger Python Ingestion Script
Write-Host "Starting Graph Engine Ingestion..." -ForegroundColor Cyan
$PythonPath = Join-Path $PSScriptRoot "..\venv\Scripts\python.exe"

if (-not (Test-Path $PythonPath)) {
    Write-Host "Local virtualenv python not found, falling back to global 'python' command..." -ForegroundColor Yellow
    $PythonPath = "python"
}

# Run the script
& $PythonPath -m graph.ingest $FullPath

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n===============================================" -ForegroundColor Green
    Write-Host "Graph ingestion completed successfully!" -ForegroundColor Green
    Write-Host "You can now query Neo4j at: http://localhost:7474" -ForegroundColor Cyan
    Write-Host "===============================================" -ForegroundColor Green
} else {
    Write-Error "Graph Ingestion script failed with exit code $LASTEXITCODE"
    exit 1
}
