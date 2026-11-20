# Build native Cutter window (C# / .NET Framework 4.8 + WebView2).
# csc.exe ships with Windows, WebView2 DLLs come from the NuGet package.
# ASCII only on purpose: Windows PowerShell 5.1 reads non-BOM scripts as ANSI.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "app\window"
$out = Join-Path $root "build\native"
$nuget = Join-Path $root ".build\nuget\wv2"

$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { throw "csc.exe not found (.NET Framework 4.x required)" }

New-Item -ItemType Directory -Force -Path $out | Out-Null

# icon.ico
$ico = Join-Path $src "icon.ico"
if (-not (Test-Path $ico)) {
    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path $python) {
        Write-Host "[build] generating icon..."
        & $python (Join-Path $root "tools\make_icon.py")
    }
}

# WebView2 package
$wvCore = Join-Path $nuget "lib\net462\Microsoft.Web.WebView2.Core.dll"
$wvForms = Join-Path $nuget "lib\net462\Microsoft.Web.WebView2.WinForms.dll"
if (-not (Test-Path $wvCore)) {
    Write-Host "[build] downloading Microsoft.Web.WebView2..."
    $version = "1.0.2903.40"
    $zip = Join-Path $root ".build\nuget\wv2.zip"
    New-Item -ItemType Directory -Force -Path (Split-Path $zip) | Out-Null
    Invoke-WebRequest -Uri "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$version/microsoft.web.webview2.$version.nupkg" -OutFile $zip -UseBasicParsing
    Expand-Archive $zip -DestinationPath $nuget -Force
}

Write-Host "[build] compiling window..."
$cscArgs = @(
    "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
    "/out:$out\Cutter.exe",
    "/win32manifest:$src\app.manifest",
    "/r:System.dll", "/r:System.Core.dll", "/r:System.Drawing.dll",
    "/r:System.Windows.Forms.dll", "/r:System.Net.dll",
    "/r:$wvCore", "/r:$wvForms"
)
if (Test-Path $ico) { $cscArgs += "/win32icon:$ico" }
$cscArgs += (Join-Path $src "CutterWindow.cs")

& $csc $cscArgs
if ($LASTEXITCODE -ne 0) { throw "window compilation failed" }

Copy-Item $wvCore $out -Force
Copy-Item $wvForms $out -Force
Copy-Item (Join-Path $nuget "runtimes\win-x64\native\WebView2Loader.dll") $out -Force
if (Test-Path $ico) { Copy-Item $ico $out -Force }

$size = [math]::Round(((Get-ChildItem $out -File | Measure-Object Length -Sum).Sum / 1MB), 2)
Write-Host "[build] done: $out ($size MB)" -ForegroundColor Green
Get-ChildItem $out | Select-Object Name, @{n = 'KB'; e = { [math]::Round($_.Length / 1KB, 1) } } | Format-Table -AutoSize
