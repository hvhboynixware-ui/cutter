# -*- coding: utf-8 -*-
"""Настройки приложения: один JSON в app/data/settings.json.

Секреты (API-ключи) лежат там же. Файл не коммитится, наружу через API отдаётся маска.
"""

from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent.parent   # Cutter/app
ROOT = APP_DIR.parent                               # Cutter
DATA_DIR = APP_DIR / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"
MODELS_DIR = ROOT / "models"

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "llm": {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "api_key": "",
        "fallback": True,
        "keys": {"openrouter": "", "gemini": "", "groq": ""},
    },
    "stt": {"whisper_size": "base", "language": "ru"},
    "render": {
        "clips": 5,
        "min_dur": 18,
        "max_dur": 60,
        "style": "classic",
        "crf": 20,
        "fps": 30,
    },
    "upload": {
        "engine": "plain",
        "profile_id": "",
        "platforms": ["tiktok"],
        "dry_run": True,
        "visibility": "public",
        "antidetect_token": "",
    },
    "paths": {"outdir": ""},
}

SECRET_PATHS = (("llm", "api_key"), ("upload", "antidetect_token"))


def _merge(base: dict, patch: dict) -> dict:
    result = deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def ensure_dirs() -> None:
    for path in (
        DATA_DIR,
        DATA_DIR / "jobs",
        DATA_DIR / "shots",
        DATA_DIR / "browser_profiles",
        MODELS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    ensure_dirs()
    if not SETTINGS_PATH.is_file():
        save(DEFAULTS)
        return deepcopy(DEFAULTS)
    try:
        stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        stored = {}
    return _merge(DEFAULTS, stored)


def save(settings: dict) -> dict:
    ensure_dirs()
    clean = _merge(DEFAULTS, settings)
    SETTINGS_PATH.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def update(patch: dict) -> dict:
    return save(_merge(load(), patch))


def mask_secrets(settings: dict) -> dict:
    copy = deepcopy(settings)
    for section, key in SECRET_PATHS:
        value = copy.get(section, {}).get(key, "")
        copy.setdefault(section, {})[key] = _mask(value)
    keys = copy.get("llm", {}).get("keys", {})
    for provider in list(keys):
        keys[provider] = _mask(keys[provider])
    return copy


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def resolve_key(settings: dict, provider: str) -> str:
    """Ключ для конкретного провайдера: общий api_key только для основного."""
    llm = settings.get("llm", {})
    keys = llm.get("keys", {}) or {}
    if keys.get(provider):
        return keys[provider]
    if llm.get("provider") == provider:
        return llm.get("api_key", "")
    return ""


def whisper_model_path(settings: dict) -> tuple[str, bool]:
    """(путь к папке модели или имя, установлена ли локально)."""
    size = settings.get("stt", {}).get("whisper_size", "base")
    local = MODELS_DIR / f"faster-whisper-{size}"
    if local.is_dir() and (local / "model.bin").is_file() and (local / "model.bin").stat().st_size > 1024:
        return str(local), True
    return size, False


def installed_models() -> list[dict]:
    result = []
    for path in sorted(MODELS_DIR.glob("faster-whisper-*")):
        model_bin = path / "model.bin"
        if model_bin.is_file():
            result.append({
                "size": path.name.replace("faster-whisper-", ""),
                "path": str(path),
                "bytes": model_bin.stat().st_size,
            })
    return result


def outdir(settings: dict) -> Path:
    raw = settings.get("paths", {}).get("outdir", "")
    path = Path(raw).expanduser() if raw else DATA_DIR / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ffmpeg_path() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_path() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
