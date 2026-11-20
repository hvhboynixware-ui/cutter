# -*- coding: utf-8 -*-
"""Загрузка клипа в VK Клипы через веб-версию VK."""

from __future__ import annotations

from typing import Callable

from app.core.upload import BaseUploader, UploadFlowError, compose_description, selectors, truncate
from app.core.upload.base import UploadRequest, UploadResult

# Лимит описания клипа во VK; длиннее площадка обрежет сама.
VK_CAPTION_LIMIT = 2000


class VKUploader(BaseUploader):
    platform = "vk"

    def _upload(self, page, req: UploadRequest, log: Callable[[str], None]) -> UploadResult:
        log("VK: открываю создание клипа")
        page.goto(selectors.VK_CLIPS_URL, wait_until="domcontentloaded")
        self._check_login(
            page,
            selectors.VK_LOGIN_MARKERS,
            "VK просит вход: войди в аккаунт в открытом окне браузера и повтори загрузку",
        )

        # Инпут скрыт и появляется не сразу; если его нет — жмём кнопку загрузки,
        # которая его и создаёт.
        file_input = self._wait(page, selectors.VK_FILE_INPUT, timeout=self.timeout_probe, visible=False)
        if file_input is None:
            trigger = self._require(
                self._wait(page, selectors.VK_SELECT_BUTTON),
                "не нашёл кнопку «Загрузить клип» — обнови VK_SELECT_BUTTON в selectors.py",
            )
            self._click(trigger, "Загрузить клип")
            file_input = self._require(
                self._wait(page, selectors.VK_FILE_INPUT, visible=False),
                "не появилось поле выбора видео: диалог загрузки клипа не открылся",
            )
        self._attach_file(file_input, req)
        log("VK: видео отдано, жду редактор")

        caption = self._require(
            self._wait(page, selectors.VK_CAPTION),
            "не появилось поле описания клипа: страница не догрузилась или у аккаунта нет доступа к клипам",
        )
        text = truncate(compose_description(req, include_title=True), VK_CAPTION_LIMIT)
        self._fill(caption, text, "описание клипа", prefer_typing=True)
        log("VK: описание заполнено")

        log("VK: жду, пока клип обработается")
        publish = self._require(
            self._wait(page, selectors.VK_PUBLISH, timeout=self.timeout_upload),
            "VK так и не включил кнопку публикации: видео не принято или обработка не закончилась",
        )
        if req.dry_run:
            return self._dry_run(log)

        self._click(publish, "Опубликовать")
        log("VK: нажал публикацию, жду подтверждение")
        if self._wait(page, selectors.VK_PUBLISHED, timeout=self.timeout_upload) is None:
            raise UploadFlowError(
                "VK не показал подтверждение публикации. Проверь профиль: "
                "клип может быть на модерации или сохранён черновиком"
            )
        self._dismiss(page, selectors.VK_DISMISS)
        return UploadResult(ok=True, platform=self.platform, message="клип опубликован в VK")
