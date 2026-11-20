# -*- coding: utf-8 -*-
"""Движок браузера: локальный Playwright-профиль или профиль антидетекта (Dolphin / AdsPower).

Все загрузки идут через один из трёх движков:
  plain     — встроенный Playwright с persistent-профилем (логин сохраняется между запусками)
  dolphin   — Dolphin Anty, подключаемся по CDP к уже поднятому профилю
  adspower  — AdsPower, то же самое

Антидетект-клиенты не переизобретаем: поднимаем профиль их локальным API и цепляемся к CDP.
Так сохраняются их фингерпринты и прокси.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

DOLPHIN_API = os.environ.get("DOLPHIN_API", "http://127.0.0.1:3001")
ADSPOWER_API = os.environ.get("ADSPOWER_API", "http://local.adspower.net:50325")

BROWSER_CANDIDATES = [
    ("msedge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ("msedge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ("chrome", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ("chrome", os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")),
]


@dataclass
class BrowserSession:
    engine: str
    profile_id: str
    context: Any
    page: Any
    _playwright: Any = None
    _browser: Any = None
    _stop_url: str = ""
    _persistent_dir: str = ""
    extra: dict = field(default_factory=dict)


def _http_json(url: str, *, token: str = "", timeout: float = 15.0, method: str = "GET") -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ответ не JSON: {raw[:200]}") from exc


def detect_browser_channel() -> str:
    """Какой бинарь отдаём Playwright: msedge / chrome. Пустая строка — не нашли."""
    custom = os.environ.get("CUTTER_BROWSER_CHANNEL", "").strip()
    if custom:
        return custom
    for channel, path in BROWSER_CANDIDATES:
        if path and Path(path).is_file():
            return channel
    return ""


def _find_browser_executable() -> str:
    for _, path in BROWSER_CANDIDATES:
        if path and Path(path).is_file():
            return path
    return ""


def list_profiles(engine: str, *, token: str = "") -> tuple[bool, list[dict], str]:
    """(сервис запущен, профили, текст ошибки)."""
    engine = (engine or "plain").lower()
    if engine == "plain":
        return True, [], ""

    if engine == "dolphin":
        try:
            data = _http_json(f"{DOLPHIN_API}/v1.0/browser_profiles?limit=200", token=token)
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            return False, [], f"Dolphin Anty не отвечает на {DOLPHIN_API}: {exc}"
        items = []
        for item in (data.get("data") or []):
            items.append({
                "id": str(item.get("id") or ""),
                "name": item.get("name") or f"profile {item.get('id')}",
                "status": "running" if item.get("status") == "started" else "idle",
            })
        return True, items, ""

    if engine == "adspower":
        try:
            data = _http_json(f"{ADSPOWER_API}/api/v1/user/list?page=1&page_size=200", token=token)
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            return False, [], f"AdsPower не отвечает на {ADSPOWER_API}: {exc}"
        if int(data.get("code", -1)) != 0:
            return False, [], f"AdsPower вернул ошибку: {data.get('msg') or data}"
        items = []
        for item in ((data.get("data") or {}).get("list") or []):
            items.append({
                "id": str(item.get("user_id") or ""),
                "name": item.get("name") or str(item.get("user_id")),
                "status": "running" if str(item.get("status")) == "1" else "idle",
            })
        return True, items, ""

    return False, [], f"неизвестный движок браузера: {engine}"


def _parse_proxy(proxy: str) -> dict | None:
    """host:port@user:pass -> playwright proxy dict."""
    proxy = proxy.strip()
    if not proxy:
        return None
    server, _, auth = proxy.partition("@")
    result: dict[str, str] = {"server": f"http://{server}" if "://" not in server else server}
    if auth:
        user, _, password = auth.partition(":")
        result["username"] = user
        result["password"] = password
    return result


def _safe_slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)
    return cleaned.strip("-") or "default"


def _profile_dir(persistent_root: Path, profile_id: str) -> Path:
    path = persistent_root / _safe_slug(profile_id or "default")
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_session(target: BrowserTarget, *, persistent_root: Path, log: Callable[[str], None] = print) -> BrowserSession:
    from playwright.sync_api import sync_playwright

    engine = (target.engine or "plain").lower()
    playwright = sync_playwright().start()

    try:
        if engine == "plain":
            channel = detect_browser_channel()
            executable = _find_browser_executable()
            if not channel and not executable:
                raise RuntimeError("не нашёл Chrome/Edge. Поставь любой из них или задай CUTTER_BROWSER_CHANNEL")
            profile_dir = _profile_dir(persistent_root, target.profile_id)
            log(f"браузер: {channel or executable}, профиль {profile_dir.name}")
            options: dict[str, Any] = {
                "user_data_dir": str(profile_dir),
                "headless": target.headless,
                "viewport": None,
                "args": ["--start-maximized", "--disable-blink-features=AutomationControlled"],
                "ignore_default_args": ["--enable-automation"],
            }
            if channel:
                options["channel"] = channel
            else:
                options["executable_path"] = executable
            proxy = _parse_proxy(target.proxy)
            if proxy:
                options["proxy"] = proxy
            context = playwright.chromium.launch_persistent_context(**options)
            pages = context.pages
            page = pages[0] if pages else context.new_page()
            return BrowserSession(engine=engine, profile_id=target.profile_id, context=context,
                                  page=page, _playwright=playwright, _persistent_dir=str(profile_dir))

        if engine == "dolphin":
            running, profiles, error = list_profiles("dolphin")
            if not running:
                raise RuntimeError(error)
            profile_id = target.profile_id or (profiles[0]["id"] if profiles else "")
            if not profile_id:
                raise RuntimeError("в Dolphin нет ни одного профиля")
            log(f"Dolphin: поднимаю профиль {profile_id}")
            data = _http_json(f"{DOLPHIN_API}/v1.0/browser_profiles/{profile_id}/start?automation=1")
            payload = data.get("data") or {}
            endpoint = payload.get("wsEndpoint") or payload.get("wsEndpointDolphin") or ""
            if not endpoint:
                raise RuntimeError(f"Dolphin не отдал ws endpoint: {str(data)[:300]}")
            browser = playwright.chromium.connect_over_cdp(endpoint, timeout=60_000)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            return BrowserSession(engine=engine, profile_id=profile_id, context=context, page=page,
                                  _playwright=playwright, _browser=browser,
                                  _stop_url=f"{DOLPHIN_API}/v1.0/browser_profiles/{profile_id}/stop")

        if engine == "adspower":
            running, profiles, error = list_profiles("adspower")
            if not running:
                raise RuntimeError(error)
            profile_id = target.profile_id or (profiles[0]["id"] if profiles else "")
            if not profile_id:
                raise RuntimeError("в AdsPower нет ни одного профиля")
            log(f"AdsPower: поднимаю профиль {profile_id}")
            data = _http_json(f"{ADSPOWER_API}/api/v1/browser/start?user_id={profile_id}")
            if int(data.get("code", -1)) != 0:
                raise RuntimeError(f"AdsPower не поднял профиль: {data.get('msg') or data}")
            endpoint = ((data.get("data") or {}).get("ws") or {}).get("puppeteer", "")
            if not endpoint:
                raise RuntimeError(f"AdsPower не отдал ws endpoint: {str(data)[:300]}")
            browser = playwright.chromium.connect_over_cdp(endpoint, timeout=60_000)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            return BrowserSession(engine=engine, profile_id=profile_id, context=context, page=page,
                                  _playwright=playwright, _browser=browser,
                                  _stop_url=f"{ADSPOWER_API}/api/v1/browser/stop?user_id={profile_id}")

        raise RuntimeError(f"неизвестный движок браузера: {engine}")
    except Exception:
        try:
            playwright.stop()
        except Exception:
            pass
        raise


def close_session(session: BrowserSession, log: Callable[[str], None] = print) -> None:
    if session is None:
        return
    # антидетект-профили гасим через их же API, чтобы клиент не держал сессию
    if session._stop_url:
        try:
            _http_json(session._stop_url)
        except Exception:
            pass
    try:
        if session._browser is not None:
            session._browser.close()
        elif session.context is not None:
            session.context.close()
    except Exception:
        pass
    try:
        if session._playwright is not None:
            session._playwright.stop()
    except Exception:
        pass


def screenshot(session: BrowserSession, path: Path) -> Path | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        session.page.screenshot(path=str(path), full_page=False)
        return path
    except Exception:
        return None


def wait_for_any(page, selectors: list[str], *, timeout: float = 60_000, visible: bool = True):
    """Ждёт первый селектор из списка. Возвращает локатор или None."""
    deadline = time.time() + timeout / 1000
    last_error: Exception | None = None
    while time.time() < deadline:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if visible:
                    if locator.is_visible():
                        return locator
                elif locator.count() > 0:
                    return locator
            except Exception as exc:
                last_error = exc
        time.sleep(0.4)
    return None
