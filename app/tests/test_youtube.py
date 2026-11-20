# -*- coding: utf-8 -*-
"""Тесты youtube.py: полностью офлайн — yt_dlp подменяется фейковым YoutubeDL,
DoH-транспорт — заглушкой. Ни одного сетевого запроса.
"""

from __future__ import annotations

import inspect
import socket
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
import yt_dlp as real_yt_dlp

from app.core import youtube

VIDEO_URL = "https://www.youtube.com/watch?v=abc123XYZ"
RUTUBE_ID = "3eac3b4561676c17df9132a9a1e62e3e"
RUTUBE_URL = f"https://rutube.ru/video/{RUTUBE_ID}/"
VK_ID = "-18403220_456239696"
VK_URL = f"https://vk.com/video{VK_ID}"

INFO = {
    "id": "abc123XYZ",
    "title": "Test Video: 2024 год!",
    "duration": 125.0,
    "uploader": "Some Channel",
    "thumbnail": "https://i.ytimg.com/vi/abc123XYZ/hqdefault.jpg",
    "is_live": False,
    "formats": [{"height": 720}, {"height": 1080}],
}

RUTUBE_INFO = {
    "id": RUTUBE_ID,
    "title": "Раненный кенгуру забежал в аптеку",
    "duration": 81.0,
    "uploader": "NTDRussian",
    "thumbnail": "https://pic.rtbcdn.ru/video/1.jpg",
    "is_live": False,
    "formats": [{"height": 368}],
}

VK_INFO = {
    "id": VK_ID,
    "title": "Тест VK",
    "duration": 60.0,
    "uploader": "Автор",
    "thumbnail": "https://iv.okcdn.ru/preview.jpg",
    "is_live": False,
    "formats": [{"height": 720}],
}


class FakeYDL:
    """Подмена yt_dlp.YoutubeDL: отдаёт заданный info и эмулирует скачивание файла."""

    info: dict = dict(INFO)
    error: Exception | None = None
    on_download_start = None
    instances: list = []
    calls: list = []

    def __init__(self, options: dict) -> None:
        self.options = options
        FakeYDL.instances.append(self)

    def __enter__(self) -> "FakeYDL":
        return self

    def __exit__(self, *args) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> dict:
        FakeYDL.calls.append({"url": url, "download": download})
        if FakeYDL.error is not None:
            raise FakeYDL.error
        info = dict(FakeYDL.info)
        if not download:
            return info

        if FakeYDL.on_download_start is not None:
            FakeYDL.on_download_start()
        template = self.options.get("outtmpl") or ""
        if isinstance(template, dict):
            template = template.get("default", "")
        if template:
            video_path = Path(template.replace("%(ext)s", "mp4"))
            video_path.parent.mkdir(parents=True, exist_ok=True)
            video_path.write_bytes(b"video" * 512)
            if self.options.get("writethumbnail"):
                video_path.with_suffix(".webp").write_bytes(b"thumb")
        for hook in self.options.get("progress_hooks") or []:
            hook({
                "status": "downloading",
                "downloaded_bytes": 512,
                "total_bytes": 1024,
                "speed": 2 * 1024 * 1024,
                "eta": 75,
            })
            hook({"status": "finished", "downloaded_bytes": 1024, "total_bytes": 1024})
        return info


