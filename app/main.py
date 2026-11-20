# -*- coding: utf-8 -*-
"""Точка входа: локальный сервер + нативное окно.

Запуск:  python -m app.main
Только сервер (браузер):  python -m app.main --browser
"""

from __future__ import annotations

import argparse
import socket
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Cutter")
    parser.add_argument("--browser", action="store_true", help="открыть в браузере, без нативного окна")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-window", action="store_true", help="только сервер (для отладки UI)")
    args = parser.parse_args()

    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()

    if not wait_ready(url):
        print("сервер не поднялся за 25 секунд", file=sys.stderr)
        sys.exit(1)

    print(f"Cutter: {url}")

    if args.no_window:
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        return

    if args.browser:
        webbrowser.open(url)
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        return

    try:
        import webview
    except Exception as exc:
        print(f"pywebview недоступен ({exc}), открываю в браузере", file=sys.stderr)
        webbrowser.open(url)
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        return

    webview.create_window(
        APP_TITLE,
        url,
        width=1480,
        height=940,
        min_size=(1120, 720),
        background_color="#0b0b12",
    )
    webview.start()
    server.should_exit = True


if __name__ == "__main__":
    main()
