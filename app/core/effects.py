# -*- coding: utf-8 -*-
"""Эффекты кадра и музыкальная подложка.

Всё — обычные ffmpeg-фильтры, чтобы рендер оставался одним проходом без внешних зависимостей.
Пресеты собраны так, чтобы картинка выглядела дороже, но не разваливалась на телефоне.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Effect:
    id: str
    label: str
    hint: str
    video: str          # ffmpeg-фильтр, применяется после кадрирования
    audio: str = ""     # необязательная обработка звука
    tone: str = "#8a8a96"  # цвет точки в интерфейсе


EFFECTS: dict[str, Effect] = {
    "none": Effect("none", "Без обработки", "как снято", "", "#6b675e"),
    "cinema": Effect(
        "cinema", "Кино", "teal-orange, виньетка",
        "eq=contrast=1.09:saturation=1.04:gamma=0.99,"
        "colorbalance=rs=0.06:bs=-0.055:rm=0.03:bm=-0.02,"
        "vignette=angle=PI/5",
        "#4f9fd8",
    ),
    "aura": Effect(
        "aura", "Аура", "тёплый свет, мягкость",
        "eq=brightness=0.025:contrast=1.03:saturation=1.12:gamma=1.02,"
        "colorbalance=rs=0.05:gs=0.02:bs=-0.04,"
        "unsharp=5:5:0.45,vignette=angle=PI/6",
        "#f0a35e",
    ),
    "warm": Effect(
        "warm", "Тёплый", "ламповый вечер",
        "colorbalance=rs=0.07:gs=0.03:bs=-0.06,eq=saturation=1.08:contrast=1.03",
        "#e8a25a",
    ),
    "vhs": Effect(
        "vhs", "VHS", "зерно и рассинхрон цвета",
        "rgbashift=rh=2:bh=-2,noise=alls=11:allf=t,eq=saturation=0.9:contrast=1.05",
        "#7bd6a8",
    ),
    "noir": Effect(
        "noir", "Ч/Б", "жёсткий контраст",
        "hue=s=0,eq=contrast=1.16:brightness=-0.015,vignette=angle=PI/4.5",
        "#c9c9d2",
    ),
}

DEFAULT_EFFECT = "none"


def get_effect(effect_id: str | None) -> Effect:
    return EFFECTS.get(effect_id or DEFAULT_EFFECT, EFFECTS[DEFAULT_EFFECT])


def effect_ids() -> list[dict]:
    return [{"id": fx.id, "label": fx.label, "hint": fx.hint, "tone": fx.tone} for fx in EFFECTS.values()]


# ---------------------------------------------------------------- защита от повторов

@dataclass(frozen=True)
class Protection:
    """Лёгкие трансформации: картинка и звук перестают быть байт-в-байт копией исходника."""

    flip: bool = True
    zoom: float = 1.0          # 1.03 — небольшой кроп по краям
    speed: float = 1.0         # 1.02 — на 2% быстрее
    grain: bool = True


def protection_filter(level: str) -> tuple[str, Protection]:
    """level: off | light | strong."""
    if level == "light":
        return (
            "hflip,scale=iw*1.03:ih*1.03,crop=iw/1.03:ih/1.03,noise=alls=5:allf=t",
            Protection(flip=True, zoom=1.03, speed=1.0, grain=True),
        )
    if level == "strong":
        return (
            "hflip,scale=iw*1.06:ih*1.06,crop=iw/1.06:ih/1.06,"
            "noise=alls=9:allf=t,eq=brightness=0.006:contrast=1.012",
            Protection(flip=True, zoom=1.06, speed=1.03, grain=True),
        )
    return "", Protection(flip=False, zoom=1.0, speed=1.0, grain=False)


# ---------------------------------------------------------------- музыка

MUSIC_ID_NONE = "none"


def music_dir() -> Path:
    from . import settings as S

    bundled = S.ROOT / "app" / "assets" / "music"
    return bundled


def list_music() -> list[dict]:
    """Треки из манифеста; если папки нет — пусто."""
    import json

    folder = music_dir()
    manifest = folder / "manifest.json"
    if not manifest.is_file():
        return []
    try:
        items = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return []
    result = []
    for item in items:
        file_name = item.get("file") or f"{item.get('id')}.mp3"
        if (folder / file_name).is_file():
            result.append({
                "id": str(item.get("id")),
                "title": item.get("title") or item.get("id"),
                "mood": item.get("mood", ""),
                "bpm": item.get("bpm", 0),
                "duration": item.get("duration", 0),
                "file": file_name,
            })
    return result


def music_path(music_id: str | None) -> Path | None:
    if not music_id or music_id == MUSIC_ID_NONE:
        return None
    for item in list_music():
        if item["id"] == music_id:
            return music_dir() / item["file"]
    return None


def build_audio_graph(
    *,
    has_source_audio: bool,
    music: Path | None,
    music_volume: float = 0.18,
    duck: bool = True,
) -> tuple[list[str], str]:
    """Возвращает (доп. аргументы входа, строка filter_complex для звука с меткой [a]).

    Источник звука — вход 0, музыка — вход 1.
    """
    if music is None:
        if not has_source_audio:
            return [], ""
        return [], (
            "[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]"
        )

    args = ["-stream_loop", "-1", "-i", str(music)]
    music_chain = f"[1:a]volume={music_volume:.3f},aresample=48000[mus]"

    if not has_source_audio:
        return args, f"{music_chain};[mus]anull[a]"

    voice_chain = "[0:a]loudnorm=I=-14:TP=-1.5:LRA=11[voice]"
    if duck:
        graph = (
            f"{voice_chain};"
            f"{music_chain};"
            f"[mus][voice]sidechaincompress=threshold=0.035:ratio=8:attack=12:release=380[musduck];"
            f"[voice][musduck]amix=inputs=2:duration=first:dropout_transition=0,"
            f"alimiter=limit=0.95[a]"
        )
    else:
        graph = (
            f"{voice_chain};"
            f"{music_chain};"
            f"[voice][mus]amix=inputs=2:duration=first:dropout_transition=0,"
            f"alimiter=limit=0.95[a]"
        )
    return args, graph


# ---------------------------------------------------------------- общий фильтр кадра

def compose_video_chain(effect_id: str, protection_level: str) -> str:
    """Последовательность фильтров после кадрирования: защита, эффект. Пустая строка — если нечего."""
    parts: list[str] = []
    protect, _ = protection_filter(protection_level)
    if protect:
        parts.append(protect)
    effect = get_effect(effect_id)
    if effect.video:
        parts.append(effect.video)
    return ",".join(part for part in parts if part)


def apply_chain(base: str, chain: str, out_label: str) -> str:
    """Аккуратно вклеивает цепочку перед меткой и перед прожигом субтитров."""
    if not chain:
        return base
    return base.replace(",ass=", f",{chain},ass=").replace("[v]", f"[{out_label}]")
