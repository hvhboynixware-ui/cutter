# Проверка собранного приложения dist\Cutter\Cutter.exe: окно + встроенный бэкенд.
# ASCII only.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "dist\Cutter\Cutter.exe"
$shots = Join-Path $root "docs\shots"
New-Item -ItemType Directory -Force -Path $shots | Out-Null

if (-not (Test-Path $exe)) { throw "not built: $exe" }

$env:CUTTER_DATA_DIR = Join-Path $root '.userdata'
$app = Start-Process -FilePath $exe -PassThru
Write-Host "[test] app pid: $($app.Id)"
Start-Sleep -Seconds 20

$backend = Get-Process cutter_backend -ErrorAction SilentlyContinue
Write-Host "[test] backend running: $([bool]$backend)"

try {
    $health = Invoke-WebRequest "http://127.0.0.1:8765/api/health" -UseBasicParsing -TimeoutSec 5
    Write-Host "[test] health: $($health.StatusCode) $($health.Content)"
} catch {
    Write-Host "[test] health FAILED: $($_.Exception.Message)"
}

$code = @'
using System;
using System.Text;
using System.Drawing;
using System.Runtime.InteropServices;
public class Cap3 {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
  public struct RECT { public int Left, Top, Right, Bottom; }
  public static string[] Find(string prefix) {
    var list = new System.Collections.Generic.List<string>();
    EnumWindows((h, l) => {
      if (!IsWindowVisible(h)) return true;
      var t = new StringBuilder(512);
      GetWindowText(h, t, 512);
      if (t.Length > 0 && t.ToString().StartsWith(prefix)) {
        RECT r; GetWindowRect(h, out r);
        list.Add(h.ToInt64() + "|" + t + "|" + (r.Right-r.Left) + "x" + (r.Bottom-r.Top));
      }
      return true;
    }, IntPtr.Zero);
    return list.ToArray();
  }
  public static bool Snap(IntPtr h, string path) {
    RECT r; if (!GetWindowRect(h, out r)) return false;
    int w = r.Right - r.Left, hh = r.Bottom - r.Top;
    if (w <= 0 || hh <= 0) return false;
    using (var bmp = new Bitmap(w, hh))
    using (var g = Graphics.FromImage(bmp)) {
      IntPtr hdc = g.GetHdc();
      bool ok = PrintWindow(h, hdc, 2);
      g.ReleaseHdc(hdc);
      bmp.Save(path, System.Drawing.Imaging.ImageFormat.Png);
      return ok;
    }
  }
}
'@
Add-Type -TypeDefinition $code -Language CSharp -ReferencedAssemblies System.Drawing

$found = [Cap3]::Find("Cutter")
Write-Host "[test] windows: $($found.Count)"
$index = 0
foreach ($item in $found) {
    $index++
    $parts = $item -split '\|'
    $path = Join-Path $shots "app-window.png"
    $ok = [Cap3]::Snap([IntPtr][long]$parts[0], $path)
    Write-Host "[test] $($parts[1]) $($parts[2]) -> $path"
}

# закрываем окно и смотрим, умер ли бэкенд
$proc = Get-Process -Id $app.Id -ErrorAction SilentlyContinue
if ($proc) { $proc.CloseMainWindow() | Out-Null; Start-Sleep -Seconds 4 }
$still = Get-Process cutter_backend -ErrorAction SilentlyContinue
Write-Host "[test] backend alive after close: $([bool]$still)"
if (-not $app.HasExited) { Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue }
Get-Process cutter_backend -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
