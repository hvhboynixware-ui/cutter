<#
.SYNOPSIS
    Реальная проверка MSI Cutter: установка без прав администратора, ярлыки,
    удаление, повторная установка поверх.

.DESCRIPTION
    Запуск: powershell -ExecutionPolicy Bypass -File installer\test-install.ps1

    Сценарии:
      1. per-user установка (MSIINSTALLPERUSER=1, msiexec /qn) — файлы, ярлыки,
         запись в "Программы и компоненты", HKCU\Software\Cutter;
      2. повторная установка поверх (тот же MSI) — не ломается;
      3. удаление (msiexec /x /qn) — файлы/ярлыки/записи исчезают;
      4. установка БЕЗ параметров (авто-fallback на per-user) — проверка, что
         пакет сам выбирает per-user без администратора;
      5. (опционально, -UpgradeMsi) major upgrade: ставим base, затем upgrade.

    Логи: installer\test-install.log (наш), installer\test-install-msiexec.log
    (подробные логи Windows Installer).

    Скрипт НЕ требует администратора и ставит всё в профиль текущего пользователя.
#>
[CmdletBinding()]
param(
    [string]$Msi = '',
    [string]$UpgradeMsi = '',
    [string]$DistDir = ''
)

$ErrorActionPreference = 'Stop'
$scriptDir = $PSScriptRoot
$repoRoot  = Split-Path -Parent $scriptDir
if ([string]::IsNullOrWhiteSpace($DistDir)) { $DistDir = Join-Path $repoRoot 'dist\Cutter' }
if ([string]::IsNullOrWhiteSpace($Msi)) {
    $found = @(Get-ChildItem -Path (Join-Path $repoRoot 'dist') -Filter 'Cutter-*-x64.msi' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    if ($found.Count -eq 0) { throw 'не найден MSI в dist\ — сначала запустите installer\build.ps1' }
    $Msi = $found[0].FullName
}
if ([string]::IsNullOrWhiteSpace($UpgradeMsi) -eq $false -and -not (Test-Path -LiteralPath $UpgradeMsi)) {
    throw "нет upgrade-MSI: $UpgradeMsi"
}

$logPath    = Join-Path $scriptDir 'test-install.log'
$msiLogPath = Join-Path $scriptDir 'test-install-msiexec.log'
if (Test-Path $logPath) { Remove-Item -LiteralPath $logPath -Force }
if (Test-Path $msiLogPath) { Remove-Item -LiteralPath $msiLogPath -Force }

$results = New-Object System.Collections.Generic.List[string]
$failCount = 0

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = '[{0}] {1} {2}' -f (Get-Date -Format 'HH:mm:ss'), $Level, $Message
    switch ($Level) {
        'FAIL' { Write-Host $line -ForegroundColor Red }
        'OK'   { Write-Host $line -ForegroundColor Green }
        'WARN' { Write-Host $line -ForegroundColor Yellow }
        default { Write-Host $line }
    }
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Check {
    param([string]$Name, $Condition, [string]$Details = '')
    # COM-вызовы иногда кладут $null в pipeline — нормализуем к $true/$false
    $ok = (@($Condition) -contains $true)
    if ($ok) {
        $results.Add("PASS: $Name")
        Write-Log ("PASS: {0} {1}" -f $Name, $Details) 'OK'
    }
    else {
        $script:failCount++
        $results.Add("FAIL: $Name $Details")
        Write-Log ("FAIL: {0} {1}" -f $Name, $Details) 'FAIL'
    }
}

function Invoke-MsiExec {
    param([string]$Arguments, [string]$Title)
    Write-Log "msiexec $Arguments"
    $p = Start-Process -FilePath 'msiexec.exe' -ArgumentList $Arguments -Wait -PassThru
    $code = $p.ExitCode
    $okCodes = @(0, 3010, 1641)
    if ($okCodes -contains $code) {
        Write-Log ("{0}: exit={1}" -f $Title, $code) 'OK'
    }
    else {
        Write-Log ("{0}: exit={1} (ожидался 0/3010)" -f $Title, $code) 'FAIL'
    }
    return $code
}

function Get-MsiProductProperty {
    param([string]$Path, [string]$PropertyName)
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $db = $installer.OpenDatabase($Path, 0)
    $view = $db.OpenView("SELECT Value FROM Property WHERE Property='$PropertyName'")
    $null = $view.Execute()
    $record = $view.Fetch()
    if ($null -eq $record) { return $null }
    return $record.StringData(1)
}

function Get-ArpEntry {
    param([string]$ProductCode)
    $candidates = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$ProductCode",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$ProductCode",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\$ProductCode"
    )
    foreach ($key in $candidates) {
        if (Test-Path $key) {
            $props = Get-ItemProperty -Path $key
            return [pscustomobject]@{
                Key             = $key
                DisplayName     = $props.DisplayName
                DisplayVersion  = $props.DisplayVersion
                InstallLocation = $props.InstallLocation
                UninstallString = $props.UninstallString
            }
        }
    }
    return $null
}

