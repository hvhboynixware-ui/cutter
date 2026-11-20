# Запуск Cutter из исходников. Первый запуск создаст venv и поставит зависимости.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:PIP_CACHE_DIR = Join-Path $PSScriptRoot ".pip-cache"
$env:TMP = Join-Path $PSScriptRoot ".tmp"
$env:TEMP = $env:TMP
New-Item -ItemType Directory -Force -Path $env:TMP, $env:PIP_CACHE_DIR | Out-Null

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "[Cutter] создаю venv..." -ForegroundColor Yellow
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "не получилось создать venv — поставь Python 3.11+" }
    & $python -m pip install --disable-pip-version-check -q -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "не получилось поставить зависимости" }
}

Write-Host "[Cutter] запускаю..." -ForegroundColor Green
& $python -m app.main @args
