# -*- coding: utf-8 -*-
"""Загрузка клипов на площадки: реестр платформ и общие помощники.

Помощники живут в этом файле, а не в отдельном модуле, потому что менять можно
только объявленный набор файлов. Поэтому они объявлены ДО импорта адаптеров:
адаптеры берут их из пакета, и при первом `import app.core.upload` циклического
импорта не возникает.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

from app.core.upload import browsers
from app.core.upload.base import UploadRequest, UploadResult, Uploader

__all__ = [
    "PLATFORMS",
    "BaseUploader",
    "TikTokUploader",
    "UploadFlowError",
    "UploadRequest",
    "UploadResult",
    "VKUploader",
    "YouTubeUploader",
    "compose_description",
    "get_uploader",
    "normalize_hashtags",
    "truncate",
]

# Скриншоты ошибок: по требованию контракта — app/data/shots от корня запуска.
SHOTS_DIR = Path("app/data/shots")

_HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)


class UploadFlowError(RuntimeError):
    """Ожидаемая ошибка сценария: её текст показываем пользователю как есть."""


def normalize_hashtags(tags: list[str]) -> list[str]:
    """Приводит хэштеги к виду без решётки и без дублей, сохраняя порядок."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        tag = (raw or "").strip().lstrip("#").strip()
        # Пробел внутри хэштега площадка не распознает — меняем на подчёркивание.
        tag = re.sub(r"\s+", "_", tag)
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
    return result


def truncate(text: str, limit: int) -> str:
    """Обрезает текст по границе слова: обрубок хэштега площадка не простит."""
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        return cut[:space].rstrip()
    return cut


def compose_description(req: UploadRequest, *, include_title: bool = False) -> str:
    """Описание + хэштеги ровно один раз.

    LLM-моменты уже приходят с хэштегами внутри description, а пользователь может
    вписать их и в списке hashtags — поэтому дубли отсекаем по уже существующим.
    """
    parts: list[str] = []
    title = (req.title or "").strip()
    body = (req.description or "").strip()
    if include_title and title and title.casefold() not in body.casefold():
        parts.append(title)
    if body:
        parts.append(body)
    text = "\n".join(parts)

    existing = {match.casefold() for match in _HASHTAG_RE.findall(text)}
    fresh = [tag for tag in normalize_hashtags(req.hashtags) if tag.casefold() not in existing]
    if fresh:
        line = " ".join(f"#{tag}" for tag in fresh)
        text = f"{text}\n\n{line}" if text else line
    return text.strip()


