# -*- coding: utf-8 -*-
"""Скачивание видео с YouTube, Rutube и VK Video через yt-dlp.

Публичные сигнатуры `probe_url()` / `download()` / `DownloadResult` не менялись:
их вызывают app/server.py и app/core/pipeline.py. Модуль ничего не знает про Job
и UI: на вход ссылка и папка, на выход файл и метаданные. Сеть живёт только в
рантайме — тесты подменяют yt_dlp и DoH-транспорт.

Отдельная боль этой машины: системный DNS может отдавать NXDOMAIN на YouTube,
хотя TCP/TLS до его IP проходит. Поэтому резолв устроен так: сначала штатный
`socket.getaddrinfo`, при `gaierror` — DNS-over-HTTPS (Cloudflare по IP-литералу,
затем dns.google), а на время работы yt-dlp/requests `getaddrinfo` подменяется
так, чтобы спасённые DoH адреса продолжали работать. После вызова резолвер
восстанавливается, кэш DoH живёт только внутри одного вызова.
"""

from __future__ import annotations

import gc
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlsplit

import yt_dlp

# ---------------------------------------------------------------- площадки

# После отбрасывания этих префиксов хост сравниваем с таблицей ниже.
_HOST_PREFIXES = ("www.", "m.", "music.", "new.", "vksport.", "web.")

_HOST_SOURCES = {
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "youtube-nocookie.com": "youtube",
    "rutube.ru": "rutube",
    "vk.com": "vk",
    "vkvideo.ru": "vk",
    "vk.ru": "vk",
}

SOURCE_LABELS = {"youtube": "YouTube", "rutube": "Rutube", "vk": "VK Video"}

_RUTUBE_PATH = re.compile(r"^/(?:live/|play/)?video(?:/private)?/([0-9a-zA-Z]{32})(?:/|$)", re.IGNORECASE)
_VK_PATH = re.compile(r"^/(?:video|clip)(-?\d+)_(\d+)")
_VK_Z = re.compile(r"video(-?\d+)_(\d+)")
_VK_EMBED_PATH = re.compile(r"/video_ext\.php/?$", re.IGNORECASE)

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

# Порядок важен: «закрыто автором» должно срабатывать раньше общего «недоступно»,
# «приватное» — раньше «удалено». Первое совпадение выбирает человеческий текст.
_ERROR_PATTERNS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("access restricted", "creator has been blocked", "because its creator",
         "available only for friends", "closed for viewing", "video is restricted"),
        "Видео закрыто для просмотра: автор ограничил доступ (или ролик приватный).",
        "restricted",
    ),
    (
        ("private video", "this video is private", "видео приватное"),
        "Видео приватное — добавь его в доступ или скачай другое.",
        "private",
    ),
    (
        ("sign in to confirm your age", "confirm your age", "age-restricted", "age restricted",
         "inappropriate for some users", "age gate"),
        "Видео с возрастным ограничением: площадка требует вход в аккаунт.",
        "age",
    ),
    (
        ("not available in your country", "available in your country", "blocked it in your country",
         "geo-restricted", "geo restriction", "not available from your location",
         "blocked in your country", "blocked this video in your country",
         "available in this country", "by region"),
        "Видео заблокировано в твоём регионе (гео-блок) — нужен VPN или другое видео.",
        "geo",
    ),
    (
        ("members-only", "join this channel", "channel members"),
        "Видео доступно только участникам канала (members-only).",
        "members",
    ),
    (
        ("confirm you're not a bot", "confirm you are not a bot", "sign in to confirm",
         "cookies", "bot check"),
        "Площадка требует проверку «я не робот» — войди в аккаунт через cookies или повтори позже.",
        "bot",
    ),
    (
        ("ffmpeg", "ffprobe"),
        "Не найден ffmpeg — без него не склеить видео и звук.",
        "ffmpeg",
    ),
    (
        ("this video is unavailable", "video unavailable", "removed by the uploader",
         "has been removed", "does not exist", "video is no longer available", "not found",
         "has been deleted", "deleted by"),
        "Видео удалено или недоступно по этой ссылке.",
        "removed",
    ),
    (
        ("this live stream", "is live", "live event will begin", "premieres"),
        "Это прямой эфир — дождись окончания и скачай запись.",
        "live",
    ),
    (
        ("http error 429", "too many requests"),
        "Слишком много запросов к площадке — подожди пару минут.",
        "rate",
    ),
    (
        ("http error 403", "forbidden"),
        "Площадка отклонила запрос (403) — обнови yt-dlp или повтори позже.",
        "forbidden",
    ),
    (
        ("requested format is not available", "no video formats found"),
        "Не нашлось подходящего формата под выбранное качество.",
        "format",
    ),
    (
        # DNS проверяем до общего «сеть»: иначе подсказка про DoH не сработает.
        ("getaddrinfo failed", "failed to resolve", "temporary failure in name resolution",
         "name or service not known", "nodename nor servname", "no address associated",
         "errno 11001", "enotfound", "name resolution"),
        "Не удаётся определить адрес сайта (DNS) — проверь подключение или смени DNS.",
        "dns",
    ),
    (
        ("timed out", "timeout", "connection", "ssl", "network", "unable to download webpage",
         "http error 5", "proxy"),
        "Проблема с сетью — проверь подключение или VPN.",
        "network",
    ),
    (
        ("unsupported url", "not a valid url", "invalid url", "unable to extract",
         "no suitable info extractor"),
        "Не похоже на ссылку видео — поддерживаются YouTube, Rutube и VK Video.",
        "unsupported",
    ),
)

