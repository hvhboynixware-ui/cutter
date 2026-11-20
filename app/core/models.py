# -*- coding: utf-8 -*-
"""Скачивание локальных моделей whisper.

huggingface.co из РФ отдаёт 403 на CDN, поэтому основной источник — modelscope.cn,
запасной — HuggingFace. Модель кладётся в <repo>/models/faster-whisper-<size>.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Callable

from . import settings as S

FILES = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]

SOURCES = [
    ("ModelScope", "https://modelscope.cn/api/v1/models/pengzhendong/faster-whisper-{size}/repo?Revision=master&FilePath={file}"),
    ("HuggingFace", "https://huggingface.co/Systran/faster-whisper-{size}/resolve/main/{file}"),
]

SIZES = [
    {"id": "tiny", "label": "tiny", "mb": 75, "note": "только проверить, что работает"},
    {"id": "base", "label": "base", "mb": 138, "note": "черновое качество"},
    {"id": "small", "label": "small", "mb": 500, "note": "минимум для работы"},
    {"id": "medium", "label": "medium", "mb": 1500, "note": "рекомендуется"},
    {"id": "large-v3", "label": "large-v3", "mb": 3000, "note": "максимум качества, нужен быстрый CPU/GPU"},
]


def _human(num: int) -> str:
    if num >= 1 << 30:
        return f"{num / (1 << 30):.1f} ГБ"
    return f"{num / (1 << 20):.0f} МБ"


def download_model(
    size: str,
    *,
    dest: Path | None = None,
    log: Callable[[str], None] = print,
    progress: Callable[[float, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    target = dest or (S.MODELS_DIR / f"faster-whisper-{size}")
    target.mkdir(parents=True, exist_ok=True)
    log(f"модель {size} -> {target}")

    known = {s["id"]: s for s in SIZES}
    total_mb = known.get(size, {}).get("mb", 0)

    for index, name in enumerate(FILES, start=1):
        if cancel and cancel():
            raise RuntimeError("отменено")
        file_path = target / name
        if file_path.is_file() and file_path.stat().st_size > 1024:
            log(f"{name}: уже есть")
            continue

        last_error: Exception | None = None
        for label, template in SOURCES:
            url = template.format(size=size, file=name)
            try:
                log(f"{name}: качаю с {label}")
                _fetch(url, file_path, size=size, log=log, progress=progress, cancel=cancel)
                last_error = None
                break
            except Exception as exc:  # пробуем следующий источник
                last_error = exc
                log(f"{label} не отдал {name}: {str(exc)[:120]}")
        if last_error is not None:
            raise RuntimeError(f"не получилось скачать {name}: {last_error}")
        if progress and total_mb:
            progress(index / len(FILES) * 100, f"{name} готов")

    model_bin = target / "model.bin"
    if not model_bin.is_file() or model_bin.stat().st_size < 1024:
        raise RuntimeError("model.bin не скачался — попробуй ещё раз")
    log(f"готово: {_human(model_bin.stat().st_size)}")
    return target


def _fetch(
    url: str,
    dest: Path,
    *,
    size: str,
    log: Callable[[str], None],
    progress: Callable[[float, str], None] | None,
    cancel: Callable[[], bool] | None,
) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Cutter/1.0"})
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
            if progress and total:
                progress(done / total * 100, f"{dest.name}: {_human(done)} / {_human(total)}")
    if dest.exists():
        dest.unlink()
    tmp.rename(dest)
