# -*- coding: utf-8 -*-
"""Загрузка клипа на YouTube (Shorts) через YouTube Studio."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from app.core.upload import BaseUploader, UploadFlowError, compose_description, selectors, truncate
from app.core.upload.base import UploadRequest, UploadResult

# Лимиты Studio: длиннее — YouTube обрежет молча или откажет.
YOUTUBE_TITLE_LIMIT = 100
YOUTUBE_DESCRIPTION_LIMIT = 5000

# Настройка видимости: в контракте UploadRequest поля нет, поэтому берём env
# (явный override) или app/data/settings.json -> upload.visibility, куда её пишет
# сервер. unlisted по умолчанию — безопасный вариант для автопубликации:
# ролик не вылетает в ленту подписчикам, пока его не проверили глазами.
YOUTUBE_VISIBILITY_ENV = "CUTTER_YOUTUBE_VISIBILITY"
YOUTUBE_DEFAULT_VISIBILITY = "unlisted"


def _visibility_from_settings() -> str:
    """Видимость из настроек приложения; ошибку чтения не считаем фатальной."""
    try:
        from app.core import settings as core_settings

        raw = (core_settings.load().get("upload") or {}).get("visibility") or ""
    except Exception:
        return ""
    return str(raw).strip().lower()


class YouTubeUploader(BaseUploader):
    platform = "youtube"
    # Обработка длинного видео в Studio идёт минутами, дефолтных 45 с мало.
    timeout_upload = 900_000

    def _upload(self, page, req: UploadRequest, log: Callable[[str], None]) -> UploadResult:
        log("YouTube: открываю Studio")
        page.goto(selectors.YOUTUBE_STUDIO_URL, wait_until="domcontentloaded")
        if "accounts.google.com" in str(getattr(page, "url", "")):
            raise UploadFlowError(
                "YouTube Studio выкинул на страницу входа Google: "
                "войди в аккаунт в открытом окне браузера и повтори загрузку"
            )
        self._check_login(
            page,
            selectors.YOUTUBE_LOGIN_MARKERS,
            "YouTube Studio показывает кнопку входа: войди в Google в открытом окне браузера и повтори",
        )

        # Инпут может уже быть в DOM (Studio держит его для drag&drop),
        # иначе открываем диалог через «Создать -> Загрузить видео».
        file_input = self._wait(page, selectors.YOUTUBE_FILE_INPUT, timeout=self.timeout_probe, visible=False)
        if file_input is None:
            create = self._require(
                self._wait(page, selectors.YOUTUBE_CREATE_BUTTON),
                "не нашёл кнопку «Создать» в YouTube Studio: проверь, что открыт канал, "
                "а не пустой аккаунт без прав на публикацию",
            )
            self._click(create, "Создать")
            item = self._require(
                self._wait(page, selectors.YOUTUBE_UPLOAD_MENU_ITEM),
                "меню «Создать» открылось, но пункта «Загрузить видео» нет — обнови селекторы меню",
            )
            self._click(item, "Загрузить видео")
            file_input = self._require(
                self._wait(page, selectors.YOUTUBE_FILE_INPUT, visible=False),
                "не появился диалог загрузки YouTube (нет поля файла)",
            )
        self._attach_file(file_input, req)
        log("YouTube: видео отдано, жду форму деталей")

        title_box = self._require(
            self._wait(page, selectors.YOUTUBE_TITLE),
            "не появилась форма с названием видео — диалог загрузки не открылся",
        )
        title = truncate((req.title or "").strip() or Path(req.video_path).stem, YOUTUBE_TITLE_LIMIT)
        self._fill(title_box, title, "название видео")
        log("YouTube: название заполнено")

        description_box = self._require(
            self._wait(page, selectors.YOUTUBE_DESCRIPTION),
            "не появилось поле описания видео — форма деталей не догрузилась",
        )
        description = truncate(compose_description(req), YOUTUBE_DESCRIPTION_LIMIT)
        self._fill(description_box, description, "описание видео")

        # Мастер: Детали -> Элементы -> Проверки -> Доступ. Три «Далее».
        for label in ("Детали", "Элементы", "Проверки"):
            next_button = self._wait(page, selectors.YOUTUBE_NEXT)
            if next_button is None:
                log(f"YouTube: шаг «{label}» уже пройден, перехожу к видимости")
                break
            self._click(next_button, f"Далее на шаге «{label}»")
            page.wait_for_timeout(self.poll_interval)

        visibility = self._visibility()
        radio = self._require(
            self._wait(page, selectors.YOUTUBE_VISIBILITY[visibility]),
            f"не нашёл выбор видимости «{visibility}» — вкладка «Доступ» не открылась",
        )
        self._click(radio, f"видимость {visibility}")
        log(f"YouTube: видимость {visibility}")
        page.wait_for_timeout(self.poll_interval * 2)

        if req.dry_run:
            return self._dry_run(log)

        done = self._require(
            self._wait(page, selectors.YOUTUBE_DONE),
            "кнопка «Опубликовать» недоступна: Studio ещё проверяет видео или не хватает прав",
        )
        self._click(done, "Опубликовать")
        log("YouTube: нажал публикацию, жду ссылку")
        published = self._wait(page, selectors.YOUTUBE_PUBLISHED, timeout=self.timeout_upload)
        if published is None:
            raise UploadFlowError(
                "YouTube не показал окно с готовым видео. Проверь канал: "
                "загрузка могла остаться в статусе «Обработка»"
            )
        url = self._extract_url(published)
        self._dismiss(page, selectors.YOUTUBE_CLOSE_DIALOGS)
        return UploadResult(
            ok=True,
            platform=self.platform,
            url=url,
            message="видео опубликовано на YouTube Shorts",
        )

    @staticmethod
    def _visibility() -> str:
        value = os.environ.get(YOUTUBE_VISIBILITY_ENV, "").strip().lower() or _visibility_from_settings()
        return value if value in selectors.YOUTUBE_VISIBILITY else YOUTUBE_DEFAULT_VISIBILITY

    @staticmethod
    def _extract_url(locator) -> str:
        """Пытается достать ссылку на ролик из окна успеха; нет ссылки — пустая строка."""
        for attribute in ("value", "href"):
            try:
                candidate = locator.get_attribute(attribute) or ""
            except Exception:
                continue
            if candidate.startswith("http"):
                return candidate
        return ""