_YOUTUBE_DNS_HINT = (
    " Похоже, DNS провайдера не отдаёт адрес YouTube: включи VPN"
    " или поставь DNS 1.1.1.1 — скачивание уже пробовало спастись через DoH."
)


# ---------------------------------------------------------------- результат и ошибки

@dataclass
class DownloadResult:
    path: Path
    title: str
    duration: float
    uploader: str
    thumbnail: Path | None = None
    source_id: str = ""
    # Площадка, с которой скачано: youtube / rutube / vk (добавлено позже, в конце,
    # чтобы не сдвигать позиционные поля контракта).
    source: str = ""


class DownloadCancelled(RuntimeError):
    """Скачивание отменено пользователем, временные файлы подчищены."""


class _Cancelled(Exception):
    """Внутренний сигнал из прогресс-хука: yt-dlp не должен считать это сетевым сбоем."""


@dataclass(frozen=True)
class VideoRef:
    """Разобранная ссылка: площадка, id и URL, который отдаём yt-dlp."""

    source: str
    video_id: str
    url: str


# ---------------------------------------------------------------- разбор ссылки

def detect_source(url: str) -> str:
    """Площадка по ссылке: youtube / rutube / vk, либо "" для чужого адреса."""
    text = (url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text if "://" in text else f"https://{text}")
        host = (parsed.hostname or "").lower()
    except ValueError:
        return ""
    return _HOST_SOURCES.get(_base_host(host), "")


def parse_url(url: str) -> VideoRef:
    """Ссылка -> VideoRef или RuntimeError с человеческим текстом."""
    text = (url or "").strip()
    if not text:
        raise RuntimeError("Пустая ссылка — вставь адрес видео с YouTube, Rutube или VK.")
    if "://" not in text:
        text = f"https://{text}"
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise RuntimeError("Не похоже на ссылку видео: не удалось разобрать адрес.") from exc
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("Не похоже на ссылку видео: нужен http(s)-адрес.")
    host = (parsed.hostname or "").lower()
    if not host:
        raise RuntimeError("Не похоже на ссылку видео — в адресе нет домена.")
    source = _HOST_SOURCES.get(_base_host(host), "")
    if not source:
        raise RuntimeError(
            "Не похоже на ссылку видео — поддерживаются YouTube, Rutube и VK Video."
        )
    if source == "youtube":
        return _parse_youtube(parsed)
    if source == "rutube":
        return _parse_rutube(parsed)
    return _parse_vk(parsed)


def _base_host(host: str) -> str:
    clean = (host or "").lower().rstrip(".")
    for prefix in _HOST_PREFIXES:
        if clean.startswith(prefix):
            return clean[len(prefix):]
    return clean


def _parse_youtube(parsed) -> VideoRef:
    video_id = _video_id(parsed)
    if not video_id:
        raise RuntimeError(
            "В ссылке нет id видео — нужна ссылка на конкретный ролик, а не на канал."
        )
    # Канонический watch-URL: убирает &list, t= и прочий мусор, чтобы yt-dlp
    # не ушёл в плейлист и не тянул лишние страницы.
    return VideoRef("youtube", video_id, f"https://www.youtube.com/watch?v={video_id}")


