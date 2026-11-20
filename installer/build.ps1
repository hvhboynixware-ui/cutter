<#
.SYNOPSIS
    Собирает MSI-инсталлятор Cutter: heat + candle + light.

.DESCRIPTION
    Одна команда из корня репозитория:
        powershell -ExecutionPolicy Bypass -File installer\build.ps1

    Что делает:
      1. Проверяет/при необходимости скачивает portable WiX v3.11 в tools\wix.
      2. Проверяет dist\Cutter (сборка PyInstaller: Cutter.exe + файлы).
      3. heat.exe собирает список файлов -> installer\obj\Files.wxs.
      4. candle.exe компилирует Cutter.wxs + Files.wxs (arch x64).
      5. light.exe линкует MSI -> dist\Cutter-<Version>-x64.msi.

    Лог: installer\build.log (перезаписывается каждым запуском).

    ВАЖНО про TEMP: light.exe на WiX 3.11 зависает, если TMP/TEMP указывает
    на путь с не-ASCII символами (например, "C:\Users\<кириллица>\...\Temp").
    Скрипт сам переключает TEMP на ASCII-каталог (%LOCALAPPDATA%\Temp\cutter-wix-tmp).

.PARAMETER Version
    Версия продукта (x.y.z). По умолчанию 1.0.0.

.PARAMETER DistDir
    Папка с one-folder сборкой приложения. По умолчанию <repo>\dist\Cutter.

.PARAMETER SkipIcon
    Не генерировать installer\icon.ico, если его нет (тогда сборка упадёт).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Version 1.0.1
#>
[CmdletBinding()]
param(
    [string]$Version = '1.0.0',
    [string]$DistDir = '',
    [switch]$SkipIcon
)

$ErrorActionPreference = 'Stop'

$scriptDir = $PSScriptRoot
$repoRoot  = Split-Path -Parent $scriptDir
if ([string]::IsNullOrWhiteSpace($DistDir)) { $DistDir = Join-Path $repoRoot 'dist\Cutter' }
$wixDir    = Join-Path $repoRoot 'tools\wix'
$objDir    = Join-Path $scriptDir 'obj'
$logPath   = Join-Path $scriptDir 'build.log'
$outDir    = Join-Path $repoRoot 'dist'
$outMsi    = Join-Path $outDir ("Cutter-{0}-x64.msi" -f $Version)
$wixUrl    = 'https://github.com/wixtoolset/wix3/releases/download/wix3112rtm/wix311-binaries.zip'

if (Test-Path $logPath) { Remove-Item -LiteralPath $logPath -Force }

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = '[{0}] {1} {2}' -f (Get-Date -Format 'HH:mm:ss'), $Level, $Message
    switch ($Level) {
        'ERROR' { Write-Host $line -ForegroundColor Red }
        'WARN'  { Write-Host $line -ForegroundColor Yellow }
        'OK'    { Write-Host $line -ForegroundColor Green }
        default { Write-Host $line }
    }
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Fail {
    param([string]$Message)
    Write-Log "ERROR: $Message" 'ERROR'
    exit 1
}

function Invoke-Tool {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string[]]$ToolArgs,
        [Parameter(Mandatory = $true)][string]$Title
    )
    Write-Log "> $Title"
    Write-Log ("  cmd: {0} {1}" -f (Split-Path -Leaf $Exe), ($ToolArgs -join ' '))
    $output = & $Exe @ToolArgs 2>&1
    $code = $LASTEXITCODE
    foreach ($line in @($output)) {
        if ($null -ne $line) { Add-Content -LiteralPath $logPath -Value ("    " + [string]$line) -Encoding UTF8 }
    }
    if ($code -ne 0) { Fail "$Title — код выхода $code (подробности в build.log)" }
    Write-Log "  ok: $Title" 'OK'
}

function Ensure-WixToolset {
    $needed = @('heat.exe', 'candle.exe', 'light.exe')
    $missing = @($needed | Where-Object { -not (Test-Path (Join-Path $wixDir $_)) })
    if ($missing.Count -eq 0) { return }

    Write-Log ("WiX v3.11 не найден ({0}), скачиваю portable: {1}" -f ($missing -join ', '), $wixUrl) 'WARN'
    if (-not (Test-Path $wixDir)) { New-Item -ItemType Directory -Force -Path $wixDir | Out-Null }
    $zip = Join-Path ([System.IO.Path]::GetTempPath()) 'wix311-binaries.zip'
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $wixUrl -OutFile $zip -UseBasicParsing
        Expand-Archive -LiteralPath $zip -DestinationPath $wixDir -Force
    }
    catch {
        Fail "не удалось скачать/распаковать WiX: $($_.Exception.Message)"
    }
    foreach ($name in $needed) {
        if (-not (Test-Path (Join-Path $wixDir $name))) { Fail "WiX: после распаковки не найден $name" }
    }
    Write-Log "WiX v3.11 распакован в $wixDir" 'OK'
}

