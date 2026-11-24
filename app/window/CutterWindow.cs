using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace Cutter
{
    /// <summary>
    /// Нативное окно приложения на WebView2 (.NET Framework 4.8 + установленный рантайм WebView2).
    /// Умеет сам поднять бэкенд (Cutter backend) и дождаться его готовности, а потом открыть интерфейс.
    /// </summary>
    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            var options = Options.Parse(args);

            if (string.IsNullOrEmpty(options.Url))
            {
                Log("не передан --url", true);
                return;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            var backend = BackendHost.Start(options);

            Application.ThreadException += (s, e) => Log("поток: " + e.Exception.Message, true);
            AppDomain.CurrentDomain.UnhandledException += (s, e) => Log("домен: " + e.ExceptionObject, true);

            try
            {
                if (backend != null && !backend.WaitReady(options.ReadyTimeoutSec))
                {
                    MessageBox.Show(
                        "Бэкенд приложения не запустился за " + options.ReadyTimeoutSec + " секунд.\n\n" +
                        "Запусти приложение заново. Если ошибка повторяется — посмотри лог в папке data\\logs.",
                        "Cutter", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    backend.Kill();
                    return;
                }

                Application.Run(new MainForm(options, backend));
            }
            finally
            {
                if (backend != null)
                {
                    backend.Kill();
                }
            }
        }

        public static string WindowDataDir()
        {
            var overrideDir = Environment.GetEnvironmentVariable("CUTTER_DATA_DIR");
            var root = !string.IsNullOrWhiteSpace(overrideDir)
                ? overrideDir.Trim()
                : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Cutter");
            return Path.Combine(root, "window");
        }

        public static void Log(string message, bool error = false)
        {
            try
            {
                var dir = Path.Combine(WindowDataDir(), "logs");
                Directory.CreateDirectory(dir);
                File.AppendAllText(Path.Combine(dir, "window.log"),
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + (error ? "ERROR " : "") + message + Environment.NewLine,
                    Encoding.UTF8);
            }
            catch
            {
                // логи не должны ронять приложение
            }
        }
    }

    internal sealed class Options
    {
        public string Url = "";
        public string Backend = "";
        public int Width = 1480;
        public int Height = 940;
        public int ReadyTimeoutSec = 90;
        public bool DevTools = false;

        public static Options Parse(string[] args)
        {
            var options = new Options();
            for (var i = 0; i < args.Length; i++)
            {
                var arg = args[i];
                switch (arg)
                {
                    case "--url": options.Url = Next(args, ref i); break;
                    case "--backend": options.Backend = Next(args, ref i); break;
                    case "--width": options.Width = ParseInt(Next(args, ref i), options.Width); break;
                    case "--height": options.Height = ParseInt(Next(args, ref i), options.Height); break;
                    case "--timeout": options.ReadyTimeoutSec = ParseInt(Next(args, ref i), options.ReadyTimeoutSec); break;
                    case "--devtools": options.DevTools = true; break;
                }
            }

            // Упакованный вариант: рядом лежит cutter_backend.exe — поднимаем его сами.
            if (string.IsNullOrEmpty(options.Backend))
            {
                var embedded = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "cutter_backend.exe");
                if (File.Exists(embedded))
                {
                    options.Backend = "cutter_backend.exe";
                    if (string.IsNullOrEmpty(options.Url))
                    {
                        options.Url = "http://127.0.0.1:8765";
                    }
                }
            }
            return options;
        }

        private static string Next(string[] args, ref int i)
        {
            return i + 1 < args.Length ? args[++i] : "";
        }

        private static int ParseInt(string value, int fallback)
        {
            int parsed;
            return int.TryParse(value, out parsed) ? parsed : fallback;
        }
    }

    /// <summary>Бэкенд: запуск Cutter backend во вложенном процессе и проверка /api/health.</summary>
    internal sealed class BackendHost
    {
        private Process _process;
        private string _healthUrl;

        public static BackendHost Start(Options options)
        {
            if (string.IsNullOrEmpty(options.Backend))
            {
                return null;
            }

            var exe = Path.IsPathRooted(options.Backend)
                ? options.Backend
                : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, options.Backend);

            if (!File.Exists(exe))
            {
                Program.Log("не найден бэкенд: " + exe, true);
                return null;
            }

            var host = new BackendHost { _healthUrl = options.Url.TrimEnd('/') + "/api/health" };
            var info = new ProcessStartInfo
            {
                FileName = exe,
                Arguments = "--no-window --port " + PortOf(options.Url),
                WorkingDirectory = Path.GetDirectoryName(exe),
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };

            try
            {
                host._process = Process.Start(info);
                Program.Log("бэкенд запущен pid=" + host._process.Id + " " + info.FileName);
                host._process.OutputDataReceived += (s, e) => { };
                host._process.ErrorDataReceived += (s, e) => { if (!string.IsNullOrEmpty(e.Data)) Program.Log("backend: " + e.Data); };
                host._process.BeginOutputReadLine();
                host._process.BeginErrorReadLine();
            }
            catch (Exception exc)
            {
                Program.Log("не смог запустить бэкенд: " + exc.Message, true);
                return null;
            }
            return host;
        }

        private static string PortOf(string url)
        {
            try { return new Uri(url).Port.ToString(); }
            catch { return "8765"; }
        }

        public bool WaitReady(int timeoutSeconds)
        {
            using (var client = new WebClient())
            {
                var deadline = DateTime.UtcNow.AddSeconds(timeoutSeconds);
                while (DateTime.UtcNow < deadline)
                {
                    if (_process != null && _process.HasExited)
                    {
                        Program.Log("бэкенд завершился с кодом " + _process.ExitCode, true);
                        return false;
                    }
                    try
                    {
                        var body = client.DownloadString(_healthUrl);
                        if (body != null && body.IndexOf("\"ok\":true", StringComparison.Ordinal) >= 0)
                        {
                            return true;
                        }
                    }
                    catch
                    {
                        // ещё не поднялся
                    }
                    Thread.Sleep(350);
                }
            }
            return false;
        }

        public void Kill()
        {
            if (_process == null)
            {
                return;
            }
            try
            {
                if (!_process.HasExited)
                {
                    _process.Kill();
                    _process.WaitForExit(5000);
                }
            }
            catch
            {
                // уже мёртв
            }
            finally
            {
                _process.Dispose();
                _process = null;
            }
        }

        public void Cleanup()
        {
        }
    }

    internal sealed class MainForm : Form
    {
        private const int DwmwaUseImmersiveDarkMode = 20;

        [DllImport("dwmapi.dll")]
        private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int attrValue, int attrSize);

        private readonly WebView2 _web;
        private readonly BackendHost _backend;
        private bool _shown;

        public MainForm(Options options, BackendHost backend)
        {
            _backend = backend;
            Text = "Cutter — нарезка в шортсы";
            Width = options.Width;
            Height = options.Height;
            MinimumSize = new Size(1120, 720);
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(10, 10, 14);
            KeyPreview = true;
            LoadWindowBounds();

            var iconPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "icon.ico");
            if (File.Exists(iconPath))
            {
                try { Icon = new Icon(iconPath); } catch { }
            }

            _web = new WebView2
            {
                Dock = DockStyle.Fill,
                DefaultBackgroundColor = Color.FromArgb(10, 10, 14),
            };
            Controls.Add(_web);

            Load += async (s, e) =>
            {
                ApplyDarkTitleBar();
                try
                {
                    var dataDir = Path.Combine(Program.WindowDataDir(), "webview2");
                    Directory.CreateDirectory(dataDir);
                    var env = await CoreWebView2Environment.CreateAsync(null, dataDir, null);
                    await _web.EnsureCoreWebView2Async(env);

                    _web.CoreWebView2.Settings.AreDefaultContextMenusEnabled = options.DevTools;
                    _web.CoreWebView2.Settings.IsStatusBarEnabled = false;
                    _web.CoreWebView2.Settings.AreDevToolsEnabled = options.DevTools;
                    _web.CoreWebView2.Settings.IsZoomControlEnabled = true;
                    _web.CoreWebView2.NewWindowRequested += (sender, args) =>
                    {
                        args.Handled = true;
                        try { Process.Start(new ProcessStartInfo(args.Uri) { UseShellExecute = true }); } catch { }
                    };
                    _web.CoreWebView2.NavigationCompleted += (sender, args) =>
                    {
                        if (!args.IsSuccess)
                        {
                            Program.Log("навигация не удалась: " + args.WebErrorStatus, true);
                        }
                    };

                    _web.CoreWebView2.Navigate(options.Url);
                    _shown = true;
                }
                catch (Exception exc)
                {
                    Program.Log("WebView2 не поднялся: " + exc.Message, true);
                    MessageBox.Show(
                        "Не получилось открыть окно приложения: нужен компонент WebView2 Runtime.\n\n" +
                        "Скачай «Microsoft Edge WebView2 Runtime» с сайта Microsoft и запусти Cutter заново.\n\n" +
                        "Подробности: " + exc.Message,
                        "Cutter", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    Close();
                }
            };

            FormClosing += (s, e) =>
            {
                SaveBounds();
            };

            KeyDown += (s, e) =>
            {
                if (e.KeyCode == Keys.F5)
                {
                    try { _web.CoreWebView2.Reload(); } catch { }
                }
                else if (e.KeyCode == Keys.F12 && options.DevTools)
                {
                    try { _web.CoreWebView2.OpenDevToolsWindow(); } catch { }
                }
            };
        }

        private static string BoundsFile
        {
            get { return Path.Combine(Program.WindowDataDir(), "window.bounds"); }
        }

        /// <summary>Помнит размер и позицию окна между запусками.</summary>
        private void LoadWindowBounds()
        {
            try
            {
                if (!File.Exists(BoundsFile)) return;
                var parts = File.ReadAllText(BoundsFile).Split(',');
                if (parts.Length != 4) return;
                int width, height, x, y;
                if (!int.TryParse(parts[0], out width) || !int.TryParse(parts[1], out height) ||
                    !int.TryParse(parts[2], out x) || !int.TryParse(parts[3], out y)) return;
                if (width < MinimumSize.Width || height < MinimumSize.Height) return;

                var area = Screen.FromPoint(new Point(x, y)).WorkingArea;
                width = Math.Min(width, area.Width);
                height = Math.Min(height, area.Height);
                x = Math.Max(area.Left, Math.Min(x, area.Right - width));
                y = Math.Max(area.Top, Math.Min(y, area.Bottom - height));

                StartPosition = FormStartPosition.Manual;
                Bounds = new Rectangle(x, y, width, height);
            }
            catch
            {
                // файл битый — окно откроется по центру
            }
        }

        private void SaveBounds()
        {
            try
            {
                var bounds = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
                Directory.CreateDirectory(Path.GetDirectoryName(BoundsFile));
                File.WriteAllText(BoundsFile, bounds.Width + "," + bounds.Height + "," + bounds.X + "," + bounds.Y);
            }
            catch
            {
                // нечего сохранять
            }
        }

        private void ApplyDarkTitleBar()
        {
            try
            {
                var value = 1;
                DwmSetWindowAttribute(Handle, DwmwaUseImmersiveDarkMode, ref value, sizeof(int));
            }
            catch
            {
                // на старых сборках Windows атрибута нет — просто светлый заголовок
            }
        }
    }
}
