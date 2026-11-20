# -*- coding: utf-8 -*-
"""Точка входа: локальный сервер + окно приложения.

Режимы окна (--window-mode):
  auto    — нативное окно Cutter.exe (WebView2); если его нет — pywebview, окно Edge, вкладка
  window  — только нативное окно Cutter.exe
  native  — только pywebview
  app     — окно Edge/Chrome без адресной строки
  browser — обычная вкладка в браузере по умолчанию
  none    — только сервер, без окна (этот режим использует Cutter.exe)
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from app.core import settings as S  # noqa: E402
from app.server import app  # noqa: E402

APP_TITLE = "Cutter — нарезка в шортсы"

BROWSERS = [
    (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "edge"),
    (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "edge"),
    (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "chrome"),
    (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "chrome"),
    (os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"), "chrome"),
]


def free_port(preferred: int = 8765) -> int:
    for port in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(url: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1.0) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def native_window_exe() -> Path | None:
    """Где лежит собранное нативное окно Cutter.exe."""
    candidates = []
    if S.FROZEN:
        candidates.append(Path(sys.executable).resolve().parent / "Cutter.exe")
    candidates.append(S.ROOT / "build" / "native" / "Cutter.exe")
    candidates.append(S.ROOT / "Cutter.exe")
    for path in candidates:
        if path.is_file():
            return path
    return None


def find_browser() -> tuple[str, str] | None:
    for path, kind in BROWSERS:
        if path and Path(path).is_file():
            return path, kind
    return None


def run_native_window(url: str, width: int, height: int) -> bool:
    exe = native_window_exe()
    if exe is None:
        return False
    print(f"окно: {exe.name}")
    try:
        process = subprocess.Popen([str(exe), "--url", url, "--width", str(width), "--height", str(height)])
    except Exception as exc:
        print(f"нативное окно не запустилось: {exc}", file=sys.stderr)
        return False
    process.wait()
    return True


def run_pywebview(url: str, width: int, height: int) -> bool:
    try:
        import webview
    except Exception as exc:
        print(f"pywebview недоступен ({exc})", file=sys.stderr)
        return False
    try:
        webview.create_window(APP_TITLE, url, width=width, height=height,
                              min_size=(1120, 720), background_color="#0b0b12")
        webview.start()
        return True
    except Exception as exc:
        print(f"pywebview не запустился ({str(exc)[:160]})", file=sys.stderr)
        return False


def run_app_window(url: str, width: int, height: int) -> bool:
    found = find_browser()
    if not found:
        return False
    exe, _kind = found
    profile = S.DATA_DIR / "window_profile"
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        exe,
        f"--app={url}",
        f"--window-size={width},{height}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    print(f"окно браузера: {Path(exe).name}")
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process.wait()
    return True


def serve(port: int) -> tuple[threading.Thread, uvicorn.Server, str]:
    url = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    return thread, server, url


def main() -> None:
    parser = argparse.ArgumentParser(description="Cutter")
    parser.add_argument("--window-mode", default="auto",
                        choices=["auto", "window", "native", "app", "browser", "none"])
    parser.add_argument("--browser", action="store_true", help="то же, что --window-mode browser")
    parser.add_argument("--no-window", action="store_true", help="то же, что --window-mode none")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--width", type=int, default=1480)
    parser.add_argument("--height", type=int, default=940)
    args = parser.parse_args()

    mode = args.window_mode
    if args.no_window:
        mode = "none"
    if args.browser:
        mode = "browser"

    port = free_port(args.port)
    thread, server, url = serve(port)
    if not wait_ready(url):
        print("сервер не поднялся за 25 секунд", file=sys.stderr)
        sys.exit(1)
    print(f"Cutter: {url}")

    def wait_server() -> None:
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

    if mode == "none":
        wait_server()
        return
    if mode == "browser":
        webbrowser.open(url)
        wait_server()
        return
    if mode == "window":
        if not run_native_window(url, args.width, args.height):
            sys.exit("нативное окно Cutter.exe не найдено — собери его tools/build_window.ps1")
        server.should_exit = True
        return
    if mode == "native":
        if not run_pywebview(url, args.width, args.height):
            sys.exit("pywebview не поднялся")
        server.should_exit = True
        return
    if mode == "app":
        run_app_window(url, args.width, args.height)
        server.should_exit = True
        return

    # auto: нативное окно → pywebview → окно Edge → вкладка
    if run_native_window(url, args.width, args.height):
        server.should_exit = True
        return
    if run_pywebview(url, args.width, args.height):
        server.should_exit = True
        return
    if run_app_window(url, args.width, args.height):
        server.should_exit = True
        return
    webbrowser.open(url)
    wait_server()


if __name__ == "__main__":
    main()