function Set-AsciiTemp {
    # light.exe кириллический TEMP не переваривает (зависает на упаковке CAB).
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Temp\cutter-wix-tmp'),
        $env:TEMP
    )
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        if ($candidate -match '[^\x20-\x7E]') { continue }
        try {
            if (-not (Test-Path $candidate)) { New-Item -ItemType Directory -Force -Path $candidate | Out-Null }
            $probe = Join-Path $candidate ('probe_' + [guid]::NewGuid().ToString('N') + '.tmp')
            Set-Content -LiteralPath $probe -Value 'ok' -Encoding ASCII
            Remove-Item -LiteralPath $probe -Force
            $env:TEMP = $candidate
            $env:TMP = $candidate
            Write-Log "TEMP/TMP для сборки: $candidate"
            return
        }
        catch { continue }
    }
    Fail 'не нашёл ASCII-путь для TEMP/TMP (light.exe зависает на не-ASCII). Задайте ASCII TEMP вручную.'
}

function Get-WixToolDir {
    # WiX 3.11: light.exe, запущенный из пути с не-ASCII символами (например,
    # "C:\Users\user\Documents\Новая папка\..."), не может создать временные
    # файлы ("Отказано в доступе") и MSI не собирается. Поэтому, если корень
    # репозитория содержит не-ASCII, запускаем тулчейн из ASCII-копии.
    param([string]$SourceDir, [string]$RepoRootPath)
    if ($RepoRootPath -match '[^\x20-\x7E]') {
        $stageRoot = Join-Path $env:LOCALAPPDATA 'Temp\cutter-wix-toolset'
        if ($stageRoot -match '[^\x20-\x7E]') {
            Fail "LOCALAPPDATA содержит не-ASCII символы, ASCII-копия тулчейна невозможна: $stageRoot"
        }
        $stageLight = Join-Path $stageRoot 'light.exe'
        $needCopy = $true
        if (Test-Path $stageLight) {
            $srcHash = (Get-FileHash -LiteralPath (Join-Path $SourceDir 'light.exe') -Algorithm SHA256).Hash
            $dstHash = (Get-FileHash -LiteralPath $stageLight -Algorithm SHA256).Hash
            if ($srcHash -eq $dstHash) { $needCopy = $false }
        }
        if ($needCopy) {
            Write-Log "путь репозитория не-ASCII — копирую тулчейн WiX в ASCII: $stageRoot"
            if (Test-Path $stageRoot) { Remove-Item -LiteralPath $stageRoot -Recurse -Force }
            New-Item -ItemType Directory -Force -Path $stageRoot | Out-Null
            Copy-Item -Path (Join-Path $SourceDir '*') -Destination $stageRoot -Recurse -Force
        }
        return $stageRoot
    }
    return $SourceDir
}

