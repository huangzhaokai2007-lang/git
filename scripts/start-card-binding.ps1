param([int]$Port = 8501)
$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path -Parent $PSScriptRoot
if (-not $taskRepo.StartsWith('D:\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Please extract the project into a named folder on drive D before starting.'
}
$taskRoot = Split-Path -Parent $taskRepo
$env:UV_CACHE_DIR = Join-Path $taskRoot 'cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $taskRoot 'runtimes'
$env:TEMP = Join-Path $taskRoot 'temp'
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:DB_PATH = Join-Path $taskRepo 'var\card-binding-demo.db'
New-Item -ItemType Directory -Force $env:UV_CACHE_DIR,$env:UV_PYTHON_INSTALL_DIR,$env:TEMP | Out-Null
Push-Location $taskRepo
try {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed.' }
    uv run streamlit run interfaces/web/app.py --server.headless true --server.address 127.0.0.1 --server.port $Port
} finally {
    Pop-Location
}
