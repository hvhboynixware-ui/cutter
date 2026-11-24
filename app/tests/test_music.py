# -*- coding: utf-8 -*-
"""Пак музыки: файлы на месте, манифест валиден, размеры и длительности в норме.

Тест офлайн: ffmpeg/ffprobe в CI может отсутствовать, поэтому проверяем манифест и файлы.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import effects as FX  # noqa: E402

MUSIC_DIR = Path(__file__).resolve().parent.parent / "assets" / "music"


def _manifest() -> list[dict]:
    path = MUSIC_DIR / "manifest.json"
    assert path.is_file(), f"нет манифеста: {path}"
    items = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(items, list) and items, "манифест пустой"
    return items


def test_eight_tracks():
    items = _manifest()
    assert len(items) == 11
    ids = {item["id"] for item in items}
    assert {"drama-guzheng", "brainrot-twist", "imperial-destiny"} <= ids


def test_manifest_fields():
    required = {"id", "title", "mood", "bpm", "duration", "license"}
    for item in _manifest():
        assert required <= set(item), f"в {item.get('id')} не хватает полей"
        assert item["id"] == item["id"].strip().lower()
        assert 60 <= int(item["bpm"]) <= 200, item["bpm"]
        assert 15 <= float(item["duration"]) <= 35, item["duration"]
        assert "generated for cutter" in item["license"].lower()


def test_files_exist_and_not_empty():
    for item in _manifest():
        file_name = item.get("file") or f"{item['id']}.mp3"
        path = MUSIC_DIR / file_name
        assert path.is_file(), f"нет файла {path}"
        size = path.stat().st_size
        assert size > 100 * 1024, f"{path.name}: слишком маленький ({size} байт)"
        with open(path, "rb") as handle:
            head = handle.read(3)
        is_id3 = head == b"ID3"
        is_frame_sync = head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
        assert is_id3 or is_frame_sync, f"{path.name}: не похоже на mp3"


def test_manifest_matches_files():
    listed = set()
    for item in _manifest():
        listed.add(item.get("file") or f"{item['id']}.mp3")
    on_disk = {p.name for p in MUSIC_DIR.glob("*.mp3")}
    assert listed == on_disk, f"манифест и файлы расходятся: {listed ^ on_disk}"


def test_ids_are_unique_and_known():
    items = _manifest()
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids)), "дублирующиеся id"
    assert len(FX.list_music()) == len(items), "list_music не видит все треки"
    for item in FX.list_music():
        assert item["title"] and item["file"].endswith(".mp3")


def test_music_path_resolves():
    first = _manifest()[0]["id"]
    assert FX.music_path(first) is not None
    assert FX.music_path("none") is None
