# -*- coding: utf-8 -*-
"""Скачивание локальных моделей whisper.

Источники: ModelScope (основной, из РФ работает без VPN) и HuggingFace (запасной).
Размеры файлов ниже — реальные Content-Length с ModelScope, чтобы прогресс и проверка
после скачивания были точными, а не «примерно 500 МБ».
"""

from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path
from typing import Callable

from . import settings as S

MB = 1024 * 1024

FILES = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]

# точные размеры (байты), снятые с ModelScope; model.bin — основной вес
SIZES = [
    {"id": "tiny", "label": "tiny", "bytes": 78_207_000, "note": "только проверить, что работает"},
    {"id": "base", "label": "base", "bytes": 147_850_000, "note": "черновое качество"},
    {"id": "small", "label": "small", "bytes": 486_200_000, "note": "минимум для работы"},
    {"id": "medium", "label": "medium", "bytes": 1_530_800_000, "note": "рекомендуется"},
    {"id": "large-v3", "label": "large-v3", "bytes": 3_089_700_000, "note": "максимум качества, нужен быстрый CPU/GPU"},
]

SOURCES = [
    ("ModelScope", "https://modelscope.cn/api/v1/models/pengzhendong/faster-whisper-{size}/repo?Revision=master&FilePath={file}"),
    ("HuggingFace", "https://huggingface.co/Systran/faster-whisper-{size}/resolve/main/{file}"),
]


def _human(num: int) -> str:
    if num >= 1 << 30:
        return f"{num / (1 << 30):.2f} ГБ"
    return f"{num / MB:.1f} МБ"


def size_info(size: str) -> dict | None:
    return next((item for item in SIZES if item["id"] == size), None)


def expected_bytes(size: str) -> int:
    info = size_info(size)
    return int(info["bytes"]) if info else 0


def folder_bytes(path: Path) -> int:
    total = 0
    for item in path.glob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def is_installed(size: str) -> bool:
    folder = S.MODELS_DIR / f"faster-whisper-{size}"
    model_bin = folder / "model.bin"
    if not model_bin.is_file() or model_bin.stat().st_size < 1024:
        return False
    expected = expected_bytes(size)
    if expected:
        return abs(folder_bytes(folder) - expected) <= expected * 0.02
    return True


def installed_models() -> list[dict]:
    result = []
    for item in SIZES:
        folder = S.MODELS_DIR / f"faster-whisper-{item['id']}"
        if folder.is_dir() and (folder / "model.bin").is_file():
            actual = folder_bytes(folder)
            result.append({
                "size": item["id"],
                "path": str(folder),
                "bytes": actual,
                "expected": item["bytes"],
                "complete": is_installed(item["id"]),
            })
    return result


def delete_model(size: str) -> bool:
    folder = S.MODELS_DIR / f"faster-whisper-{size}"
    if not folder.is_dir():
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()


def download_model(
    size: str,
    *,
    dest: Path | None = None,
    log: Callable[[str], None] = print,
    progress: Callable[[float, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    info = size_info(size)
    if info is None:
        raise RuntimeError(f"неизвестный размер модели: {size}")

    target = dest or (S.MODELS_DIR / f"faster-whisper-{size}")
    target.mkdir(parents=True, exist_ok=True)
    total_expected = int(info["bytes"])
    log(f"модель {size}: ожидается {_human(total_expected)}, папка {target}")

    total_done = 0
    for name in FILES:
        if cancel and cancel():
            raise RuntimeError("отменено")
        file_path = target / name
        if file_path.is_file() and file_path.stat().st_size > 1024:
            total_done += file_path.stat().st_size
            log(f"{name}: уже на диске ({_human(file_path.stat().st_size)})")
            continue

        last_error: Exception | None = None
        for label, template in SOURCES:
            url = template.format(size=size, file=name)
            try:
                log(f"{name}: качаю с {label}")
                _fetch(
                    url, file_path,
                    log=log,
                    cancel=cancel,
                    on_bytes=lambda chunk_done, chunk_total, _base=total_done, _name=name: _report(
                        progress, log, total_done + chunk_done, total_expected, f"{_name}: {_human(chunk_done)} / {_human(chunk_total)}"
                    ),
                )
                last_error = None
                break
            except Exception as exc:  # пробуем следующий источник
                last_error = exc
                log(f"{label} не отдал {name}: {str(exc)[:120]}")
        if last_error is not None:
            raise RuntimeError(f"не получилось скачать {name}: {last_error}")

        total_done += file_path.stat().st_size
        if progress:
            progress(total_done / max(1, total_expected) * 100, f"{name} готов")

    actual = folder_bytes(target)
    log(f"проверка: на диске {_human(actual)}, ожидалось ~{_human(total_expected)}")
    if total_expected and actual < total_expected * 0.9:
        raise RuntimeError(
            f"модель скачалась не полностью ({_human(actual)} из {_human(total_expected)}). "
            "Удали её и попробуй ещё раз"
        )
    model_bin = target / "model.bin"
    if not model_bin.is_file() or model_bin.stat().st_size < 1024:
        raise RuntimeError("model.bin не скачался — попробуй ещё раз")
    log(f"готово: {_human(actual)}")
    return target


def _report(progress, log, done: int, total: int, message: str) -> None:
    if progress and total:
        progress(min(99.0, done / total * 100), message)


def _fetch(
    url: str,
    dest: Path,
    *,
    log: Callable[[str], None],
    cancel: Callable[[], bool] | None,
    on_bytes: Callable[[int, int], None],
) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Cutter/1.1"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(request, timeout=120) as response, open(tmp, "wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel and cancel():
                handle.close()
                tmp.unlink(missing_ok=True)
                raise RuntimeError("отменено")
            chunk = response.read(1 << 20)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            on_bytes(done, total)
    if dest.exists():
        dest.unlink()
    tmp.rename(dest)
