# -*- coding: utf-8 -*-
"""Контракт загрузки: dataclass'ы, которые используют все платформенные адаптеры."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol


@dataclass
class BrowserTarget:
    engine: str = "plain"          # plain | dolphin | adspower
    profile_id: str = ""           # id профиля антидетекта или имя локального профиля
    headless: bool = False
    proxy: str = ""                # host:port@user:pass (только для engine=plain)


@dataclass
class UploadRequest:
    video_path: Path
    title: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    platform: str = ""
    account_label: str = ""
    dry_run: bool = False


@dataclass
class UploadResult:
    ok: bool
    platform: str
    url: str = ""
    message: str = ""
    screenshot: Path | None = None


class Uploader(Protocol):
    platform: str

    def upload(self, session, req: UploadRequest, log: Callable[[str], None]) -> UploadResult:
        ...