def _parse_rutube(parsed) -> VideoRef:
    path = parsed.path or ""
    match = _RUTUBE_PATH.match(path)
    if not match:
        raise RuntimeError(
            "В ссылке Rutube нет id видео — нужна ссылка вида https://rutube.ru/video/<id>/."
        )
    video_id = match.group(1)
    if "/private/" in path.lower():
        # Приватная ссылка Rutube без ?p= не откроется — токен обязателен.
        token = (parse_qs(parsed.query).get("p") or [""])[0].strip()
        if not token:
            raise RuntimeError(
                "Это закрытая ссылка Rutube без параметра p — скопируй адрес из Rutube целиком."
            )
        return VideoRef(
            "rutube", video_id,
            f"https://rutube.ru/video/private/{video_id}/?p={quote(token, safe='')}",
        )
    if path.lower().startswith("/live/"):
        return VideoRef("rutube", video_id, f"https://rutube.ru/live/video/{video_id}/")
    return VideoRef("rutube", video_id, f"https://rutube.ru/video/{video_id}/")


def _parse_vk(parsed) -> VideoRef:
    path = parsed.path or ""
    query = parse_qs(parsed.query)
    if _VK_EMBED_PATH.search(path):
        oid = (query.get("oid") or [""])[0].strip()
        sid = (query.get("id") or [""])[0].strip()
        if re.fullmatch(r"-?\d+", oid) and re.fullmatch(r"\d+", sid):
            video_id = f"{oid}_{sid}"
            hash_value = (query.get("hash") or [""])[0].strip()
            if hash_value:
                url = (
                    f"https://vk.com/video_ext.php?oid={oid}&id={sid}"
                    f"&hash={quote(hash_value, safe='')}"
                )
                return VideoRef("vk", video_id, url)
            # Без hash проще открыть обычную страницу ролика: yt-dlp её умеет.
            return VideoRef("vk", video_id, f"https://vk.com/video{oid}_{sid}")
        raise RuntimeError(
            "В ссылке VK нет id видео — нужна ссылка вида https://vk.com/video-123_456."
        )
    match = _VK_PATH.match(path)
    if match:
        oid, sid = match.group(1), match.group(2)
        return VideoRef("vk", f"{oid}_{sid}", _clean_url(parsed))
    # Ссылки-обёртки вида vk.com/videos-123?z=video-123_456 — вытаскиваем ролик из z.
    z_value = (query.get("z") or [""])[0]
    z_match = _VK_Z.search(z_value)
    if z_match:
        oid, sid = z_match.group(1), z_match.group(2)
        return VideoRef("vk", f"{oid}_{sid}", f"https://vk.com/video{oid}_{sid}")
    raise RuntimeError(
        "В ссылке VK нет id видео — нужна ссылка вида https://vk.com/video-123_456"
        " (или vkvideo.ru/video-123_456)."
    )


def _clean_url(parsed) -> str:
    """URL без якоря: #t=10 не нужен, а query (list, t) VK иногда требует."""
    return parsed._replace(fragment="").geturl()


def _require_video_id(url: str) -> str:
    """Совместимость: id ролика любой поддерживаемой площадки."""
    return parse_url(url).video_id


def _video_id(parsed) -> str:
    """yt-dlp-подобный id YouTube из URL."""
    path = parsed.path or ""
    host = (parsed.hostname or "").lower()
    if host.endswith("youtu.be"):
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


# ---------------------------------------------------------------- DNS-over-HTTPS

# Cloudflare спрашиваем по IP-литералу: он не требует DNS вообще и потому работает,
# когда системный резолвер врёт. dns.google идёт вторым (ему самому нужен рабочий DNS,
# зато он независим от Cloudflare). Формат у обоих — JSON (application/dns-json).
DOH_ENDPOINTS: tuple[str, ...] = (
    "https://1.1.1.1/dns-query?name={host}&type=A",
    "https://dns.google/resolve?name={host}&type=A",
)
DOH_HEADERS = {
    "accept": "application/dns-json",
    "user-agent": "Cutter/1.0 (DoH fallback)",
}
DOH_TIMEOUT = 6.0

# Флаг «сейчас внутри DoH-запроса» — иначе подменённый getaddrinfo будет рекурсивно
# пытаться резолвить сам DoH-хост и уйдёт в бесконечность.
_LOCAL = threading.local()


