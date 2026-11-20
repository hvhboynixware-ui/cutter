# Запуск собранного приложения с логом (ASCII only).
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "dist\Cutter\Cutter.exe"
$log = Join-Path $root ".build\app_test.log"

function Say($text) {
    $line = "[test] " + $text
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding UTF8
}

Set-Content -Path $log -Value "" -Encoding UTF8
Say "exe: $exe"

$env:CUTTER_DATA_DIR = Join-Path $root ".userdata"
Remove-Item $env:CUTTER_DATA_DIR -Recurse -Force -ErrorAction SilentlyContinue
Say "CUTTER_DATA_DIR=$env:CUTTER_DATA_DIR"

$app = Start-Process -FilePath $exe -PassThru
Say "started pid=$($app.Id)"

for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 1
    $h = Get-Process -Id $app.Id -ErrorAction SilentlyContinue
    if (-not $h) { Say "app exited at second $i"; break }
    $backend = Get-Process cutter_backend -ErrorAction SilentlyContinue
    $state = "second ${i}: backend=" + [bool]$backend
    if ($i % 5 -eq 0) { Say $state }
}

$backend = Get-Process cutter_backend -ErrorAction SilentlyContinue
Say "backend running: $([bool]$backend)"

try {
    $health = Invoke-WebRequest "http://127.0.0.1:8765/api/health" -UseBasicParsing -TimeoutSec 5
    Say "health: $($health.StatusCode)"
    Add-Content -Path $log -Value $health.Content -Encoding UTF8
} catch {
    Say "health failed: $($_.Exception.Message)"
}

$wlog = Join-Path $root "dist\Cutter\data\logs\window.log"
if (Test-Path $wlog) { Say "window.log:"; Get-Content $wlog | Select-Object -Last 10 | ForEach-Object { Add-Content -Path $log -Value $_ } } else { Say "no window.log" }

Say "done"
