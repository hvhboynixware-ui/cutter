# Собирает готовое приложение Cutter в dist\Cutter\ (окно + бэкенд + ffmpeg).
# ASCII only: Windows PowerShell 5.1 читает скрипты без BOM как ANSI.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "dist"
$target = Join-Path $dist "Cutter"
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) { throw "venv not found: $python" }

Write-Host "[build] 1/3 native window" -ForegroundColor Cyan
powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "tools\build_window.ps1")
if ($LASTEXITCODE -ne 0) { throw "window build failed" }

Write-Host "[build] 2/3 backend (PyInstaller)" -ForegroundColor Cyan
Push-Location $root
try {
    $env:PIP_CACHE_DIR = Join-Path $root ".pip-cache"
    $env:TMP = Join-Path $root ".tmp"
    $env:TEMP = $env:TMP
    & $python -m PyInstaller --noconfirm --clean --distpath $dist --workpath (Join-Path $root ".build\pyi") (Join-Path $root "tools\cutter.spec")
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }
} finally {
    Pop-Location
}

Write-Host "[build] 3/3 window files into dist" -ForegroundColor Cyan
$native = Join-Path $root "build\native"
Copy-Item (Join-Path $native "Cutter.exe") $target -Force
Copy-Item (Join-Path $native "Microsoft.Web.WebView2.Core.dll") $target -Force
Copy-Item (Join-Path $native "Microsoft.Web.WebView2.WinForms.dll") $target -Force
Copy-Item (Join-Path $native "WebView2Loader.dll") $target -Force
Copy-Item (Join-Path $native "icon.ico") $target -Force

$size = [math]::Round(((Get-ChildItem $target -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "[build] done: $target ($size MB)" -ForegroundColor Green
Write-Host "[build] run: $(Join-Path $target 'Cutter.exe')"