@pytest.fixture
def fake_ytdlp(monkeypatch):
    FakeYDL.info = dict(INFO)
    FakeYDL.error = None
    FakeYDL.on_download_start = None
    FakeYDL.instances = []
    FakeYDL.calls = []
    monkeypatch.setattr(youtube, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    return FakeYDL


# ---------------------------------------------------------------- публичный контракт

def test_public_api_signatures_unchanged():
    probe = inspect.signature(youtube.probe_url)
    assert list(probe.parameters) == ["url"]

    download = inspect.signature(youtube.download)
    assert list(download.parameters) == ["url", "outdir", "max_height", "on_progress", "cancel"]
    assert download.parameters["url"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert download.parameters["outdir"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("max_height", "on_progress", "cancel"):
        assert download.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert download.parameters["max_height"].default == 1080
    assert download.parameters["on_progress"].default is None
    assert download.parameters["cancel"].default is None
    assert download.return_annotation == "DownloadResult"


def test_download_result_contract_fields_preserved():
    fields = list(youtube.DownloadResult.__dataclass_fields__)
    # Позиционные поля контракта идут первыми и в том же порядке, новые — только в хвосте.
    assert fields[:6] == ["path", "title", "duration", "uploader", "thumbnail", "source_id"]
    assert youtube.DownloadResult.__dataclass_fields__["thumbnail"].default is None
    assert youtube.DownloadResult.__dataclass_fields__["source_id"].default == ""
    assert youtube.DownloadResult.__dataclass_fields__["source"].default == ""
    assert fields.index("source") > fields.index("source_id")


# ---------------------------------------------------------------- slug и формат

def test_slugify():
    assert youtube.slugify("Test Video: 2024!!!") == "test-video-2024"
    assert youtube.slugify("  Hello --- World  ") == "hello-world"
    assert youtube.slugify("Привет, мир!") == "privet-mir"
    assert youtube.slugify("") == "video"
    assert youtube.slugify("!!!???") == "video"
    assert len(youtube.slugify("a" * 200)) == 60


def test_format_selector():
    assert youtube.format_selector(1080) == (
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
        "best[height<=1080][ext=mp4]/best"
    )
    assert "height<=720" in youtube.format_selector(720)
    # защита от мусора в настройках
    assert "height<=144" in youtube.format_selector(0)
    assert "height<=4320" in youtube.format_selector(99999)


# ---------------------------------------------------------------- площадки

@pytest.mark.parametrize("url, expected", [
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "youtube"),
    ("https://youtu.be/jNQXAC9IVRw", "youtube"),
    ("https://m.youtube.com/shorts/jNQXAC9IVRw", "youtube"),
    ("https://music.youtube.com/watch?v=jNQXAC9IVRw", "youtube"),
    (RUTUBE_URL, "rutube"),
    ("https://rutube.ru/live/video/3eac3b4561676c17df9132a9a1e62e3e/", "rutube"),
    (VK_URL, "vk"),
    (f"https://vkvideo.ru/video{VK_ID}", "vk"),
    (f"https://m.vk.com/video{VK_ID}", "vk"),
    ("https://vimeo.com/123456", ""),
    ("", ""),
    ("просто текст", ""),
])
def test_detect_source(url, expected):
    assert youtube.detect_source(url) == expected


@pytest.mark.parametrize("url, source, video_id, expected_url", [
    # YouTube: любые формы -> канонический watch-URL без &list и t=
    ("https://youtu.be/abc123XYZ?t=10", "youtube", "abc123XYZ",
     "https://www.youtube.com/watch?v=abc123XYZ"),
    ("https://www.youtube.com/watch?v=abc123XYZ&list=PL1", "youtube", "abc123XYZ",
     "https://www.youtube.com/watch?v=abc123XYZ"),
    ("https://m.youtube.com/shorts/abc123XYZ", "youtube", "abc123XYZ",
     "https://www.youtube.com/watch?v=abc123XYZ"),
    # Rutube: канонические ссылки на обычный и прямой эфир
    (RUTUBE_URL, "rutube", RUTUBE_ID, RUTUBE_URL),
    (f"https://rutube.ru/live/video/{RUTUBE_ID}/", "rutube", RUTUBE_ID,
     f"https://rutube.ru/live/video/{RUTUBE_ID}/"),
    # VK: обычная ссылка, vkvideo, m.vk, embed с hash и ссылка-обёртка с z=
    (VK_URL, "vk", VK_ID, VK_URL),
    (f"https://vkvideo.ru/video{VK_ID}", "vk", VK_ID, f"https://vkvideo.ru/video{VK_ID}"),
    (f"https://m.vk.com/video{VK_ID}", "vk", VK_ID, f"https://m.vk.com/video{VK_ID}"),
    ("https://vk.com/video_ext.php?oid=-77521&id=162222515&hash=87b046504ccd8bfa", "vk",
     "-77521_162222515",
     "https://vk.com/video_ext.php?oid=-77521&id=162222515&hash=87b046504ccd8bfa"),
    ("https://vk.com/videos-77521?z=video-77521_162222515%2Fclub77521", "vk",
     "-77521_162222515", "https://vk.com/video-77521_162222515"),
    ("https://vk.com/clip205387401_456239017", "vk", "205387401_456239017",
     "https://vk.com/clip205387401_456239017"),
])
def test_parse_url_variants(url, source, video_id, expected_url):
    ref = youtube.parse_url(url)
    assert ref.source == source
    assert ref.video_id == video_id
    assert ref.url == expected_url


def test_parse_url_rutube_private_keeps_token(monkeypatch):
    ref = youtube.parse_url(f"https://rutube.ru/video/private/{RUTUBE_ID}/?p=secret_token")
    assert ref.video_id == RUTUBE_ID
    assert ref.url == f"https://rutube.ru/video/private/{RUTUBE_ID}/?p=secret_token"


@pytest.mark.parametrize("url, fragment", [
    ("", "Пустая ссылка"),
    ("ftp://youtube.com/watch?v=abc123XYZ", "http(s)"),
    ("https://vimeo.com/123456", "Не похоже на ссылку"),
    ("просто текст", "Не похоже на ссылку"),
    ("https://www.youtube.com/@somechannel", "id видео"),
    ("https://www.youtube.com/playlist?list=PL123456", "id видео"),
    ("https://rutube.ru/video/person/313878/", "id видео"),
    ("https://rutube.ru/", "id видео"),
    ("https://rutube.ru/video/private/" + RUTUBE_ID + "/", "параметра p"),
    ("https://vk.com/video/@mobidevices", "id видео"),
    ("https://vk.com/video_ext.php?oid=abc&id=12", "id видео"),
])
def test_parse_url_errors(url, fragment):
    with pytest.raises(RuntimeError) as error:
        youtube.parse_url(url)
    assert fragment in str(error.value)


def test_require_video_id_compat():
    for url in (
        "https://youtu.be/abc123XYZ",
        "youtu.be/abc123XYZ?t=10",
        "https://www.youtube.com/watch?v=abc123XYZ&list=PL1",
        "https://m.youtube.com/shorts/abc123XYZ",
        "https://www.youtube.com/live/abc123XYZ",
        "https://www.youtube.com/embed/abc123XYZ",
        RUTUBE_URL,
        VK_URL,
    ):
        assert youtube._require_video_id(url) in {"abc123XYZ", RUTUBE_ID, VK_ID}


# ---------------------------------------------------------------- DoH

def test_parse_doh_answers_json_string():
    payload = (
        '{"Status":0,"Answer":[{"name":"www.youtube.com.","type":5,"data":"x.cdn.net."},'
        '{"name":"www.youtube.com.","type":1,"data":"142.251.155.4"},'
        '{"name":"www.youtube.com.","type":1,"data":"142.251.150.4"}]}'
    )
    assert youtube.parse_doh_answers(payload) == ["142.251.155.4", "142.251.150.4"]


def test_parse_doh_answers_dict_and_junk():
    assert youtube.parse_doh_answers({"Answer": [{"type": 1, "data": "142.251.155.4"},
                                                 {"type": 1, "data": "142.251.155.4"}]}) == [
        "142.251.155.4"]
    assert youtube.parse_doh_answers({"Answer": [{"type": 1, "data": "не-ip"}]}) == []
    assert youtube.parse_doh_answers({"Answer": [{"type": 28, "data": "2a00:1450::1"}]}) == []
    assert youtube.parse_doh_answers({}) == []
    assert youtube.parse_doh_answers({"Answer": "мусор"}) == []
    assert youtube.parse_doh_answers("не json") == []
    assert youtube.parse_doh_answers(None) == []


def test_doh_query_urls_order():
    urls = youtube._doh_query_urls("www.youtube.com")
    assert len(urls) == 2
    # Cloudflare по IP-литералу идёт первым: ему DNS не нужен вообще.
    assert urls[0].startswith("https://1.1.1.1/dns-query")
    assert "name=www.youtube.com&type=A" in urls[0]
    assert urls[1].startswith("https://dns.google/resolve")


def test_doh_resolve_falls_back_to_second_endpoint(monkeypatch):
    seen = []

    def fake_read(url):
        seen.append(url)
        if "1.1.1.1" in url:
            raise OSError("cloudflare недоступен")
        return {"Answer": [{"type": 1, "data": "142.250.178.78"}]}

    monkeypatch.setattr(youtube, "_read_doh_json", fake_read)
    assert youtube._doh_resolve("youtube.com") == ["142.250.178.78"]
    assert len(seen) == 2


def test_doh_resolve_returns_empty_when_nothing_works(monkeypatch):
    calls = []
    monkeypatch.setattr(youtube, "_read_doh_json",
                        lambda url: calls.append(url) or {"Answer": []})
    assert youtube._doh_resolve("www.youtube.com") == []
    assert len(calls) == 2


def _fake_getaddrinfo(real, blocked=("www.youtube.com",)):
    def resolver(host, port, *args, **kwargs):
        if host in blocked:
            raise socket.gaierror(-11001, "getaddrinfo failed")
        return real(host, port, *args, **kwargs)
    return resolver


def test_doh_fallback_resolves_after_system_dns_failure(monkeypatch):
    real = socket.getaddrinfo
    fake_original = _fake_getaddrinfo(real)
    monkeypatch.setattr(socket, "getaddrinfo", fake_original)

    doh_calls = []

    def fake_read(url):
        doh_calls.append(url)
        return {"Answer": [{"type": 1, "data": "142.251.155.4"},
                           {"type": 1, "data": "142.251.150.4"}]}

    monkeypatch.setattr(youtube, "_read_doh_json", fake_read)

    with youtube.doh_fallback() as resolver:
        assert socket.getaddrinfo is not fake_original
        entries = socket.getaddrinfo("www.youtube.com", 443, socket.AF_INET, socket.SOCK_STREAM)
        assert [entry[4] for entry in entries] == [
            ("142.251.155.4", 443), ("142.251.150.4", 443)]
        assert entries[0][:3] == (socket.AF_INET, socket.SOCK_STREAM, 0)
        assert resolver.doh_hosts == ["www.youtube.com"]
        # Повторный запрос того же хоста идёт из кэша вызова, а не в DoH.
        socket.getaddrinfo("www.youtube.com", 443)
        assert len(doh_calls) == 1

    assert socket.getaddrinfo is fake_original


def test_doh_fallback_uses_system_dns_first(monkeypatch):
    real = socket.getaddrinfo
    fake_original = _fake_getaddrinfo(real, blocked=())
    monkeypatch.setattr(socket, "getaddrinfo", fake_original)

    def boom(url):
        raise AssertionError("DoH не должен вызываться, пока работает системный DNS")

    monkeypatch.setattr(youtube, "_read_doh_json", boom)
    with youtube.doh_fallback():
        entries = socket.getaddrinfo("rutube.ru", 443, socket.AF_INET, socket.SOCK_STREAM)
    assert entries


def test_doh_fallback_restores_resolver_on_exception(monkeypatch):
    real = socket.getaddrinfo
    fake_original = _fake_getaddrinfo(real)
    monkeypatch.setattr(socket, "getaddrinfo", fake_original)

    with pytest.raises(ValueError):
        with youtube.doh_fallback():
            assert socket.getaddrinfo is not fake_original
            raise ValueError("бум")
    assert socket.getaddrinfo is fake_original


def test_doh_fallback_reraises_gaierror_when_doh_empty(monkeypatch):
    real = socket.getaddrinfo
    fake_original = _fake_getaddrinfo(real)
    monkeypatch.setattr(socket, "getaddrinfo", fake_original)
    monkeypatch.setattr(youtube, "_read_doh_json", lambda url: {"Answer": []})

    with youtube.doh_fallback():
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("www.youtube.com", 443)


def test_doh_fallback_does_not_fake_ipv6(monkeypatch):
    real = socket.getaddrinfo
    fake_original = _fake_getaddrinfo(real)
    monkeypatch.setattr(socket, "getaddrinfo", fake_original)
    monkeypatch.setattr(youtube, "_read_doh_json",
                        lambda url: {"Answer": [{"type": 1, "data": "142.251.155.4"}]})

    with youtube.doh_fallback():
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("www.youtube.com", 443, socket.AF_INET6)


# ---------------------------------------------------------------- классификация ошибок

@pytest.mark.parametrize("message, expected, source", [
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "приватное", "youtube"),
    ("ERROR: Sign in to confirm your age. This video may be inappropriate for some users.", "возрастн", "youtube"),
    ("ERROR: The uploader has not made this video available in your country", "регион", "youtube"),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader", "удалено", "youtube"),
    ("ERROR: [youtube] abc: This live stream will begin in 3 hours", "прямой эфир", "youtube"),
    ("ERROR: Unable to download webpage: HTTP Error 429: Too Many Requests", "Слишком много запросов", "youtube"),
    ("ERROR: ffprobe not found. Please install ffmpeg", "ffmpeg", ""),
    ("ERROR: [youtube] abc: This video is members-only", "участникам", "youtube"),
    ("ERROR: [youtube] abc: The uploader has blocked this video in your country", "регион", "youtube"),
    # Rutube / VK
    ("ERROR: [rutube] 3eac: Unable to download video JSON: HTTP Error 403: Forbidden", "403", "rutube"),
    ("ERROR: [rutube] 3eac: Unable to download options JSON: HTTP Error 404: Not Found", "удалено", "rutube"),
    ("ERROR: [vk] Access restricted", "закрыто", "vk"),
    ("ERROR: [vk] This video is unavailable because its creator has been blocked", "закрыто", "vk"),
    ("ERROR: Unsupported URL: https://example.com/x", "Не похоже на ссылку", ""),
])
def test_classify_error(message, expected, source):
    assert expected in youtube.classify_error(message, source=source)


def test_classify_error_unknown_and_empty():
    message = youtube.classify_error("ERROR: something completely weird")
    assert "Не удалось скачать видео" in message
    assert "something completely weird" in message
    assert "Не удалось скачать видео" in youtube.classify_error("")


def test_classify_dns_gives_youtube_specific_hint():
    text = "ERROR: Failed to resolve 'www.youtube.com' ([Errno 11001] getaddrinfo failed)"
    youtube_message = youtube.classify_error(text, source="youtube")
    assert "DNS" in youtube_message
    assert "VPN" in youtube_message and "DoH" in youtube_message
    # Для Rutube/VK тот же текст — без «яндекс-специфичных» советов.
    plain = youtube.classify_error(text)
    assert "DNS" in plain
    assert "VPN" not in plain


# ---------------------------------------------------------------- probe_url

def test_probe_url_returns_contract_dict(fake_ytdlp):
    info = youtube.probe_url(VIDEO_URL)
    assert info == {
        "title": "Test Video: 2024 год!",
        "duration": 125.0,
        "uploader": "Some Channel",
        "thumbnail_url": "https://i.ytimg.com/vi/abc123XYZ/hqdefault.jpg",
        "source_id": "abc123XYZ",
        "is_live": False,
        "height": 1080,
        "source": "youtube",
    }
    options = fake_ytdlp.instances[0].options
    assert options["skip_download"] is True
    assert options["noplaylist"] is True
    assert options["extract_flat"] == "in_playlist"


def test_probe_url_uses_canonical_url(fake_ytdlp):
    youtube.probe_url("https://youtu.be/abc123XYZ?t=30")
    assert fake_ytdlp.calls[0] == {
        "url": "https://www.youtube.com/watch?v=abc123XYZ",
        "download": False,
    }


def test_probe_url_platforms(fake_ytdlp):
    fake_ytdlp.info = dict(RUTUBE_INFO)
    info = youtube.probe_url(RUTUBE_URL)
    assert info["source"] == "rutube"
    assert fake_ytdlp.calls[-1]["url"] == RUTUBE_URL

    fake_ytdlp.info = dict(VK_INFO)
    info = youtube.probe_url(f"https://vkvideo.ru/video{VK_ID}")
    assert info["source"] == "vk"
    assert info["source_id"] == VK_ID


def test_probe_url_classifies_download_error(fake_ytdlp):
    fake_ytdlp.error = real_yt_dlp.utils.DownloadError(
        "ERROR: [youtube] abc123XYZ: Private video. Sign in if you've been granted access"
    )
    with pytest.raises(RuntimeError) as error:
        youtube.probe_url(VIDEO_URL)
    assert "приватное" in str(error.value)


def test_probe_url_rejects_playlist_info(fake_ytdlp):
    fake_ytdlp.info = {"_type": "playlist", "id": "PL1"}
    with pytest.raises(RuntimeError) as error:
        youtube.probe_url(VIDEO_URL)
    assert "плейлист" in str(error.value)


def test_probe_and_download_wrap_doh(monkeypatch, fake_ytdlp, tmp_path):
    entered = []

    def spy():
        entered.append(True)
        return nullcontext()

    monkeypatch.setattr(youtube, "doh_fallback", spy)
    youtube.probe_url(VIDEO_URL)
    assert len(entered) == 1
    youtube.download(VIDEO_URL, tmp_path)
    # DoH-контекст один на весь download, а не по одному на каждый вызов yt-dlp.
    assert len(entered) == 2


# ---------------------------------------------------------------- download

def test_download_creates_expected_file_name(fake_ytdlp, tmp_path):
    progress: list[dict] = []
    result = youtube.download(VIDEO_URL, tmp_path, max_height=1080, on_progress=progress.append)

    assert isinstance(result, youtube.DownloadResult)
    assert result.path == tmp_path / "abc123XYZ_test-video-2024-god.mp4"
    assert result.path.is_file()
    assert result.title == "Test Video: 2024 год!"
    assert result.duration == pytest.approx(125.0)
    assert result.uploader == "Some Channel"
    assert result.source_id == "abc123XYZ"
    assert result.source == "youtube"
    assert result.thumbnail is not None and result.thumbnail.suffix == ".webp"

    assert progress[0] == {
        "percent": 50.0,
        "downloaded": 512,
        "total": 1024,
        "speed": "2.0 МБ/с",
        "eta": "01:15",
    }
    assert progress[-1]["percent"] == 100.0


def test_download_speed_options(fake_ytdlp, tmp_path):
    youtube.download(VIDEO_URL, tmp_path)
    options = fake_ytdlp.instances[-1].options
    assert options["skip_download"] is False
    assert options["format"] == youtube.format_selector(1080)
    assert options["merge_output_format"] == "mp4"
    assert options["noplaylist"] is True
    assert options["overwrites"] is True
    # Ускорение: параллельные фрагменты и повторы на обрывах
    assert options["concurrent_fragment_downloads"] == 8
    assert options["retries"] == 5
    assert options["fragment_retries"] == 5


def test_download_writes_source_for_rutube_and_vk(fake_ytdlp, tmp_path):
    fake_ytdlp.info = dict(RUTUBE_INFO)
    result = youtube.download(RUTUBE_URL, tmp_path / "rutube")
    assert result.source == "rutube"
    assert result.path.name == f"{RUTUBE_ID}_ranennyy-kenguru-zabezhal-v-apteku.mp4"
    assert result.path.is_file()
    assert fake_ytdlp.calls[0]["url"] == RUTUBE_URL

    fake_ytdlp.calls = []
    fake_ytdlp.info = dict(VK_INFO)
    result = youtube.download(VK_URL, tmp_path / "vk")
    assert result.source == "vk"
    assert result.path.name == f"{VK_ID}_test-vk.mp4"
    assert fake_ytdlp.calls[0]["url"] == VK_URL


def test_download_cancel_cleans_partial_files(fake_ytdlp, tmp_path):
    state = {"cancelled": False}
    fake_ytdlp.on_download_start = lambda: state.update(cancelled=True)

    with pytest.raises(youtube.DownloadCancelled) as error:
        youtube.download(VIDEO_URL, tmp_path, cancel=lambda: state["cancelled"])
    assert "отменено" in str(error.value).lower()
    assert list(tmp_path.iterdir()) == []


def test_download_cancelled_before_start(fake_ytdlp, tmp_path):
    with pytest.raises(youtube.DownloadCancelled):
        youtube.download(VIDEO_URL, tmp_path, cancel=lambda: True)
    assert fake_ytdlp.instances == []
    assert list(tmp_path.iterdir()) == []


def test_download_classifies_error(fake_ytdlp, tmp_path):
    fake_ytdlp.error = real_yt_dlp.utils.DownloadError(
        "ERROR: [youtube] abc123XYZ: Video unavailable. This video has been removed by the uploader"
    )
    with pytest.raises(RuntimeError) as error:
        youtube.download(VIDEO_URL, tmp_path)
    assert "удалено" in str(error.value)


def test_download_rejects_bad_url(fake_ytdlp, tmp_path):
    with pytest.raises(RuntimeError) as error:
        youtube.download("https://example.com/video", tmp_path)
    assert "Не похоже на ссылку видео" in str(error.value)
    assert fake_ytdlp.instances == []


def test_download_playlist_rejected(fake_ytdlp, tmp_path):
    fake_ytdlp.info = {"_type": "playlist", "id": "PL1"}
    with pytest.raises(RuntimeError) as error:
        youtube.download(VIDEO_URL, tmp_path)
    assert "плейлист" in str(error.value)


def test_download_rejects_live_stream(fake_ytdlp, tmp_path):
    fake_ytdlp.info = dict(INFO)
    fake_ytdlp.info["is_live"] = True
    with pytest.raises(RuntimeError) as error:
        youtube.download(VIDEO_URL, tmp_path)
    assert "прямой эфир" in str(error.value)


def test_download_without_title_uses_fallback_slug(fake_ytdlp, tmp_path):
    fake_ytdlp.info = {"id": "abc123XYZ", "title": "!!!", "duration": 30}
    result = youtube.download("https://youtu.be/abc123XYZ", tmp_path)
    assert result.path.name == "abc123XYZ_video.mp4"
