# -*- coding: utf-8 -*-
"""Кандидаты селекторов для площадок загрузки.

Площадки не дают API для автопостинга и меняют разметку чаще, чем выходят релизы,
поэтому у каждого элемента интерфейса список кандидатов: browsers.wait_for_any
перебирает их по порядку и берёт первый живой. Порядок в списках — от самого
устойчивого к самому хрупкому: data-e2e / data-testid, затем id, затем текст,
затем структурные фолбэки.

Если площадка обновила интерфейс — правь только этот файл, адаптеры трогать не
нужно. Новый кандидат добавляй в конец списка, а не заменяй старые: часть
пользователей сидит на старых версиях страниц (A/B-тесты площадок).

Проверено только теоретически: живых аккаунтов TikTok / YouTube / VK у модуля
нет, поэтому все списки собраны по известной разметке веб-версий.
"""

from __future__ import annotations

# ---------------------------------------------------------------- TikTok ---- #
# TikTok Studio — единственный стабильный путь веб-загрузки; старый /upload
# переехал, поэтому открываем именно студию.
TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"

# Поле файла скрыто (display:none), искать надо без проверки видимости.
# Если TikTok переедет на новый загрузчик — первым падает accept*="video",
# тогда добавить сюда data-e2e нового инпута из DevTools.
TIKTOK_FILE_INPUT = [
    'input[type="file"][accept*="video"]',  # форма принимает только видео — точнее всего
    'input[type="file"]',                   # запасной: accept убрали или поменяли
    '[data-e2e="upload-file-input"]',
]

# Описание — Draft.js: правильная работа только через contenteditable,
# поэтому fill() ему не доверяем и печатаем посимвольно.
# Если поле пропало — искать в DevTools новый data-e2e у редактора описания.
TIKTOK_CAPTION = [
    'div[data-e2e="caption-input"]',
    '.public-DraftEditor-content[contenteditable="true"]',
    'div[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"]',
]

# Кнопка публикации заблокирована, пока видео обрабатывается, — ждём именно
# включённую (без disabled), иначе клик уйдёт в никуда.
# Если кнопка поменяла текст или data-e2e — правь этот список первым.
TIKTOK_PUBLISH = [
    '[data-e2e="post_video_button"]:not([disabled])',
    '[data-e2e="post_video_button"]:not([aria-disabled="true"])',
    'button:has-text("Опубликовать"):not([disabled])',
    'button:has-text("Post"):not([disabled])',
    'button:has-text("Publish"):not([disabled])',
]

# Признаки разлогина: у гостя в студии нет кнопки публикации, зато есть вход.
TIKTOK_LOGIN_MARKERS = [
    '[data-e2e="login-button"]',
    'button:has-text("Войти")',
    'button:has-text("Log in")',
    'a[href*="/login"]',
]

# Подтверждение публикации. Стабильного data-e2e на диалог успеха нет,
# поэтому сначала структурные/id-варианты, потом локализованный текст.
# Если площадка сменила язык интерфейса — добавь свой вариант в список.
TIKTOK_PUBLISHED = [
    '[data-e2e="post-publish"]',
    '[data-e2e="upload-status"]',
    'text=Твой ролик опубликован',
    'text=Ваше видео опубликовано',
    'text=Your video has been posted',
    'text=Video published',
]

# Необязательные поп-апы после публикации: закрываем, но их отсутствие не ошибка.
TIKTOK_DISMISS = [
    '[data-e2e="post-publish-close"]',
    'button:has-text("Понятно")',
    'button:has-text("Закрыть")',
    'button:has-text("OK")',
    'button:has-text("Got it")',
]

# ---------------------------------------------------------------- YouTube --- #
# Studio сам выбирает русский/английский по аккаунту, поэтому текстовые
# фолбэки ниже дают оба языка. Других стабильных точек входа в студию нет.
YOUTUBE_STUDIO_URL = "https://studio.youtube.com"

# Кнопка «Создать». Раньше всех ломается локализованный aria-label,
# поэтому id кастомного элемента ytcp-button стоит первым.
YOUTUBE_CREATE_BUTTON = [
    'ytcp-button#create-icon',
    '#create-icon',
    'ytcp-button[aria-label*="Создать"]',
]

# Пункт меню «Загрузить видео». test-id — внутренний атрибут YouTube,
# но живёт дольше текста и локализации. Со сменой меню править здесь.
YOUTUBE_UPLOAD_MENU_ITEM = [
    'tp-yt-paper-item[test-id="upload-beta"]',
    '#text-item-0',
    'tp-yt-paper-item:has-text("Загрузить видео")',
    'tp-yt-paper-item:has-text("Upload videos")',
]

