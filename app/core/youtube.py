# -*- coding: utf-8 -*-
"""Скачивание с YouTube через yt-dlp: проверка ссылки, прогресс, отмена, понятные ошибки.

Модуль ничего не знает про Job и UI: на вход ссылка и папка, на выход файл и метаданные.
Сеть живёт только в рантайме — все тесты подменяют yt_dlp.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import yt_dlp

# Хосты, которые считаем YouTube. Ссылки на другие сервисы отсекаем до обращения к сети.
_YT_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
# Служебные файлы yt-dlp, которые не считаем готовым видео.
_SKIP_SUFFIXES = _IMAGE_SUFFIXES | {
    ".part", ".ytdl", ".temp", ".json", ".description", ".vtt", ".srt", ".ass", ".txt",
}

# Маркер отмены в тексте исключения: yt-dlp может обернуть наше исключение в DownloadError.
_CANCEL_MARK = "__CUTTER_CANCELLED__"

# Кириллица в заголовке иначе вырезается regex'ом и slug остаётся пустым.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Порядок важен: «private» должен сработать раньше общего «unavailable».
_ERROR_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("private video", "this video is private", "видео приватное"),
        "Видео приватное — добавь его в доступ или скачай другое.",
    ),
    (
        ("sign in to confirm your age", "confirm your age", "age-restricted", "age restricted",
         "inappropriate for some users"),
        "Видео с возрастным ограничением: YouTube требует вход в аккаунт.",
    ),
    (
        ("not available in your country", "available in your country", "blocked it in your country",
         "geo-restricted", "geo restriction", "not available from your location",
         "blocked in your country"),
        "Видео заблокировано в твоём регионе (гео-блок) — нужен VPN или другое видео.",
    ),
    (
        ("members-only", "join this channel", "channel members"),
        "Видео доступно только участникам канала (members-only).",
    ),
    (
        ("confirm you're not a bot", "confirm you are not a bot", "sign in to confirm",
         "cookies", "bot check"),
        "YouTube требует проверку «я не робот» — войди в аккаунт через cookies или повтори позже.",
    ),
    (
        ("ffmpeg", "ffprobe"),
        "Не найден ffmpeg — без него не склеить видео и звук.",
    ),
    (
        ("this video is unavailable", "video unavailable", "removed by the uploader",
         "has been removed", "does not exist", "video is no longer available", "not found"),
        "Видео удалено или недоступно по этой ссылке.",
    ),
    (
        ("this live stream", "is live", "live event will begin", "premieres"),
        "Это прямой эфир — дождись окончания и скачай запись.",
    ),
    (
        ("http error 429", "too many requests"),
        "Слишком много запросов к YouTube — подожди пару минут.",
    ),
    (
        ("http error 403", "forbidden"),
        "YouTube отклонил запрос (403) — обнови yt-dlp или повтори позже.",
    ),
    (
        ("requested format is not available", "no video formats found"),
        "Не нашлось подходящего формата под выбранное качество.",
    ),
    (
        ("timed out", "timeout", "connection", "getaddrinfo", "ssl", "network"),
        "Проблема с сетью — проверь подключение или VPN.",
    ),
    (
        ("unsupported url", "not a valid url", "invalid url"),
        "Не похоже на ссылку YouTube — проверь адрес.",
    ),
)


@dataclass
class DownloadResult:
    path: Path
    title: str
    duration: float
    uploader: str
    thumbnail: Path | None = None
    source_id: str = ""


class DownloadCancelled(RuntimeError):
    """Скачивание отменено пользователем, временные файлы подчищены."""


class _Cancelled(Exception):
    """Внутренний сигнал из прогресс-хука: yt-dlp не должен считать это сетевым сбоем."""


# ---------------------------------------------------------------- публичный API

def probe_url(url: str) -> dict:
    """Метаданные видео без скачивания: {title, duration, uploader, thumbnail_url, source_id, is_live, height}."""
    video_id = _require_video_id(url)
    options = _base_options()
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise RuntimeError(classify_error(str(exc))) from exc
    if not isinstance(info, dict):
        raise RuntimeError("Не удалось прочитать видео: YouTube вернул пустой ответ.")
    _reject_playlist(info)
    height = int(info.get("height") or 0)
    if not height:
        heights = [int(item.get("height") or 0) for item in (info.get("formats") or [])
                   if isinstance(item, dict)]
        height = max(heights, default=0)
    return {
        "title": str(info.get("title") or ""),
        "duration": float(info.get("duration") or 0.0),
        "uploader": str(info.get("uploader") or info.get("channel") or ""),
        "thumbnail_url": _thumbnail_url(info),
        "source_id": str(info.get("id") or video_id),
        "is_live": bool(info.get("is_live")),
        "height": height,
    }


def download(
    url: str,
    outdir: Path,
    *,
    max_height: int = 1080,
    on_progress: Callable[[dict], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> DownloadResult:
    """Скачать лучшее mp4 <= max_height в outdir под именем <source_id>_<slug>.mp4."""
    video_id = _require_video_id(url)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if _is_cancelled(cancel):
        raise DownloadCancelled("Скачивание отменено.")

    # Сначала узнаём title: имя файла с slug строится из него, а не из сырого заголовка.
    info = _extract_info(url, _base_options(), cancel=cancel)
    _reject_playlist(info)
    source_id = str(info.get("id") or video_id)
    title = str(info.get("title") or "video")
    duration = float(info.get("duration") or 0.0)
    uploader = str(info.get("uploader") or info.get("channel") or "")
    outbase = f"{source_id}_{slugify(title)}"

    options = _base_options()
    options.update({
        "skip_download": False,
        "format": format_selector(max_height),
        "merge_output_format": "mp4",
        "outtmpl": {"default": str(outdir / f"{outbase}.%(ext)s")},
        "noplaylist": True,
        "progress_hooks": [_make_progress_hook(on_progress, cancel)],
        # превью пригодится в UI; если YouTube его не отдаст — просто не будет файла
        "writethumbnail": True,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        # mp4 обязателен для рендера; remux не перекодирует, только меняет контейнер
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
    })

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            final_info = ydl.extract_info(url, download=True)
    except Exception as exc:
        _cleanup(outdir, outbase)
        if _is_cancelled(cancel) or _CANCEL_MARK in str(exc):
            raise DownloadCancelled("Скачивание отменено.") from None
        raise RuntimeError(classify_error(str(exc))) from exc

    if _is_cancelled(cancel):
        _cleanup(outdir, outbase)
        raise DownloadCancelled("Скачивание отменено.")
    if isinstance(final_info, dict):
        _reject_playlist(final_info)
        title = str(final_info.get("title") or title)
        duration = float(final_info.get("duration") or duration)
        uploader = str(final_info.get("uploader") or final_info.get("channel") or uploader)
        source_id = str(final_info.get("id") or source_id)

    video_path = _locate_video(outdir, outbase)
    if video_path is None:
        _cleanup(outdir, outbase)
        raise RuntimeError("Скачивание завершилось, но файл видео не найден — повтори попытку.")
    # merge_output_format гарантирует mp4; если контейнер всё же другой — имя даём по факту расширения
    target = outdir / f"{outbase}.mp4"
    if video_path.suffix.lower() == ".mp4" and video_path != target:
        video_path = video_path.replace(target)
    return DownloadResult(
        path=video_path,
        title=title,
        duration=duration,
        uploader=uploader,
        thumbnail=_locate_thumbnail(outdir, outbase),
        source_id=source_id,
    )


def slugify(title: str, max_len: int = 60) -> str:
    """Заголовок -> имя файла: только a-z0-9-, нижний регистр."""
    text = str(title or "").lower()
    text = "".join(_TRANSLIT.get(char, char) for char in text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    text = text[:max_len].strip("-")
    return text or "video"


def format_selector(max_height: int = 1080) -> str:
    """Формат yt-dlp: лучший mp4 с аудио не выше max_height, иначе лучший доступный."""
    try:
        height = int(max_height)
    except (TypeError, ValueError):
        height = 1080
    height = max(144, min(4320, height))
    return (
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"best[height<={height}][ext=mp4]/best"
    )


def classify_error(message: str) -> str:
    """Текст ошибки yt-dlp -> понятная русская причина."""
    raw = " ".join(str(message or "").split())
    lowered = raw.lower()
    for needles, human in _ERROR_PATTERNS:
        if any(needle in lowered for needle in needles):
            return human
    if not raw:
        return "Не удалось скачать видео: YouTube вернул пустой ответ."
    cleaned = raw.removeprefix("ERROR: ").strip()
    return f"Не удалось скачать видео: {cleaned[:300]}"


# ---------------------------------------------------------------- разбор ссылки

def _require_video_id(url: str) -> str:
    text = (url or "").strip()
    if not text:
        raise RuntimeError("Пустая ссылка — вставь адрес видео с YouTube.")
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("Не похоже на ссылку YouTube: нужен http(s)-адрес.")
    host = (parsed.hostname or "").lower()
    if host not in _YT_HOSTS:
        raise RuntimeError("Не похоже на ссылку YouTube: ожидаю youtube.com или youtu.be.")
    video_id = _video_id(parsed)
    if not video_id:
        raise RuntimeError("В ссылке нет id видео — нужна ссылка на конкретный ролик, а не на канал.")
    return video_id


def _video_id(parsed) -> str:
    path = parsed.path or ""
    if parsed.hostname and parsed.hostname.lower().endswith("youtu.be"):
        candidate = path.strip("/").split("/")[0]
        return candidate if _is_video_id(candidate) else ""
    query = parse_qs(parsed.query)
    from_query = (query.get("v") or [""])[0].strip()
    if _is_video_id(from_query):
        return from_query
    match = re.match(r"^/(?:shorts|embed|live|v|e|watch)/([A-Za-z0-9_-]{6,})", path)
    if match and _is_video_id(match.group(1)):
        return match.group(1)
    return ""


def _is_video_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{6,}", value or ""))


def _thumbnail_url(info: dict) -> str:
    thumb = info.get("thumbnail")
    if isinstance(thumb, str) and thumb:
        return thumb
    thumbnails = info.get("thumbnails") or []
    for entry in reversed(thumbnails):
        if isinstance(entry, dict) and entry.get("url"):
            return str(entry["url"])
    return ""


def _reject_playlist(info: dict) -> None:
    if info.get("_type") in {"playlist", "multi_video"}:
        raise RuntimeError("Ссылка ведёт на плейлист или канал — нужна ссылка на конкретное видео.")


# ---------------------------------------------------------------- yt-dlp обвязка

def _base_options() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "noplaylist": True,
        # плейлисты не разворачиваем, но одиночное видео читаем полностью (extract_flat-режим)
        "extract_flat": "in_playlist",
        "socket_timeout": 30,
        "writesubtitles": False,
        "writeautomaticsub": False,
    }


def _extract_info(url: str, options: dict, *, cancel: Callable[[], bool] | None = None) -> dict:
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        if _is_cancelled(cancel) or _CANCEL_MARK in str(exc):
            raise DownloadCancelled("Скачивание отменено.") from None
        raise RuntimeError(classify_error(str(exc))) from exc
    if _is_cancelled(cancel):
        raise DownloadCancelled("Скачивание отменено.")
    if not isinstance(info, dict):
        raise RuntimeError("Не удалось прочитать видео: YouTube вернул пустой ответ.")
    return info


def _make_progress_hook(
    on_progress: Callable[[dict], None] | None,
    cancel: Callable[[], bool] | None,
) -> Callable[[dict], None]:
    def hook(data: Any) -> None:
        # Отмену проверяем на каждом тике: скачивание может идти минутами.
        if _is_cancelled(cancel):
            raise _Cancelled(_CANCEL_MARK)
        if on_progress is None or not isinstance(data, dict):
            return
        status = data.get("status")
        if status not in {"downloading", "finished"}:
            return
        total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
        downloaded = int(data.get("downloaded_bytes") or 0)
        if status == "finished":
            percent = 100.0
        elif total > 0:
            percent = downloaded / total * 100.0
        else:
            percent = 0.0
        on_progress({
            "percent": round(max(0.0, min(100.0, percent)), 1),
            "downloaded": downloaded,
            "total": total,
            "speed": _format_speed(data.get("speed")),
            "eta": _format_eta(data.get("eta")),
        })

    return hook


def _is_cancelled(cancel: Callable[[], bool] | None) -> bool:
    if cancel is None:
        return False
    try:
        return bool(cancel())
    except Exception:
        return False


# ---------------------------------------------------------------- файлы

def _locate_video(outdir: Path, outbase: str) -> Path | None:
    candidates: list[Path] = []
    for path in outdir.glob(f"{outbase}.*"):
        if not path.is_file() or path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        candidates.append(path)
    if not candidates:
        return None
    # самый большой файл — это видео, превью/сабы уже отфильтрованы
    candidates.sort(key=lambda item: (item.stat().st_size, item.stat().st_mtime), reverse=True)
    return candidates[0]


def _locate_thumbnail(outdir: Path, outbase: str) -> Path | None:
    found: list[Path] = []
    for path in outdir.glob(f"{outbase}.*"):
        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES:
            found.append(path)
    if not found:
        return None
    # jpg предпочтительнее webp: его проще показать в webview
    found.sort(key=lambda item: (item.suffix.lower() != ".jpg", item.name))
    return found[0]


def _cleanup(outdir: Path, outbase: str) -> None:
    """Не оставляем обрывки (.part и т.п.) на диске — иначе рендер возьмёт битый файл."""
    for path in outdir.glob(f"{outbase}.*"):
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            continue


# ---------------------------------------------------------------- форматирование

def _format_speed(speed: Any) -> str:
    try:
        value = float(speed)
    except (TypeError, ValueError):
        return "—"
    if value <= 0:
        return "—"
    return f"{_human_size(value)}/с"


def _human_size(value: float) -> str:
    size = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def _format_eta(eta: Any) -> str:
    try:
        seconds = int(float(eta))
    except (TypeError, ValueError):
        return "—"
    if seconds < 0:
        return "—"
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
