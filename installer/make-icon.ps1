<#
.SYNOPSIS
    Генерирует installer\icon.ico для Cutter (иконка продукта / ярлыков).
.DESCRIPTION
    Нужна, когда нет готового .ico от PyInstaller. Рисует скруглённый квадрат
    с монограммой "C" и складывает многоразмерный PNG-ICO (16..256).
    Если файл icon.ico уже есть и не указан -Force, скрипт его не трогает.
#>
[CmdletBinding()]
param(
    [string]$OutFile = '',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($OutFile)) { $OutFile = Join-Path $PSScriptRoot 'icon.ico' }
if ((Test-Path $OutFile) -and -not $Force) {
    Write-Host "icon.ico уже есть, пропускаю: $OutFile"
    return
}

Add-Type -AssemblyName System.Drawing

$sizes = @(16, 24, 32, 48, 64, 128, 256)
$iconData = New-Object System.Collections.Generic.List[byte[]]

foreach ($s in $sizes) {
    $bmp = New-Object System.Drawing.Bitmap($s, $s, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $g.Clear([System.Drawing.Color]::Transparent)

    # Скруглённый квадрат
    $r = [float]([Math]::Max(2.0, $s * 0.23))
    $d = $r * 2.0
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $path.AddArc(0, 0, $d, $d, 180, 90)
    $path.AddArc($s - $d, 0, $d, $d, 270, 90)
    $path.AddArc($s - $d, $s - $d, $d, $d, 0, 90)
    $path.AddArc(0, $s - $d, $d, $d, 90, 90)
    $path.CloseFigure()

    $p1 = New-Object System.Drawing.Point(0, 0)
    $p2 = New-Object System.Drawing.Point($s, $s)
    $c1 = [System.Drawing.Color]::FromArgb(255, 27, 33, 44)    # графит
    $c2 = [System.Drawing.Color]::FromArgb(255, 12, 16, 22)    # почти чёрный
    $bgBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($p1, $p2, $c1, $c2)
    $g.FillPath($bgBrush, $path)

    # Акцентная нижняя подсветка (тонкая диагональная полоса)
    $band = $null
    if ($s -ge 32) {
        $band = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(230, 43, 209, 199))
        $g.SetClip($path)
        $h = [float]([Math]::Max(1.0, $s * 0.045))
        $g.FillRectangle($band, 0, [float]($s * 0.87), $s, $h)
        $g.ResetClip()
    }

    # Монограмма C
    $fontSize = [float]($s * 0.62)
    $font = New-Object System.Drawing.Font('Segoe UI', $fontSize, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $fmt = New-Object System.Drawing.StringFormat
    $fmt.Alignment = [System.Drawing.StringAlignment]::Center
    $fmt.LineAlignment = [System.Drawing.StringAlignment]::Center
    $rect = New-Object System.Drawing.RectangleF(0, [float](-$s * 0.03), $s, $s)
    # лёгкая тень
    $shadow = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(90, 0, 0, 0))
    $rectS = New-Object System.Drawing.RectangleF(0, [float]([Math]::Max(1.0, $s * 0.02) - $s * 0.03), $s, $s)
    $g.DrawString('C', $font, $shadow, $rectS, $fmt)
    # градиентная буква: белый -> светло-бирюзовый
    $fp1 = New-Object System.Drawing.Point(0, [int]($s * 0.1))
    $fp2 = New-Object System.Drawing.Point(0, [int]($s * 0.95))
    $fgBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($fp1, $fp2, [System.Drawing.Color]::White, [System.Drawing.Color]::FromArgb(255, 140, 245, 240))
    $g.DrawString('C', $font, $fgBrush, $rect, $fmt)

    # Забираем пиксели BGRA (классический BMP, а не PNG — совместимо со всем, включая WiX/msiexec)
    $rectLock = New-Object System.Drawing.Rectangle(0, 0, $s, $s)
    $locked = $bmp.LockBits($rectLock, [System.Drawing.Imaging.ImageLockMode]::ReadOnly, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $stride = $locked.Stride
    $pixels = New-Object byte[] ($stride * $s)
    [System.Runtime.InteropServices.Marshal]::Copy($locked.Scan0, $pixels, 0, $pixels.Length)
    $bmp.UnlockBits($locked)

    # DIB: BITMAPINFOHEADER + XOR (строки снизу вверх) + AND-маска
    $dib = New-Object System.IO.MemoryStream
    $dw = New-Object System.IO.BinaryWriter($dib)
    $dw.Write([UInt32]40)          # biSize
    $dw.Write([Int32]$s)           # biWidth
    $dw.Write([Int32]($s * 2))     # biHeight (XOR + AND)
    $dw.Write([UInt16]1)           # biPlanes
    $dw.Write([UInt16]32)          # biBitCount
    $dw.Write([UInt32]0)           # biCompression = BI_RGB
    $dw.Write([UInt32]($s * $s * 4))
    $dw.Write([Int32]0); $dw.Write([Int32]0)
    $dw.Write([UInt32]0); $dw.Write([UInt32]0)
    for ($y = $s - 1; $y -ge 0; $y--) { $dw.Write($pixels, ($y * $stride), ($s * 4)) }
    $maskRow = [int][Math]::Ceiling($s / 8.0)
    if (($maskRow % 4) -ne 0) { $maskRow += (4 - ($maskRow % 4)) }
    $mask = New-Object byte[] ($maskRow * $s)
    $dw.Write($mask, 0, $mask.Length)
    $dw.Flush()
    $iconData.Add($dib.ToArray())
    $dw.Dispose(); $dib.Dispose()

    $fgBrush.Dispose(); $shadow.Dispose(); $font.Dispose(); $bgBrush.Dispose()
    if ($null -ne $band) { $band.Dispose() }
    $g.Dispose(); $bmp.Dispose(); $path.Dispose()
}

# Сборка ICO: заголовок + записи каталога + DIB-данные
$ms = New-Object System.IO.MemoryStream
$bw = New-Object System.IO.BinaryWriter($ms)
$bw.Write([UInt16]0)                 # reserved
$bw.Write([UInt16]1)                 # type = icon
$bw.Write([UInt16]$sizes.Count)      # count

$offset = 6 + (16 * $sizes.Count)
for ($i = 0; $i -lt $sizes.Count; $i++) {
    $s = $sizes[$i]
    $bytes = $iconData[$i]
    $bw.Write([byte]($(if ($s -ge 256) { 0 } else { $s })))  # width
    $bw.Write([byte]($(if ($s -ge 256) { 0 } else { $s })))  # height
    $bw.Write([byte]0)               # palette colors
    $bw.Write([byte]0)               # reserved
    $bw.Write([UInt16]1)             # color planes
    $bw.Write([UInt16]32)            # bits per pixel
    $bw.Write([UInt32]$bytes.Length) # byte size
    $bw.Write([UInt32]$offset)       # offset
    $offset += $bytes.Length
}
foreach ($bytes in $iconData) { $bw.Write($bytes) }
$bw.Flush()
[System.IO.File]::WriteAllBytes($OutFile, $ms.ToArray())
$bw.Dispose(); $ms.Dispose()

# Проверка: .NET должен прочитать ico
$ico = New-Object System.Drawing.Icon($OutFile)
Write-Host "OK: $OutFile ($((Get-Item $OutFile).Length) bytes, base size $($ico.Width)x$($ico.Height))"
$ico.Dispose()
