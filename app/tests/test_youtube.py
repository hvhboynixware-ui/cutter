# -*- coding: utf-8 -*-
"""Тесты youtube.py: полностью офлайн, yt_dlp подменяется фейковым YoutubeDL."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yt_dlp as real_yt_dlp

from app.core import youtube

VIDEO_URL = "https://www.youtube.com/watch?v=abc123XYZ"

INFO = {
    "id": "abc123XYZ",
    "title": "Test Video: 2024 год!",
    "duration": 125.0,
    "uploader": "Some Channel",
    "thumbnail": "https://i.ytimg.com/vi/abc123XYZ/hqdefault.jpg",
    "is_live": False,
    "formats": [{"height": 720}, {"height": 1080}],
}


class FakeYDL:
    """Подмена yt_dlp.YoutubeDL: отдаёт заданный info и эмулирует скачивание файла."""

    info: dict = dict(INFO)
    error: Exception | None = None
    on_download_start = None
    instances: list = []

    def __init__(self, options: dict) -> None:
        self.options = options
        FakeYDL.instances.append(self)

    def __enter__(self) -> "FakeYDL":
        return self

    def __exit__(self, *args) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> dict:
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
    monkeypatch.setattr(youtube, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    return FakeYDL


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


# ---------------------------------------------------------------- классификация ошибок

@pytest.mark.parametrize("message, expected", [
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "приватное"),
    ("ERROR: Sign in to confirm your age. This video may be inappropriate for some users.", "возрастн"),
    ("ERROR: The uploader has not made this video available in your country", "регион"),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader", "удалено"),
    ("ERROR: [youtube] abc: This live stream will begin in 3 hours", "прямой эфир"),
    ("ERROR: Unable to download webpage: HTTP Error 429: Too Many Requests", "Слишком много запросов"),
    ("ERROR: ffprobe not found. Please install ffmpeg", "ffmpeg"),
    ("ERROR: [youtube] abc: This video is members-only", "участникам"),
])
def test_classify_error(message, expected):
    assert expected in youtube.classify_error(message)


def test_classify_error_unknown_and_empty():
    message = youtube.classify_error("ERROR: something completely weird")
    assert "Не удалось скачать видео" in message
    assert "something completely weird" in message
    assert "Не удалось скачать видео" in youtube.classify_error("")


# ---------------------------------------------------------------- ссылки

@pytest.mark.parametrize("url, fragment", [
    ("", "Пустая ссылка"),
    ("https://vimeo.com/123456", "Не похоже на ссылку YouTube"),
    ("https://www.youtube.com/@somechannel", "id видео"),
    ("https://www.youtube.com/playlist?list=PL123456", "id видео"),
])
def test_probe_rejects_non_video_links(fake_ytdlp, url, fragment):
    with pytest.raises(RuntimeError) as error:
        youtube.probe_url(url)
    assert fragment in str(error.value)


def test_video_id_variants():
    for url in (
        "https://youtu.be/abc123XYZ",
        "youtu.be/abc123XYZ?t=10",
        "https://www.youtube.com/watch?v=abc123XYZ&list=PL1",
        "https://m.youtube.com/shorts/abc123XYZ",
        "https://www.youtube.com/live/abc123XYZ",
        "https://www.youtube.com/embed/abc123XYZ",
    ):
        assert youtube._require_video_id(url) == "abc123XYZ"


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
    }
    options = fake_ytdlp.instances[0].options
    assert options["skip_download"] is True
    assert options["noplaylist"] is True
    assert options["extract_flat"] == "in_playlist"


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
    assert result.thumbnail is not None and result.thumbnail.suffix == ".webp"

    assert progress[0] == {
        "percent": 50.0,
        "downloaded": 512,
        "total": 1024,
        "speed": "2.0 МБ/с",
        "eta": "01:15",
    }
    assert progress[-1]["percent"] == 100.0

    download_options = fake_ytdlp.instances[-1].options
    assert download_options["skip_download"] is False
    assert download_options["format"] == youtube.format_selector(1080)
    assert download_options["merge_output_format"] == "mp4"


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
    assert "Не похоже на ссылку YouTube" in str(error.value)


def test_download_playlist_rejected(fake_ytdlp, tmp_path):
    fake_ytdlp.info = {"_type": "playlist", "id": "PL1"}
    with pytest.raises(RuntimeError) as error:
        youtube.download(VIDEO_URL, tmp_path)
    assert "плейлист" in str(error.value)


def test_download_without_title_uses_fallback_slug(fake_ytdlp, tmp_path):
    fake_ytdlp.info = {"id": "abc123XYZ", "title": "!!!", "duration": 30}
    result = youtube.download("https://youtu.be/abc123XYZ", tmp_path)
    assert result.path.name == "abc123XYZ_video.mp4"
