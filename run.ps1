# Cutter launcher (PowerShell). ASCII only: Windows PowerShell 5.1 reads non-BOM scripts as ANSI.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:PIP_CACHE_DIR = Join-Path $PSScriptRoot ".pip-cache"
$env:TMP = Join-Path $PSScriptRoot ".tmp"
$env:TEMP = $env:TMP
New-Item -ItemType Directory -Force -Path $env:TMP, $env:PIP_CACHE_DIR | Out-Null

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "[Cutter] creating venv..." -ForegroundColor Yellow
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed - install Python 3.11+" }
    & $python -m pip install --disable-pip-version-check -q -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }
}

Write-Host "[Cutter] starting..." -ForegroundColor Green
& $python -m app.main @args