function Get-MsiSummary {    param([string]$Path)
    try {
        $installer = New-Object -ComObject WindowsInstaller.Installer
        $summary = $installer.SummaryInformation($Path, 0)
        $subject = $summary.Property(3)
        $rev = $summary.Property(9)
        Write-Log ("MSI summary: subject='{0}', package code={1}" -f $subject, $rev)
    }
    catch {
        Write-Log "MSI summary недоступен (не критично): $($_.Exception.Message)" 'WARN'
    }
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()
Write-Log "=== Cutter MSI build $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Write-Log "version=$Version  repo=$repoRoot"

Ensure-WixToolset
Set-AsciiTemp
$toolDir = Get-WixToolDir -SourceDir $wixDir -RepoRootPath $repoRoot
Write-Log "тулчейн: $toolDir"

# --- icon ---
$iconPath = Join-Path $scriptDir 'icon.ico'
if ((-not (Test-Path $iconPath)) -and (-not $SkipIcon)) {
    Write-Log 'icon.ico нет — генерирую через make-icon.ps1'
    & (Join-Path $scriptDir 'make-icon.ps1') -OutFile $iconPath
    if ($LASTEXITCODE -ne 0 -and -not (Test-Path $iconPath)) { Fail 'не удалось сгенерировать icon.ico (make-icon.ps1)' }
}
if (-not (Test-Path $iconPath)) { Fail "нет файла $iconPath (запустите make-icon.ps1 или положите .ico вручную)" }

# --- dist ---
if (-not (Test-Path $DistDir)) { Fail "нет папки dist: $DistDir — сначала соберите приложение (PyInstaller)" }
$appExe = Join-Path $DistDir 'Cutter.exe'
if (-not (Test-Path $appExe)) { Fail "в $DistDir нет Cutter.exe — это не one-folder сборка" }
$distFiles = @(Get-ChildItem -LiteralPath $DistDir -Recurse -File -Force)
$distSizeMb = [math]::Round((($distFiles | Measure-Object Length -Sum).Sum) / 1MB, 1)
Write-Log ("dist: {0} файлов, {1} МБ ({2})" -f $distFiles.Count, $distSizeMb, $DistDir)
if ($distFiles.Count -lt 10) { Write-Log 'ВНИМАНИЕ: в dist мало файлов — похоже на dummy-сборку' 'WARN' }

# --- obj ---
if (Test-Path $objDir) { Remove-Item -LiteralPath $objDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $objDir | Out-Null

Push-Location $scriptDir
try {
    $filesWxs   = Join-Path $objDir 'Files.wxs'
    $filesObj   = Join-Path $objDir 'Files.wixobj'
    $productWxs = Join-Path $scriptDir 'Cutter.wxs'
    $productObj = Join-Path $objDir 'Cutter.wixobj'

    # 1) heat: список файлов dist\Cutter -> ComponentGroup ProductComponents
    Invoke-Tool -Exe (Join-Path $toolDir 'heat.exe') -Title 'heat: harvest dist\Cutter' -ToolArgs @(
        'dir', $DistDir,
        '-nologo',
        '-cg', 'ProductComponents',
        '-gg', '-g1',
        '-sreg', '-scom',
        '-srd',
        '-dr', 'INSTALLFOLDER',
        '-var', 'var.SourceDir',
        '-out', $filesWxs
    )

    # 2) candle: файловый список
    Invoke-Tool -Exe (Join-Path $toolDir 'candle.exe') -Title 'candle: Files.wxs' -ToolArgs @(
        '-nologo', '-arch', 'x64',
        ("-dSourceDir={0}" -f $DistDir),
        '-out', $filesObj,
        $filesWxs
    )

    # 3) candle: продукт
    Invoke-Tool -Exe (Join-Path $toolDir 'candle.exe') -Title 'candle: Cutter.wxs' -ToolArgs @(
        '-nologo', '-arch', 'x64',
        ("-dVersion={0}" -f $Version),
        ("-dSourceDir={0}" -f $DistDir),
        '-out', $productObj,
        $productWxs
    )

    # 4) light: линковка MSI (UI-расширение — для WixUI_Minimal).
    #    -loc/-cultures: русская кодовая страница 1251 (иначе кириллица в
    #    Product/ARP-строках падает на LGHT0311, а 1252 не умеет кириллицу).
    #    ICE57 — предупреждение про HKMU-ключ в dual-purpose пакете (ложное).
    #    ICE61 — Maximum version == current при AllowSameVersionUpgrades.
    #    MSI собирается в ASCII-путь: light/WixCreateCab не умеет писать cab
    #    в путь с кириллицей (ERROR_INVALID_NAME); потом файл переносится.
    $uiExt = Join-Path $toolDir 'WixUIExtension.dll'
    if (-not (Test-Path $uiExt)) { Fail "нет $uiExt" }
    $locFile = Join-Path $scriptDir 'Cutter.ru-RU.wxl'
    if (-not (Test-Path $locFile)) { Fail "нет файла локализации $locFile" }
    $stageMsiDir = Join-Path $env:TEMP 'cutter-msi-out'
    if (Test-Path $stageMsiDir) { Remove-Item -LiteralPath $stageMsiDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $stageMsiDir | Out-Null
    $stageMsi = Join-Path $stageMsiDir ("Cutter-{0}-x64.msi" -f $Version)
    Invoke-Tool -Exe (Join-Path $toolDir 'light.exe') -Title 'light: link MSI' -ToolArgs @(
        '-nologo',
        '-ext', $uiExt,
        '-loc', $locFile,
        '-cultures:ru-RU',
        '-sice:ICE57',
        '-sice:ICE61',
        '-out', $stageMsi,
        $productObj,
        $filesObj
    )
}
finally {
    Pop-Location
}

# Перенос собранного MSI в dist (кириллический путь репозитория)
if (-not (Test-Path $stageMsi)) { Fail "MSI не создан: $stageMsi" }
if (Test-Path $outMsi) { Remove-Item -LiteralPath $outMsi -Force }
Move-Item -LiteralPath $stageMsi -Destination $outMsi -Force
Write-Log "MSI перенесён: $stageMsi -> $outMsi"

if (-not (Test-Path $outMsi)) { Fail "MSI не создан: $outMsi" }
$msiItem = Get-Item -LiteralPath $outMsi
$hash = (Get-FileHash -LiteralPath $outMsi -Algorithm SHA256).Hash
Write-Log ("MSI: {0}" -f $msiItem.FullName) 'OK'
Write-Log ("     размер: {0} МБ, изменение: {1}" -f [math]::Round($msiItem.Length / 1MB, 1), $msiItem.LastWriteTime)
Write-Log ("     SHA256: {0}" -f $hash)
Get-MsiSummary -Path $outMsi
$sw.Stop()
Write-Log ("=== готово за {0:ss\.ff} с ===" -f $sw.Elapsed) 'OK'
