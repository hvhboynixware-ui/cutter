# -*- coding: utf-8 -*-
"""Загрузка клипа в TikTok через веб-версию TikTok Studio."""

from __future__ import annotations

from typing import Callable

from app.core.upload import BaseUploader, UploadFlowError, compose_description, selectors, truncate
from app.core.upload.base import UploadRequest, UploadResult

# Больше TikTok обрезает сам, лучше резать осознанно и по границе слова.
TIKTOK_CAPTION_LIMIT = 2200


class TikTokUploader(BaseUploader):
    platform = "tiktok"

    def _upload(self, page, req: UploadRequest, log: Callable[[str], None]) -> UploadResult:
        log("TikTok: открываю Studio")
        page.goto(selectors.TIKTOK_UPLOAD_URL, wait_until="domcontentloaded")
        self._check_login(
            page,
            selectors.TIKTOK_LOGIN_MARKERS,
            "TikTok Studio просит вход: войди в аккаунт в открытом окне браузера и повтори загрузку",
        )

        file_input = self._require(
            self._wait(page, selectors.TIKTOK_FILE_INPUT, visible=False),
            "не нашёл поле выбора видео в TikTok Studio — похоже, разметка поменялась. "
            "Обнови TIKTOK_FILE_INPUT в app/core/upload/selectors.py",
        )
        self._attach_file(file_input, req)
        log("TikTok: видео отдано, жду редактор")

        caption = self._require(
            self._wait(page, selectors.TIKTOK_CAPTION),
            "не появилось поле описания: страница не догрузилась или у аккаунта нет доступа к загрузке",
        )
        text = truncate(compose_description(req, include_title=True), TIKTOK_CAPTION_LIMIT)
        self._fill(caption, text, "описание TikTok", prefer_typing=True)
        log("TikTok: описание заполнено")

        log("TikTok: жду, пока видео обработается")
        publish = self._require(
            self._wait(page, selectors.TIKTOK_PUBLISH, timeout=self.timeout_upload),
            "TikTok так и не включил кнопку публикации: видео слишком тяжёлое, "
            "площадка не приняла формат или загрузка не стартовала",
        )
        if req.dry_run:
            return self._dry_run(log)

        self._click(publish, "Опубликовать")
        log("TikTok: нажал публикацию, жду подтверждение")
        if self._wait(page, selectors.TIKTOK_PUBLISHED, timeout=self.timeout_upload) is None:
            raise UploadFlowError(
                "TikTok не показал подтверждение публикации. Загляни в профиль: "
                "ролик может висеть на модерации или уйти в черновики"
            )
        self._dismiss(page, selectors.TIKTOK_DISMISS)
        return UploadResult(ok=True, platform=self.platform, message="видео опубликовано в TikTok")
