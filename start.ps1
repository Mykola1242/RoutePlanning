$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python environment' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependencies failed' }
if (-not (Test-Path 'frontend\dist\index.html')) {
    Push-Location frontend
    try {
        npm.cmd ci --cache .npm-cache
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies failed' }
        npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally { Pop-Location }
}
Write-Host 'Open http://127.0.0.1:8000 in your browser. Ctrl+C stops the server.'
& '.\.venv\Scripts\python.exe' main.py