def parse_doh_answers(payload: Any) -> list[str]:
    """Ответ DoH (JSON-строка или dict) -> список IPv4 из Answer[].data."""
    try:
        data = json.loads(payload) if isinstance(payload, (str, bytes, bytearray)) else payload
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    answers = data.get("Answer") or []
    if not isinstance(answers, list):
        return []
    result: list[str] = []
    for item in answers:
        if not isinstance(item, dict) or item.get("type") != 1:
            continue
        value = str(item.get("data") or "").strip()
        try:
            # Строгая проверка IPv4: inet_aton принимает и «1.2.3», это нам не нужно.
            address = ipaddress.IPv4Address(value)
        except ValueError:
            continue
        normalized = str(address)
        if normalized not in result:
            result.append(normalized)
    return result


def _doh_query_urls(host: str) -> tuple[str, ...]:
    encoded = quote(host, safe=".-_")
    return tuple(template.format(host=encoded) for template in DOH_ENDPOINTS)


def _read_doh_json(url: str) -> dict:
    """GET к DoH-серверу -> JSON-dict. Отдельная функция, чтобы тесты не ходили в сеть."""
    request = urllib.request.Request(url, headers=DOH_HEADERS)
    with urllib.request.urlopen(request, timeout=DOH_TIMEOUT) as response:
        raw = response.read()
    data = json.loads(raw.decode("utf-8", "replace"))
    return data if isinstance(data, dict) else {}


def _doh_resolve(host: str) -> list[str]:
    """host -> список IPv4 через DoH. Пустой список, если оба сервера не ответили."""
    for url in _doh_query_urls(host):
        previous = getattr(_LOCAL, "in_doh", False)
        _LOCAL.in_doh = True
        try:
            payload = _read_doh_json(url)
        except Exception:
            continue
        finally:
            _LOCAL.in_doh = previous
        addresses = parse_doh_answers(payload)
        if addresses:
            return addresses
    return []


class _DohFallback:
    """Контекст скачивания: штатный резолв, при NXDOMAIN — через DoH.

    Пока контекст активен, `socket.getaddrinfo` подменяется обёрткой: она сначала
    отдаёт запрос системному резолверу и только при `gaierror` идёт в DoH.
    Результаты DoH кэшируются на время вызова (yt-dlp спрашивает один хост много раз).
    `__exit__` гарантированно возвращает исходный резолвер — в том числе при исключении.
    """

    def __init__(self) -> None:
        self._original: Callable[..., list] | None = None
        self._cache: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        # Какие хосты реально спас DoH — пригодится для логов и тестов.
        self.doh_hosts: list[str] = []

    def __enter__(self) -> "_DohFallback":
        self._original = socket.getaddrinfo
        socket.getaddrinfo = self._resolve  # type: ignore[assignment]
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        if self._original is not None:
            socket.getaddrinfo = self._original  # type: ignore[assignment]
            self._original = None
        return False

    def _resolve(self, host: Any, port: Any, *args: Any, **kwargs: Any) -> list:
        original = self._original or socket.getaddrinfo
        # DoH-запрос сам идёт в сеть — не даём ему рекурсивно попасть в этот же патч.
        if getattr(_LOCAL, "in_doh", False):
            return original(host, port, *args, **kwargs)
        try:
            return original(host, port, *args, **kwargs)
        except socket.gaierror:
            addresses = self._lookup(str(host))
            if not addresses:
                raise
            family = kwargs.get("family", args[0] if len(args) > 0 else 0)
            socktype = kwargs.get("type", args[1] if len(args) > 1 else 0)
            proto = kwargs.get("proto", args[2] if len(args) > 2 else 0)
            # DoH вернул IPv4: если вызывающий требовал только IPv6 — не подменяем.
            if family not in (0, socket.AF_INET):
                raise
            return [
                (socket.AF_INET, socktype or socket.SOCK_STREAM, proto, "", (ip, port))
                for ip in addresses
            ]

    def _lookup(self, host: str) -> list[str]:
        if not host:
            return []
        with self._lock:
            cached = self._cache.get(host)
            if cached is None:
                cached = _doh_resolve(host)
                self._cache[host] = cached
                if cached:
                    self.doh_hosts.append(host)
            return list(cached)


def doh_fallback() -> _DohFallback:
    """Создать контекст «системный DNS, затем DoH» для одного вызова скачивания."""
    return _DohFallback()


# ---------------------------------------------------------------- публичный API