# Файловый инпут появляется после «Создать -> Загрузить видео» и всегда скрыт.
YOUTUBE_FILE_INPUT = [
    'input[type="file"]',
    'ytcp-uploads-file-picker input[type="file"]',
    '#select-files-button input',
]

# Название: поле внутри ytcp-social-suggestions-textbox, id держится годами.
YOUTUBE_TITLE = [
    '#title-textarea #textbox',
    'ytcp-social-suggestions-textbox#title-textarea #textbox',
    'div#textbox[contenteditable="true"]',
]

# Описание: та же конструкция, свой id. При смене формы искать по тексту лейбла.
YOUTUBE_DESCRIPTION = [
    '#description-textarea #textbox',
    'ytcp-social-suggestions-textbox#description-textarea #textbox',
    '#description-textarea div[contenteditable="true"]',
]

# «Далее» между шагами мастера: ждём только включённую кнопку.
YOUTUBE_NEXT = [
    'ytcp-button#next-button:not([disabled])',
    '#next-button:not([disabled])',
]

# Видимость выбирается radio-кнопками по name, а не по локализованному тексту.
YOUTUBE_VISIBILITY = {
    "public": [
        'tp-yt-paper-radio-button[name="PUBLIC"]',
        'ytcp-radio-button[name="PUBLIC"]',
    ],
    "unlisted": [
        'tp-yt-paper-radio-button[name="UNLISTED"]',
        'ytcp-radio-button[name="UNLISTED"]',
    ],
    "private": [
        'tp-yt-paper-radio-button[name="PRIVATE"]',
        'ytcp-radio-button[name="PRIVATE"]',
    ],
}

# Финальная публикация. Кнопка есть всегда, но включена только после проверок.
YOUTUBE_DONE = [
    'ytcp-button#done-button:not([disabled])',
    '#done-button:not([disabled])',
]

# Окно с готовым видео и ссылкой. #share-url — поле со ссылкой на ролик.
YOUTUBE_PUBLISHED = [
    'ytcp-video-share-dialog #share-url',
    '#share-url',
    'ytcp-video-share-dialog',
]

# Закрытие окна успеха; необязательный шаг.
YOUTUBE_CLOSE_DIALOGS = [
    'ytcp-video-share-dialog #close-button',
    '#close-button',
    'ytcp-button#close-button',
]

# Признаки того, что сессия не авторизована.
YOUTUBE_LOGIN_MARKERS = [
    'a[href*="accounts.google.com"]',
    'ytcp-button#sign-in',
    'text=Войти',
]

# --------------------------------------------------------------------- VK --- #
# VK Клипы создаются с этой страницы; при переезде в vk.com/clips/new?act=create
# достаточно поменять URL здесь, флоу тот же.
VK_CLIPS_URL = "https://vk.com/clips/new"

# Файловый инпут VK скрыт и появляется после клика по кнопке загрузки,
# поэтому адаптер сначала смотрит input, потом жмёт кнопку из VK_SELECT_BUTTON.
VK_FILE_INPUT = [
    'input[type="file"][accept*="video"]',
    'input[type="file"]',
    '[data-testid="clips-upload-input"]',
]

# Кнопка, открывающая диалог загрузки, если файлового инпута в DOM ещё нет.
# Нужна как запасной путь: VK иногда подгружает инпут только по клику.
VK_SELECT_BUTTON = [
    '[data-testid="clips-upload-button"]',
    'button:has-text("Загрузить клип")',
    'button:has-text("Загрузить видео")',
]

# Описание клипа: VK переиспользует contenteditable-редактор, поэтому
# текстовый фолбэк через textarea держим последним (старый интерфейс).
VK_CAPTION = [
    '[data-testid="clips-caption"] div[contenteditable="true"]',
    'div[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"]',
    'textarea',
]

# Публикация: сначала testid, потом локализованный текст.
VK_PUBLISH = [
    '[data-testid="clips-publish"]',
    'button:has-text("Опубликовать клип")',
    'button:has-text("Опубликовать")',
]

# Подтверждение после публикации.
VK_PUBLISHED = [
    '[data-testid="clips-published"]',
    'text=Клип опубликован',
    'text=Ваш клип опубликован',
    'text=Клип отправлен',
]

# Необязательные поп-апы (подсказки, «Понятно»), снимаем после успеха.
VK_DISMISS = [
    '[data-testid="clips-close-popup"]',
    'button:has-text("Понятно")',
    'button:has-text("Закрыть")',
]

# Разлогин: либо форма входа в iframe (#index_login), либо ссылка на login.vk.com.
VK_LOGIN_MARKERS = [
    '#index_login',
    'a[href*="login.vk.com"]',
    'button:has-text("Войти")',
]