function Get-ProductCodesFromArp {
    $codes = @()
    foreach ($root in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall')) {
        if (Test-Path $root) {
            Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {
                $props = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
                if ($props.DisplayName -eq 'Cutter') { $codes += $_.PSChildName }
            }
        }
    }
    return $codes
}

function Get-ShortcutPath {
    param([string]$Kind)
    switch ($Kind) {
        'StartMenu' { return Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Cutter\Cutter.lnk' }
        'Desktop'   { return Join-Path ([Environment]::GetFolderPath('Desktop')) 'Cutter.lnk' }
    }
}

function Test-Shortcut {
    param([string]$Path, [string]$ExpectedTarget)
    if (-not (Test-Path -LiteralPath $Path)) { return [pscustomobject]@{ Exists = $false; Target = '' } }
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($Path)
    return [pscustomobject]@{ Exists = $true; Target = $lnk.TargetPath }
}

function Uninstall-IfPresent {
    param([string]$MsiPath)
    $codes = @(Get-ProductCodesFromArp)
    if ($codes.Count -eq 0) { return }
    Write-Log "перед тестом удаляю уже установленный Cutter: $($codes -join ', ')" 'WARN'
    $code = Invoke-MsiExec -Title 'cleanup uninstall' -Arguments ("/x `"{0}`" /qn /norestart REBOOT=ReallySuppress /L*v `"{1}`"" -f $MsiPath, $msiLogPath)
    Start-Sleep -Seconds 1
}

function Wait-Installed {
    param([string]$ProductCode, [int]$Seconds = 30)
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (Test-Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$ProductCode") { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Assert-InstalledState {
    param(
        [string]$Tag,
        [string]$ProductCode,
        [string]$ExpectedDir,
        [string]$ExpectedVersion
    )
    $arp = Get-ArpEntry -ProductCode $ProductCode
    Check "$Tag / запись в Программах и компонентах" ($null -ne $arp) ("key=" + $(if ($arp) { $arp.Key } else { 'нет' }))
    if ($null -ne $arp) {
        Check "$Tag / DisplayName = Cutter" ($arp.DisplayName -eq 'Cutter') ("DisplayName=" + $arp.DisplayName)
        Check "$Tag / DisplayVersion = $ExpectedVersion" ($arp.DisplayVersion -eq $ExpectedVersion) ("DisplayVersion=" + $arp.DisplayVersion)
        if ($arp.InstallLocation) {
            Check "$Tag / InstallLocation = $ExpectedDir" ($arp.InstallLocation.TrimEnd('\') -ieq $ExpectedDir.TrimEnd('\')) ("InstallLocation=" + $arp.InstallLocation)
        }
        Check "$Tag / UninstallString есть" (-not [string]::IsNullOrWhiteSpace($arp.UninstallString))
    }

    $appExe = Join-Path $ExpectedDir 'Cutter.exe'
    Check "$Tag / файлы установлены ($ExpectedDir)" (Test-Path -LiteralPath $appExe) ("Cutter.exe=" + (Test-Path -LiteralPath $appExe))
    $installedFiles = @()
    if (Test-Path -LiteralPath $ExpectedDir) { $installedFiles = @(Get-ChildItem -LiteralPath $ExpectedDir -Recurse -File -Force) }
    $distFiles = @()
    if (Test-Path -LiteralPath $DistDir) { $distFiles = @(Get-ChildItem -LiteralPath $DistDir -Recurse -File -Force) }
    Check "$Tag / количество файлов совпадает с dist" ($installedFiles.Count -eq $distFiles.Count) ("installed=" + $installedFiles.Count + " dist=" + $distFiles.Count)

    $startShortcut = Test-Shortcut -Path (Get-ShortcutPath 'StartMenu')
    Check "$Tag / ярлык в меню Пуск" ($startShortcut.Exists) ((Get-ShortcutPath 'StartMenu'))
    if ($startShortcut.Exists) {
        Check "$Tag / ярлык Пуск указывает на Cutter.exe" ($startShortcut.Target -ieq $appExe) ("target=" + $startShortcut.Target)
    }
    $deskShortcut = Test-Shortcut -Path (Get-ShortcutPath 'Desktop')
    Check "$Tag / ярлык на рабочем столе" ($deskShortcut.Exists) (Get-ShortcutPath 'Desktop')
    if ($deskShortcut.Exists) {
        Check "$Tag / ярлык рабочего стола указывает на Cutter.exe" ($deskShortcut.Target -ieq $appExe) ("target=" + $deskShortcut.Target)
    }

    $regPath = 'HKCU:\Software\Cutter'
    $regOk = $false
    $regInstallPath = ''
    if (Test-Path $regPath) {
        $props = Get-ItemProperty -Path $regPath -ErrorAction SilentlyContinue
        $regInstallPath = $props.InstallPath
        $regOk = ($regInstallPath -ieq $ExpectedDir)
    }
    Check "$Tag / HKCU\Software\Cutter\InstallPath" $regOk ("InstallPath=" + $regInstallPath)
}

function Assert-UninstalledState {
    param([string]$Tag, [string]$ProductCode, [string]$InstallDir)
    $arp = Get-ArpEntry -ProductCode $ProductCode
    Check "$Tag / запись в Программах и компонентах удалена" ($null -eq $arp)
    Check "$Tag / папка установки удалена" (-not (Test-Path -LiteralPath (Join-Path $InstallDir 'Cutter.exe')))
    if (Test-Path -LiteralPath $InstallDir) {
        $left = @(Get-ChildItem -LiteralPath $InstallDir -Recurse -File -Force)
        Check "$Tag / в папке установки не осталось файлов" ($left.Count -eq 0) ("осталось=" + $left.Count)
    }
    else {
        Check "$Tag / папка установки удалена" $true
    }
    Check "$Tag / ярлык Пуск удалён" (-not (Test-Path -LiteralPath (Get-ShortcutPath 'StartMenu')))
    Check "$Tag / ярлык рабочего стола удалён" (-not (Test-Path -LiteralPath (Get-ShortcutPath 'Desktop')))
    Check "$Tag / HKCU\Software\Cutter удалён" (-not (Test-Path 'HKCU:\Software\Cutter'))
}

# ---------------------------------------------------------------------------
Write-Log "=== Cutter MSI test $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Write-Log "MSI: $Msi"
if (-not (Test-Path -LiteralPath $Msi)) { throw "нет MSI: $Msi" }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Log "тест идёт без прав администратора: $(-not $isAdmin)"

$productCode = Get-MsiProductProperty -Path $Msi -PropertyName 'ProductCode'
$productVersion = Get-MsiProductProperty -Path $Msi -PropertyName 'ProductVersion'
Write-Log "ProductCode=$productCode ProductVersion=$productVersion"
Check 'ProductCode сгенерирован (не * и не пустой)' (($productCode -match '^\{[0-9A-Fa-f-]{36}\}$'))
Check 'ProductVersion непустой' (-not [string]::IsNullOrWhiteSpace($productVersion))

Uninstall-IfPresent -MsiPath $Msi

# Ожидаемая папка per-user установки
$perUserDir = Join-Path $env:LOCALAPPDATA 'Programs\Cutter'

# --- 1. per-user install (явно MSIINSTALLPERUSER=1) ---
Write-Log '--- сценарий 1: установка per-user (MSIINSTALLPERUSER=1, /qn) ---'
$code = Invoke-MsiExec -Title 'install per-user' -Arguments ("/i `"{0}`" /qn /norestart MSIINSTALLPERUSER=1 REBOOT=ReallySuppress /L*v `"{1}`"" -f $Msi, $msiLogPath)
Check 'install per-user: exit code' ($code -eq 0 -or $code -eq 3010 -or $code -eq 1641) ("exit=" + $code)
Wait-Installed -ProductCode $productCode -Seconds 20 | Out-Null
Assert-InstalledState -Tag 'install' -ProductCode $productCode -ExpectedDir $perUserDir -ExpectedVersion $productVersion
$installedDir = $perUserDir

# --- 2. повторная установка поверх (тот же MSI) ---
Write-Log '--- сценарий 2: повторная установка поверх ---'
$code = Invoke-MsiExec -Title 'reinstall over' -Arguments ("/i `"{0}`" /qn /norestart MSIINSTALLPERUSER=1 REBOOT=ReallySuppress /L*v `"{1}`"" -f $Msi, $msiLogPath)
Check 'повторная установка: exit code' ($code -eq 0 -or $code -eq 3010 -or $code -eq 1641) ("exit=" + $code)
Assert-InstalledState -Tag 'reinstall' -ProductCode $productCode -ExpectedDir $installedDir -ExpectedVersion $productVersion
$arpCount = @(Get-ProductCodesFromArp).Count
Check 'повторная установка: в ARP ровно одна запись Cutter' ($arpCount -eq 1) ("записей=" + $arpCount)

# --- 3. удаление ---
Write-Log '--- сценарий 3: удаление (msiexec /x /qn) ---'
$code = Invoke-MsiExec -Title 'uninstall' -Arguments ("/x `"{0}`" /qn /norestart REBOOT=ReallySuppress /L*v `"{1}`"" -f $Msi, $msiLogPath)
Check 'uninstall: exit code' ($code -eq 0 -or $code -eq 3010 -or $code -eq 1641) ("exit=" + $code)
Start-Sleep -Seconds 1
Assert-UninstalledState -Tag 'uninstall' -ProductCode $productCode -InstallDir $installedDir
$arpCount = @(Get-ProductCodesFromArp).Count
Check 'uninstall: в ARP нет записи Cutter' ($arpCount -eq 0) ("записей=" + $arpCount)

# --- 4. установка БЕЗ параметров: авто-fallback на per-user без админа ---
Write-Log '--- сценарий 4: установка без параметров (авто per-user fallback) ---'
$code = Invoke-MsiExec -Title 'install auto scope' -Arguments ("/i `"{0}`" /qn /norestart REBOOT=ReallySuppress /L*v `"{1}`"" -f $Msi, $msiLogPath)
Check 'install auto: exit code' ($code -eq 0 -or $code -eq 3010 -or $code -eq 1641) ("exit=" + $code)
if ($code -eq 0 -or $code -eq 3010 -or $code -eq 1641) {
    $arp = Get-ArpEntry -ProductCode $productCode
    Check 'install auto: продукт установлен' ($null -ne $arp) ("key=" + $(if ($arp) { $arp.Key } else { 'нет' }))
    if ($null -ne $arp) {
        $autoDir = $arp.InstallLocation
        if ([string]::IsNullOrWhiteSpace($autoDir)) { $autoDir = $perUserDir }
        Check 'install auto: путь per-user (без прав админа)' ($autoDir.TrimEnd('\') -ieq $perUserDir.TrimEnd('\')) ("InstallLocation=" + $autoDir)
        Check 'install auto: файлы на месте' (Test-Path -LiteralPath (Join-Path $autoDir 'Cutter.exe'))
        Check 'install auto: ярлык Пуск создан' (Test-Path -LiteralPath (Get-ShortcutPath 'StartMenu'))
    }
    $code = Invoke-MsiExec -Title 'uninstall after auto' -Arguments ("/x `"{0}`" /qn /norestart REBOOT=ReallySuppress /L*v `"{1}`"" -f $Msi, $msiLogPath)
    Check 'uninstall after auto: exit code' ($code -eq 0 -or $code -eq 3010 -or $code -eq 1641) ("exit=" + $code)
    Assert-UninstalledState -Tag 'after-auto' -ProductCode $productCode -InstallDir $perUserDir
}

# --- 5. major upgrade (если дан второй MSI) ---
if (-not [string]::IsNullOrWhiteSpace($UpgradeMsi)) {
    Write-Log '--- сценарий 5: major upgrade ---'
    $upgradeCode = Get-MsiProductProperty -Path $UpgradeMsi -PropertyName 'ProductCode'
    $upgradeVersion = Get-MsiProductProperty -Path $UpgradeMsi -PropertyName 'ProductVersion'
    Write-Log "upgrade: $UpgradeMsi (ProductCode=$upgradeCode, Version=$upgradeVersion)"
    $code = Invoke-MsiExec -Title 'install base for upgrade' -Arguments ("/i `"{0}`" /qn /norestart MSIINSTALLPERUSER=1 REBOOT=ReallySuppress /L*v `"{1}`"" -f $Msi, $msiLogPath)
    Check 'upgrade: базовая версия установлена' ($code -eq 0 -or $code -eq 3010 -or $code -eq 1641) ("exit=" + $code)
    Check 'upgrade: базовая версия на месте' (Test-Path -LiteralPath (Join-Path $perUserDir 'Cutter.exe'))
    $code = Invoke-MsiExec -Title 'major upgrade install' -Arguments ("/i `"{0}`" /qn /norestart MSIINSTALLPERUSER=1 REBOOT=ReallySuppress /L*v `"{1}`"" -f $UpgradeMsi, $msiLogPath)
    Check 'upgrade: установка новой версии поверх' ($code -eq 0 -or $code -eq 3010 -or $code -eq 1641) ("exit=" + $code)
    Start-Sleep -Seconds 1
    $arpNew = Get-ArpEntry -ProductCode $upgradeCode
    Check 'upgrade: новая версия в ARP' ($null -ne $arpNew) ("key=" + $(if ($arpNew) { $arpNew.Key } else { 'нет' }))
    if ($null -ne $arpNew) {
        Check 'upgrade: DisplayVersion = новая' ($arpNew.DisplayVersion -eq $upgradeVersion) ("DisplayVersion=" + $arpNew.DisplayVersion)
    }
    $arpOld = Get-ArpEntry -ProductCode $productCode
    Check 'upgrade: старая версия снята (ARP)' ($null -eq $arpOld)
    Check 'upgrade: файлы новой версии на месте' (Test-Path -LiteralPath (Join-Path $perUserDir 'Cutter.exe'))
    $arpCount = @(Get-ProductCodesFromArp).Count
    Check 'upgrade: в ARP ровно одна запись Cutter' ($arpCount -eq 1) ("записей=" + $arpCount)
    $code = Invoke-MsiExec -Title 'uninstall after upgrade' -Arguments ("/x `"{0}`" /qn /norestart REBOOT=ReallySuppress /L*v `"{1}`"" -f $UpgradeMsi, $msiLogPath)
    Check 'upgrade: удаление после теста' ($code -eq 0 -or $code -eq 3010 -or $code -eq 1641) ("exit=" + $code)
    Assert-UninstalledState -Tag 'after-upgrade' -ProductCode $upgradeCode -InstallDir $perUserDir
}

# ---------------------------------------------------------------------------
Write-Log '=== ИТОГ ==='
foreach ($line in $results) { Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8 }
Write-Log ("PASS={0} FAIL={1}" -f (@($results | Where-Object { $_ -like 'PASS:*' }).Count), $failCount)
if ($failCount -gt 0) {
    Write-Log "ТЕСТ ПРОВАЛЕН: $failCount проверок упало" 'FAIL'
    exit 1
}
Write-Log 'ТЕСТ ПРОЙДЕН' 'OK'
exit 0