def probe_url(url: str) -> dict:
    """Метаданные видео без скачивания.

    Возвращает: {title, duration, uploader, thumbnail_url, source_id, is_live, height, source}.
    `source` — площадка: youtube / rutube / vk.
    """
    ref = parse_url(url)
    with doh_fallback():
        info = _extract_info(ref.url, _base_options(), source=ref.source)
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
        "source_id": str(info.get("id") or ref.video_id),
        "is_live": bool(info.get("is_live")),
        "height": height,
        "source": ref.source,
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
    ref = parse_url(url)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if _is_cancelled(cancel):
        raise DownloadCancelled("Скачивание отменено.")

    # DoH-контекст один на весь вызов: кэш резолва переживает оба обращения к yt-dlp.
    with doh_fallback():
        # Сначала узнаём title: имя файла с slug строится из него, а не из сырого заголовка.
        info = _extract_info(ref.url, _base_options(), cancel=cancel, source=ref.source)
        _reject_playlist(info)
        if info.get("is_live"):
            raise RuntimeError("Это прямой эфир — дождись окончания и скачай запись.")
        source_id = str(info.get("id") or ref.video_id)
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
            "overwrites": True,
            "progress_hooks": [_make_progress_hook(on_progress, cancel)],
            # превью пригодится в UI; если площадка его не отдаст — просто не будет файла
            "writethumbnail": True,
            # Скорость: тянем фрагменты DASH/HLS параллельно и переживаем обрывы.
            "concurrent_fragment_downloads": 8,
            "retries": 5,
            "fragment_retries": 5,
            # mp4 обязателен для рендера; remux не перекодирует, только меняет контейнер
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        })

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                final_info = ydl.extract_info(ref.url, download=True)
        except Exception as exc:
            # Держим только текст ошибки: traceback ссылается на открытые потоки
            # загрузчика, и пока он жив, Windows не даёт удалить .part.
            failure: str | None = str(exc) or exc.__class__.__name__
            cancelled_by_hook = _is_cancelled(cancel) or _CANCEL_MARK in failure
        else:
            failure = None
            cancelled_by_hook = False

        if failure is not None:
            _cleanup(outdir, outbase)
            if cancelled_by_hook:
                raise DownloadCancelled("Скачивание отменено.") from None
            raise RuntimeError(classify_error(failure, source=ref.source)) from None

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
        source=ref.source,
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


def classify_error(message: str, source: str = "") -> str:
    """Текст ошибки yt-dlp -> понятная русская причина.

    `source` (youtube/rutube/vk) позволяет добавить площадке свой совет:
    для YouTube при проблеме DNS подсказываем VPN/смену DNS.
    """
    raw = " ".join(str(message or "").split())
    lowered = raw.lower()
    for needles, human, category in _ERROR_PATTERNS:
        if any(needle in lowered for needle in needles):
            if category == "dns" and source == "youtube":
                return human + _YOUTUBE_DNS_HINT
            return human
    if not raw:
        return "Не удалось скачать видео: площадка вернула пустой ответ."
    cleaned = raw.removeprefix("ERROR: ").strip()
    return f"Не удалось скачать видео: {cleaned[:300]}"


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
        "socket_timeout": 25,
        "writesubtitles": False,
        "writeautomaticsub": False,
    }


def _extract_info(
    url: str,
    options: dict,
    *,
    cancel: Callable[[], bool] | None = None,
    source: str = "",
) -> dict:
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        if _is_cancelled(cancel) or _CANCEL_MARK in str(exc):
            raise DownloadCancelled("Скачивание отменено.") from None
        raise RuntimeError(classify_error(str(exc), source=source)) from exc
    if _is_cancelled(cancel):
        raise DownloadCancelled("Скачивание отменено.")
    if not isinstance(info, dict):
        raise RuntimeError("Не удалось прочитать видео: площадка вернула пустой ответ.")
    return info


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
    """Не оставляем обрывки (.part и т.п.) на диске — иначе рендер возьмёт битый файл.

    На Windows удаление не проходит, пока поток загрузки держит файл открытым:
    после отмены фрагменты доигрывают и отпускают дескриптор с задержкой. Поэтому
    несколько попыток с паузой — за это время загрузчик закрывает потоки сам.
    """
    last_exc: OSError | None = None
    for attempt in range(40):
        leftovers = [path for path in outdir.glob(f"{outbase}.*") if path.is_file()]
        if not leftovers:
            return
        for path in leftovers:
            try:
                path.unlink()
                last_exc = None
            except OSError as exc:
                last_exc = exc
        if attempt % 5 == 4:
            # Ссылки на открытые потоки yt-dlp могут держаться в циклах — подтолкнём GC.
            gc.collect()
        time.sleep(0.1)
    if last_exc is not None:
        # Не смогли удалить: папка задачи всё равно уникальна, файл не попадёт в рендер.
        return


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
