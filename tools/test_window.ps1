# Проверка нативного окна: запускает сервер и Cutter.exe, снимает скрин окна через PrintWindow.
# ASCII only.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$exe = Join-Path $root "build\native\Cutter.exe"
$shots = Join-Path $root "docs\shots"
New-Item -ItemType Directory -Force -Path $shots | Out-Null

$env:PYTHONIOENCODING = "utf-8"
$env:CUTTER_FAKE_LLM = "1"
$server = Start-Process -FilePath $python -ArgumentList "-m", "app.main", "--no-window", "--port", "8770" -WorkingDirectory $root -PassThru -WindowStyle Minimized
Start-Sleep -Seconds 6

try {
    $health = Invoke-WebRequest "http://127.0.0.1:8770/api/health" -UseBasicParsing -TimeoutSec 5
    Write-Host "[test] health: $($health.StatusCode)"
} catch {
    Write-Host "[test] health FAILED: $($_.Exception.Message)"
}

$app = Start-Process -FilePath $exe -ArgumentList "--url", "http://127.0.0.1:8770", "--width", "1400", "--height", "900" -PassThru
Write-Host "[test] window pid: $($app.Id)"
Start-Sleep -Seconds 10

$code = @'
using System;
using System.Text;
using System.Drawing;
using System.Runtime.InteropServices;
public class Cap2 {
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

$found = [Cap2]::Find("Cutter")
Write-Host "[test] windows: $($found.Count)"
$index = 0
foreach ($item in $found) {
    $index++
    $parts = $item -split '\|'
    $path = Join-Path $shots "_native_$index.png"
    $ok = [Cap2]::Snap([IntPtr][long]$parts[0], $path)
    Write-Host "[test] $($parts[1]) $($parts[2]) -> $path (PrintWindow=$ok)"
}

if (-not $app.HasExited) { Stop-Process -Id $app.Id -Force }
Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
