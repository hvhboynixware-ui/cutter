# -*- coding: utf-8 -*-
"""Точка входа: локальный сервер + окно приложения.

Режимы окна (--window-mode):
  auto    — сначала нативное окно (pywebview), если не поднялось — окно Edge/Chrome в режиме приложения
  native  — только pywebview
  app     — только окно Edge/Chrome без адресной строки
  browser — обычная вкладка в браузере по умолчанию
  none    — только сервер, без окна (отладка UI)
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

from app.server import app  # noqa: E402

APP_TITLE = "Cutter — нарезка в шортсы"
DATA_DIR = Path(__file__).resolve().parent / "data"

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


def find_browser() -> tuple[str, str] | None:
    for path, kind in BROWSERS:
        if path and Path(path).is_file():
            return path, kind
    return None


def open_native_window(url: str, width: int, height: int) -> bool:
    """Нативное окно через pywebview. Возвращает False, если не поднялось."""
    try:
        import webview
    except Exception as exc:
        print(f"нативное окно недоступно ({exc})", file=sys.stderr)
        return False

    try:
        webview.create_window(APP_TITLE, url, width=width, height=height,
                              min_size=(1120, 720), background_color="#0b0b12")
        webview.start()
        return True
    except Exception as exc:
        print(f"нативное окно не запустилось ({str(exc)[:200]})", file=sys.stderr)
        return False


def open_app_window(url: str, width: int, height: int) -> bool:
    """Окно браузера в режиме приложения: без адресной строки, отдельный профиль."""
    found = find_browser()
    if not found:
        return False
    exe, _kind = found
    profile = DATA_DIR / "window_profile"
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        exe,
        f"--app={url}",
        f"--window-size={width},{height}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    print(f"открываю окно через {Path(exe).name}")
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
                        choices=["auto", "native", "app", "browser", "none"])
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

    if mode == "none":
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        return

    if mode == "browser":
        webbrowser.open(url)
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        return

    if mode == "native":
        if not open_native_window(url, args.width, args.height):
            print("нативное окно не поднялось — запусти с --window-mode app", file=sys.stderr)
            sys.exit(1)
        server.should_exit = True
        return

    if mode == "app":
        if not open_app_window(url, args.width, args.height):
            print("не нашёл Edge или Chrome", file=sys.stderr)
            webbrowser.open(url)
            try:
                while thread.is_alive():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
        server.should_exit = True
        return

    # auto: нативное окно, иначе окно-приложение, иначе вкладка
    if open_native_window(url, args.width, args.height):
        server.should_exit = True
        return
    if open_app_window(url, args.width, args.height):
        server.should_exit = True
        return
    webbrowser.open(url)
    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
