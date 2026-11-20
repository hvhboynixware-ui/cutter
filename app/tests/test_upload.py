# -*- coding: utf-8 -*-
"""Офлайн-тесты загрузчика: сеть и Playwright не трогаем, страница подменена заглушкой."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.upload import (
    PLATFORMS,
    SHOTS_DIR,
    TikTokUploader,
    VKUploader,
    YouTubeUploader,
    compose_description,
    get_uploader,
    normalize_hashtags,
)
from app.core.upload import selectors
from app.core.upload import youtube as youtube_module
from app.core.upload.base import UploadRequest


# ------------------------------------------------------------------ заглушки -- #
class FakeLocator:
    """Повторяет только те методы Playwright, которые реально зовут адаптеры."""

    def __init__(self, page: "FakePage", selector: str) -> None:
        self._page = page
        self.selector = selector

    @property
    def first(self) -> "FakeLocator":
        return self

    def is_visible(self) -> bool:
        return self.selector in self._page.visible

    def count(self) -> int:
        return 1 if self.selector in self._page.present else 0

    def click(self, **kwargs) -> None:
        self._page.actions.append(("click", self.selector))
        # Клик может открыть диалог, которого до этого не было в DOM.
        for shown in self._page.after_click.get(self.selector, set()):
            self._page.show(shown)

    def fill(self, text: str, **kwargs) -> None:
        if self.selector in self._page.readonly:
            raise RuntimeError("element is not editable")
        self._page.values[self.selector] = text
        self._page.actions.append(("fill", self.selector, text))

    def press_sequentially(self, text: str, delay: int = 0, **kwargs) -> None:
        self._page.values[self.selector] = text
        self._page.actions.append(("type", self.selector, text))

    def press(self, key: str, **kwargs) -> None:
        self._page.actions.append(("press", self.selector, key))

    def set_input_files(self, files, **kwargs) -> None:
        self._page.uploads.append((self.selector, files))
        self._page.actions.append(("upload", self.selector, files))

    def get_attribute(self, name: str):
        return self._page.attributes.get((self.selector, name))

    def inner_text(self, **kwargs) -> str:
        return self._page.texts.get(self.selector, "")


class FakePage:
    def __init__(self, *, present: set[str] | None = None, url: str = "") -> None:
        self.present = set(present or ())
        self.visible = set(self.present)
        self.readonly: set[str] = set()
        self.texts: dict[str, str] = {}
        self.values: dict[str, str] = {}
        self.attributes: dict[tuple[str, str], str] = {}
        self.actions: list[tuple] = []
        self.uploads: list[tuple[str, str]] = []
        self.screenshots: list[str] = []
        self.after_click: dict[str, set[str]] = {}
        self.url = url
        self.goto_error: Exception | None = None

    def show(self, selector: str) -> "FakePage":
        """Добавляет элемент в DOM и делает видимым."""
        self.present.add(selector)
        self.visible.add(selector)
        return self

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def goto(self, url: str, **kwargs) -> None:
        if self.goto_error is not None:
            raise self.goto_error
        self.url = url
        self.actions.append(("goto", url))

    def wait_for_timeout(self, milliseconds: int) -> None:
        pass

    def screenshot(self, path: str = "", full_page: bool = False) -> None:
        self.screenshots.append(path)

    def clicks(self) -> list[str]:
        return [action[1] for action in self.actions if action[0] == "click"]


class FakeSession:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.context = None
        self.engine = "plain"
        self.profile_id = "test-profile"


def speed_up(uploader):
    """Сжимает ожидания: тесты не должны сидеть на реальных таймаутах."""
    uploader.timeout_ui = 40
    uploader.timeout_upload = 60
    uploader.timeout_login = 15
    uploader.timeout_probe = 15
    uploader.poll_interval = 0
    return uploader


@pytest.fixture()
def video(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return path


def make_request(video: Path, **overrides) -> UploadRequest:
    data = {
        "video_path": video,
        "title": "Тестовый клип",
        "description": "Проверка загрузки",
        "hashtags": ["shorts", "#shorts", "тест"],
        "platform": "tiktok",
        "account_label": "test",
    }
    data.update(overrides)
    return UploadRequest(**data)


def hashtag_occurrences(text: str) -> list[str]:
    return [match.casefold() for match in re.findall(r"#(\w+)", text, re.UNICODE)]


# -------------------------------------------------------------------- реестр -- #
def test_registry_exposes_all_platforms() -> None:
    assert set(PLATFORMS) == {"tiktok", "youtube", "vk"}
    for platform, entry in PLATFORMS.items():
        assert entry["label"], f"у {platform} нет человекочитаемого названия"
        uploader = get_uploader(platform)
        assert uploader.platform == platform
        assert callable(uploader.upload)


def test_get_uploader_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError) as error:
        get_uploader("instagram")
    text = str(error.value)
    assert "instagram" in text
    assert "tiktok" in text and "youtube" in text and "vk" in text


# ------------------------------------------------------- описание и хэштеги --- #
def test_normalize_hashtags_strips_and_dedupes() -> None:
    assert normalize_hashtags(["#shorts", "shorts", "  ", "#Топ", "топ"]) == ["shorts", "Топ"]


def test_description_gets_each_hashtag_once(video: Path) -> None:
    req = make_request(video, description="Смотри до конца #shorts", hashtags=["#shorts", "shorts", "Топ"])
    text = compose_description(req)
    assert text.count("#shorts") == 1
    assert hashtag_occurrences(text).count("топ") == 1
    assert text.startswith("Смотри до конца")


def test_description_keeps_title_once(video: Path) -> None:
    req = make_request(video, title="Мой клип", description="Мой клип\nосновной текст", hashtags=[])
    text = compose_description(req, include_title=True)
    assert text.count("Мой клип") == 1
    assert "основной текст" in text


def test_description_without_hashtags_is_stable(video: Path) -> None:
    req = make_request(video, hashtags=[])
    assert compose_description(req) == "Проверка загрузки"


# -------------------------------------------------------------------- TikTok -- #
def test_tiktok_dry_run_stops_before_publish(video: Path) -> None:
    page = FakePage(present={
        selectors.TIKTOK_FILE_INPUT[0],
        selectors.TIKTOK_CAPTION[0],
        selectors.TIKTOK_PUBLISH[0],
    })
    uploader = speed_up(TikTokUploader())

    result = uploader.upload(FakeSession(page), make_request(video, dry_run=True), lambda text: None)

    assert result.ok is True
    assert result.message == "dry-run: готово к публикации"
    assert page.uploads, "файл клипа должен быть отдан площадке"
    assert selectors.TIKTOK_PUBLISH[0] not in page.clicks(), "dry-run не имеет права нажимать публикацию"


def test_tiktok_caption_contains_each_hashtag_once(video: Path) -> None:
    page = FakePage(present={
        selectors.TIKTOK_FILE_INPUT[0],
        selectors.TIKTOK_CAPTION[0],
        selectors.TIKTOK_PUBLISH[0],
    })
    uploader = speed_up(TikTokUploader())

    uploader.upload(FakeSession(page), make_request(video, dry_run=True), lambda text: None)

    caption = page.values[selectors.TIKTOK_CAPTION[0]]
    assert caption.count("#shorts") == 1
    assert hashtag_occurrences(caption).count("тест") == 1
    assert "Тестовый клип" in caption


def test_tiktok_missing_caption_returns_error_with_screenshot(video: Path) -> None:
    page = FakePage(present={selectors.TIKTOK_FILE_INPUT[0]})
    uploader = speed_up(TikTokUploader())

    result = uploader.upload(FakeSession(page), make_request(video, dry_run=True), lambda text: None)

    assert result.ok is False
    assert "описан" in result.message.lower()
    assert result.screenshot is not None
    assert Path(result.screenshot).parent == SHOTS_DIR
    assert page.screenshots, "при ошибке должен сниматься скриншот"


def test_tiktok_publishes_when_not_dry_run(video: Path) -> None:
    page = FakePage(present={
        selectors.TIKTOK_FILE_INPUT[0],
        selectors.TIKTOK_CAPTION[0],
        selectors.TIKTOK_PUBLISH[0],
        selectors.TIKTOK_PUBLISHED[0],
        selectors.TIKTOK_DISMISS[0],
    })
    uploader = speed_up(TikTokUploader())

    result = uploader.upload(FakeSession(page), make_request(video), lambda text: None)

    assert result.ok is True
    assert selectors.TIKTOK_PUBLISH[0] in page.clicks()
    assert "TikTok" in result.message


def test_tiktok_login_wall_returns_human_message(video: Path) -> None:
    page = FakePage(present={selectors.TIKTOK_LOGIN_MARKERS[0]})
    uploader = speed_up(TikTokUploader())

    result = uploader.upload(FakeSession(page), make_request(video, dry_run=True), lambda text: None)

    assert result.ok is False
    assert "вход" in result.message.lower()
    assert not page.uploads, "без входа файл никуда не уходит"


# ------------------------------------------------------------------- YouTube -- #
def youtube_page() -> FakePage:
    return FakePage(present={
        selectors.YOUTUBE_FILE_INPUT[0],
        selectors.YOUTUBE_TITLE[0],
        selectors.YOUTUBE_DESCRIPTION[0],
        selectors.YOUTUBE_NEXT[0],
        selectors.YOUTUBE_VISIBILITY["unlisted"][0],
        selectors.YOUTUBE_DONE[0],
    })


def test_youtube_opens_upload_dialog_from_create_menu(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTTER_YOUTUBE_VISIBILITY", "unlisted")
    page = FakePage(present={
        selectors.YOUTUBE_CREATE_BUTTON[0],
        selectors.YOUTUBE_UPLOAD_MENU_ITEM[0],
        selectors.YOUTUBE_TITLE[0],
        selectors.YOUTUBE_DESCRIPTION[0],
        selectors.YOUTUBE_NEXT[0],
        selectors.YOUTUBE_VISIBILITY["unlisted"][0],
    })
    # Поле файла появляется только после «Загрузить видео» в меню «Создать».
    page.after_click[selectors.YOUTUBE_UPLOAD_MENU_ITEM[0]] = {selectors.YOUTUBE_FILE_INPUT[0]}
    uploader = speed_up(YouTubeUploader())

    result = uploader.upload(FakeSession(page), make_request(video, platform="youtube", dry_run=True), lambda text: None)

    assert result.ok is True
    assert selectors.YOUTUBE_CREATE_BUTTON[0] in page.clicks()
    assert selectors.YOUTUBE_UPLOAD_MENU_ITEM[0] in page.clicks()
    assert page.uploads


def test_youtube_dry_run_does_not_publish(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTTER_YOUTUBE_VISIBILITY", "unlisted")
    page = youtube_page()
    uploader = speed_up(YouTubeUploader())

    result = uploader.upload(FakeSession(page), make_request(video, platform="youtube", dry_run=True), lambda text: None)

    assert result.ok is True
    assert result.message == "dry-run: готово к публикации"
    assert selectors.YOUTUBE_VISIBILITY["unlisted"][0] in page.clicks()
    assert selectors.YOUTUBE_DONE[0] not in page.clicks(), "dry-run не имеет права нажимать публикацию"
    assert page.uploads


def test_youtube_public_visibility_comes_from_env(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTTER_YOUTUBE_VISIBILITY", "public")
    page = youtube_page()
    page.show(selectors.YOUTUBE_VISIBILITY["public"][0])
    uploader = speed_up(YouTubeUploader())

    result = uploader.upload(FakeSession(page), make_request(video, platform="youtube", dry_run=True), lambda text: None)

    assert result.ok is True
    assert selectors.YOUTUBE_VISIBILITY["public"][0] in page.clicks()


def test_youtube_visibility_falls_back_to_app_settings(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CUTTER_YOUTUBE_VISIBILITY", raising=False)
    monkeypatch.setattr(youtube_module, "_visibility_from_settings", lambda: "public")
    page = youtube_page()
    page.show(selectors.YOUTUBE_VISIBILITY["public"][0])
    uploader = speed_up(YouTubeUploader())

    result = uploader.upload(FakeSession(page), make_request(video, platform="youtube", dry_run=True), lambda text: None)

    assert result.ok is True
    assert selectors.YOUTUBE_VISIBILITY["public"][0] in page.clicks()


def test_youtube_publish_returns_url(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTTER_YOUTUBE_VISIBILITY", "unlisted")
    page = youtube_page()
    page.show(selectors.YOUTUBE_PUBLISHED[1])
    page.show(selectors.YOUTUBE_CLOSE_DIALOGS[0])
    page.attributes[(selectors.YOUTUBE_PUBLISHED[1], "href")] = "https://www.youtube.com/shorts/abc123"
    uploader = speed_up(YouTubeUploader())

    result = uploader.upload(FakeSession(page), make_request(video, platform="youtube"), lambda text: None)

    assert result.ok is True
    assert result.url == "https://www.youtube.com/shorts/abc123"
    assert selectors.YOUTUBE_DONE[0] in page.clicks()


# ------------------------------------------------------------------------ VK -- #
def test_vk_dry_run_stops_before_publish(video: Path) -> None:
    page = FakePage(present={
        selectors.VK_FILE_INPUT[0],
        selectors.VK_CAPTION[0],
        selectors.VK_PUBLISH[0],
    })
    uploader = speed_up(VKUploader())

    result = uploader.upload(FakeSession(page), make_request(video, platform="vk", dry_run=True), lambda text: None)

    assert result.ok is True
    assert result.message == "dry-run: готово к публикации"
    assert selectors.VK_PUBLISH[0] not in page.clicks()
    assert page.uploads


def test_vk_publish_success(video: Path) -> None:
    page = FakePage(present={
        selectors.VK_FILE_INPUT[0],
        selectors.VK_CAPTION[0],
        selectors.VK_PUBLISH[0],
        selectors.VK_PUBLISHED[0],
        selectors.VK_DISMISS[0],
    })
    uploader = speed_up(VKUploader())

    result = uploader.upload(FakeSession(page), make_request(video, platform="vk"), lambda text: None)

    assert result.ok is True
    assert selectors.VK_PUBLISH[0] in page.clicks()
    assert "VK" in result.message


# ------------------------------------------------- общие аварийные сценарии -- #
def test_unexpected_exception_becomes_error_result(video: Path) -> None:
    page = FakePage()
    page.goto_error = RuntimeError("net::ERR_CONNECTION_RESET")
    uploader = speed_up(TikTokUploader())

    result = uploader.upload(FakeSession(page), make_request(video, dry_run=True), lambda text: None)

    assert result.ok is False
    assert "неожиданная ошибка" in result.message
    assert result.screenshot is not None


def test_missing_video_file_does_not_touch_browser(tmp_path: Path) -> None:
    page = FakePage()
    uploader = speed_up(TikTokUploader())
    req = make_request(tmp_path / "нет-такого.mp4", dry_run=True)

    result = uploader.upload(FakeSession(page), req, lambda text: None)

    assert result.ok is False
    assert "не найден" in result.message
    assert page.actions == [], "без файла страницу открывать незачем"