class BaseUploader:
    """Каркас адаптера: проверка входа, превращение исключений в UploadResult.

    Таймауты — атрибуты экземпляра (а не константы), чтобы тесты и прод могли
    настроить ожидание под скорость конкретной площадки и профиля.
    """

    platform: str = ""

    timeout_ui = 45_000        # появление полей и кнопок
    timeout_upload = 300_000   # загрузка и обработка видео на стороне площадки
    timeout_login = 3_000      # проверка разлогина при открытии страницы
    timeout_probe = 2_000      # быстрая проба: элемент уже в DOM или надо жать кнопку
    poll_interval = 400        # пауза после кликов, которые перерисовывают форму

    def upload(self, session, req: UploadRequest, log: Callable[[str], None] = print) -> UploadResult:
        try:
            page = getattr(session, "page", None)
        except Exception:  # свойство page может бросить, если сессия уже умерла
            page = None
        if page is None:
            return UploadResult(
                ok=False,
                platform=self.platform,
                message="браузер не открылся: нет активной страницы. Закрой окно браузера и повтори загрузку",
            )
        try:
            video = Path(req.video_path)
        except Exception:
            video = Path("")
        if not video.is_file():
            return UploadResult(
                ok=False,
                platform=self.platform,
                message=f"файл клипа не найден: {video}. Пересобери клип и повтори загрузку",
            )
        try:
            return self._upload(page, req, log)
        except UploadFlowError as exc:
            return self._fail(session, str(exc), log)
        except Exception as exc:  # Playwright и площадки кидают любые исключения
            return self._fail(
                session,
                f"неожиданная ошибка при загрузке на {self.platform}: {exc}. "
                "Посмотри скриншот: обычно это разлогин или смена разметки площадки",
                log,
            )

    def _upload(self, page, req: UploadRequest, log: Callable[[str], None]) -> UploadResult:
        # Контракт: конкретный сценарий пишут наследники.
        raise NotImplementedError

    def _fail(self, session, message: str, log: Callable[[str], None]) -> UploadResult:
        log(message)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shot = browsers.screenshot(session, SHOTS_DIR / f"{self.platform}_{stamp}.png")
        return UploadResult(ok=False, platform=self.platform, message=message, screenshot=shot)

    def _dry_run(self, log: Callable[[str], None]) -> UploadResult:
        log("dry-run: всё готово, кнопку публикации не нажимаю")
        return UploadResult(ok=True, platform=self.platform, message="dry-run: готово к публикации")

    def _wait(self, page, candidates: list[str], *, timeout: int | None = None, visible: bool = True):
        return browsers.wait_for_any(
            page, candidates, timeout=self.timeout_ui if timeout is None else timeout, visible=visible
        )

    @staticmethod
    def _require(locator, message: str):
        if locator is None:
            raise UploadFlowError(message)
        return locator

    def _check_login(self, page, candidates: list[str], message: str) -> None:
        if self._wait(page, candidates, timeout=self.timeout_login) is not None:
            raise UploadFlowError(message)

    def _attach_file(self, locator, req: UploadRequest) -> None:
        try:
            locator.set_input_files(str(req.video_path))
        except Exception as exc:
            raise UploadFlowError(
                f"браузер не смог отдать файл {req.video_path}: {exc}. "
                "Проверь, что файл не открыт другим плеером и путь не в облачной папке"
            ) from exc

    def _click(self, locator, what: str) -> None:
        try:
            locator.click(timeout=self.timeout_ui)
        except Exception as exc:
            raise UploadFlowError(
                f"не удалось нажать «{what}»: {exc}. Обнови страницу в окне браузера и повтори"
            ) from exc

    def _fill(self, locator, text: str, what: str, *, prefer_typing: bool = False) -> None:
        # contenteditable (Draft.js, редакторы площадок) иногда игнорирует fill,
        # поэтому второй путь — печать поверх выделения.
        try:
            if prefer_typing:
                locator.click(timeout=self.timeout_ui)
                locator.press_sequentially(text, delay=8)
            else:
                locator.fill("", timeout=self.timeout_ui)
                locator.fill(text, timeout=self.timeout_ui)
            return
        except Exception:
            pass
        try:
            locator.click(timeout=self.timeout_ui)
            locator.press_sequentially(text, delay=8)
        except Exception as exc:
            raise UploadFlowError(
                f"поле «{what}» не принимает текст: {exc}. "
                "Скорее всего площадка показала проверку или диалог — закрой его и повтори"
            ) from exc

    def _dismiss(self, page, candidates: list[str], *, timeout: int = 1500) -> None:
        """Снимает необязательные поп-апы; их отсутствие ошибкой не считаем."""
        popup = self._wait(page, candidates, timeout=timeout)
        if popup is None:
            return
        try:
            popup.click(timeout=timeout)
        except Exception:
            pass


from app.core.upload.tiktok import TikTokUploader  # noqa: E402  — импорт после помощников, иначе цикл
from app.core.upload.youtube import YouTubeUploader  # noqa: E402
from app.core.upload.vk import VKUploader  # noqa: E402

PLATFORMS: dict[str, dict] = {
    "tiktok": {"label": "TikTok", "uploader": TikTokUploader()},
    "youtube": {"label": "YouTube Shorts", "uploader": YouTubeUploader()},
    "vk": {"label": "VK Клипы", "uploader": VKUploader()},
}


def get_uploader(platform: str) -> Uploader:
    """Адаптер по идентификатору площадки; неизвестная — ValueError со списком."""
    key = (platform or "").strip().lower()
    entry = PLATFORMS.get(key)
    if entry is None:
        known = ", ".join(sorted(PLATFORMS))
        raise ValueError(f"неизвестная площадка «{platform}». Доступны: {known}")
    return entry["uploader"]
