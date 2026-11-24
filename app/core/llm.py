# -*- coding: utf-8 -*-
"""LLM-провайдеры: выбор вирусных моментов из расшифровки.

Модуль делает одну вещь: расшифровка -> запрос к LLM -> провалидированный list[Moment].
Ничего не рендерит и файлов не трогает.

Провайдеры: deepseek (основной), openrouter, gemini, groq, ollama (локальный)
и custom — любой свой OpenAI-совместимый сервер (/v1: LM Studio, vLLM, шлюзы).
OpenAI-совместимые идут через /chat/completions, Gemini — через свой REST.
При падении основного провайдера и включённом fallback идём по цепочке FALLBACK_ORDER.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

# Контракт: запрос к LLM живёт до 180 с — длинная расшифровка не должна обрываться раньше.
TIMEOUT = 180.0
# Порядок фолбэка: основной провайдер пробуем первым, дальше строго этот список.
# custom сюда не входит: молча стучаться на чужой/локальный сервер как фолбэк — сюрприз.
FALLBACK_ORDER = ("deepseek", "openrouter", "gemini", "groq", "ollama")

# Бюджет расшифровки: длинные видео ужимаем равномерно, чтобы не упереться в лимит токенов.
TRANSCRIPT_BUDGET = 20000
SEGMENT_MAX_CHARS = 240
MAX_OUTPUT_TOKENS = 4096
# Допуск на стык моментов: касание границ не считаем пересечением.
OVERLAP_EPS = 0.05


@dataclass
class LLMConfig:
    provider: str = "deepseek"          # deepseek | openrouter | gemini | groq | ollama | custom | chatgpt_sub | google_sub
    model: str = "deepseek-flash"
    api_key: str = ""
    base_url: str = ""                  # переопределение, иначе дефолт провайдера
    account_id: str = ""                # для chatgpt_sub / google_sub
    temperature: float = 1.1
    fallback: bool = True               # при падении основного идти по цепочке
    keys: dict = field(default_factory=dict)   # ключи по провайдерам {"openrouter": "..."}


@dataclass
class Moment:
    start: float                        # секунды
    end: float                          # секунды
    hook: str = ""
    title: str = ""
    hashtags: list[str] = field(default_factory=list)
    reason: str = ""
    score: int = 0                      # 0-100, оценка «залетит / не залетит»


class LLMError(RuntimeError):
    """Ошибка с человеческим текстом: уходит в лог задачи и в текст ошибки job."""


# Реестр провайдеров. free=True у моделей — для бейджа «бесплатно» в UI.
PROVIDERS: tuple[dict, ...] = (
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "kind": "openai",
        "base_url": "https://api.deepseek.com",
        "needs_key": True,
        "key_url": "https://platform.deepseek.com/api_keys",
        "note": "Основной провайдер: дёшево и быстро, ключ выдаётся сразу.",
        "models": (
            {"id": "deepseek-flash", "label": "DeepSeek Flash", "free": False},
            # старый id, может ещё пригодиться с прошлыми ключами
            {"id": "deepseek-chat", "label": "DeepSeek Chat", "free": False},
        ),
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "needs_key": True,
        "key_url": "https://openrouter.ai/keys",
        "note": "Агрегатор моделей, у free-моделей есть лимит запросов.",
        "models": (
            {"id": "deepseek/deepseek-chat-v3-0324:free", "label": "DeepSeek V3 (free)", "free": True},
            {"id": "meta-llama/llama-3.3-70b-instruct:free", "label": "Llama 3.3 70B (free)", "free": True},
            {"id": "qwen/qwen-2.5-72b-instruct:free", "label": "Qwen 2.5 72B (free)", "free": True},
        ),
    },
    {
        "id": "gemini",
        "label": "Google Gemini",
        "kind": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "needs_key": True,
        "key_url": "https://aistudio.google.com/app/apikey",
        "note": "Есть бесплатный лимит, ключ берётся в AI Studio.",
        "models": (
            {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash", "free": True},
            {"id": "gemini-2.0-flash-lite", "label": "Gemini 2.0 Flash Lite", "free": True},
        ),
    },
    {
        "id": "groq",
        "label": "Groq",
        "kind": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "needs_key": True,
        "key_url": "https://console.groq.com/keys",
        "note": "Очень быстрый, есть бесплатный лимит.",
        "models": (
            {"id": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B", "free": True},
            {"id": "llama-3.1-8b-instant", "label": "Llama 3.1 8B Instant", "free": True},
        ),
    },
    {
        "id": "ollama",
        "label": "Ollama (локально)",
        "kind": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "needs_key": False,
        "key_url": "",
        "note": "Локальный провайдер: ключ не нужен, но нужен запущенный ollama.",
        "models": (
            {"id": "llama3.1", "label": "Llama 3.1 (локально)", "free": True},
            {"id": "llama3.2", "label": "Llama 3.2 (локально)", "free": True},
            {"id": "qwen2.5", "label": "Qwen 2.5 (локально)", "free": True},
        ),
    },
    {
        "id": "custom",
        "label": "Свой сервер (OpenAI /v1)",
        "kind": "openai",
        # LM Studio по умолчанию: при пустом base_url в настройках бьём сюда.
        "base_url": "http://127.0.0.1:1234/v1",
        "needs_key": False,             # vLLM/LM Studio часто работают без ключа
        "key_url": "",
        "note": "Любой OpenAI-совместимый: LM Studio, Ollama, vLLM, OpenRouter-совместимые шлюзы. Укажи base_url и модель",
        "models": (),                   # пусто: имя модели пользователь вводит руками
        # Модель по умолчанию: без неё resolve/pick_moments не сможет выбрать spec["models"][0].
        "default_model": "local-model",
    },
)
_BY_ID = {item["id"]: item for item in PROVIDERS}

# Провайдеры по подписке (аккаунты без API-ключа): живут отдельно от PROVIDERS,
# чтобы не менять контракт available_providers() для существующих клиентов/тестов.
ACCOUNT_PROVIDERS: tuple[dict, ...] = (
    {
        "id": "chatgpt_sub",
        "label": "ChatGPT (подписка / аккаунт)",
        "kind": "account",
        "account_provider": "chatgpt",
        "base_url": "https://chatgpt.com",
        "needs_key": False,
        "key_url": "",
        "note": "Вход в аккаунт ChatGPT Plus/Pro (OAuth или браузер), API-ключ не нужен.",
        "models": (
            {"id": "gpt-5.1-codex-mini", "label": "GPT-5.1 Mini (подписка)", "free": False},
            {"id": "gpt-5.1", "label": "GPT-5.1 (подписка)", "free": False},
            {"id": "gpt-4o", "label": "GPT-4o (веб-сессия)", "free": False},
        ),
    },
    {
        "id": "google_sub",
        "label": "Google Gemini (аккаунт / AI Pro)",
        "kind": "account",
        "account_provider": "google",
        "base_url": "https://gemini.google.com",
        "needs_key": False,
        "key_url": "",
        "note": "Вход в Google-аккаунт через встроенный браузер (gemini.google.com), без API-ключа.",
        "models": (
            {"id": "gemini-web", "label": "Gemini Web (аккаунт Google)", "free": True},
        ),
    },
)
_ACCOUNT_BY_ID = {item["id"]: item for item in ACCOUNT_PROVIDERS}
_ALL_BY_ID = {**_BY_ID, **_ACCOUNT_BY_ID}


def _default_model(spec: dict) -> str:
    """Модель по умолчанию: у custom моделей нет, ему задаём явно default_model."""
    return spec.get("default_model") or spec["models"][0]["id"]


_SYSTEM = "Ты — редактор коротких видео. Отвечай только JSON, без пояснений и markdown."

# Пояснения к HTTP-кодам: пользователь должен понять, что именно чинить.
_STATUS_HINTS = {
    400: "провайдер отклонил запрос (400) — проверь ключ и имя модели",
    401: "ключ не подошёл (401) — проверь API-ключ",
    402: "на счёте провайдера нет денег (402) — пополни баланс",
    403: "ключ без доступа (403) — проверь права ключа и регион",
    404: "провайдер не нашёл endpoint или модель (404) — проверь base_url и model",
    413: "слишком большой запрос (413) — исходник слишком длинный",
    422: "провайдер не принял параметры запроса (422)",
    429: "лимит запросов или кончился баланс (429) — подожди или пополни счёт",
}


# ---------------------------------------------------------------- публичный API

def available_providers() -> list[dict]:
    """[{id, label, models: [{id, label, free}], needs_key, key_url, note}] — для /api/providers."""
    result: list[dict] = []
    for spec in PROVIDERS:
        result.append({
            "id": spec["id"],
            "label": spec["label"],
            "models": [dict(model) for model in spec["models"]],
            "needs_key": spec["needs_key"],
            "key_url": spec["key_url"],
            "note": spec["note"],
        })
    return result


def account_providers() -> list[dict]:
    """Провайдеры по подписке / входу в аккаунт без API-ключа."""
    return [
        {
            "id": spec["id"],
            "label": spec["label"],
            "account_provider": spec["account_provider"],
            "models": [dict(model) for model in spec["models"]],
            "needs_key": False,
            "key_url": "",
            "note": spec["note"],
        }
        for spec in ACCOUNT_PROVIDERS
    ]


def resolve(provider: str, model: str = "") -> tuple[str, str, str]:
    """provider, model, base_url по умолчанию для провайдера."""
    provider_id = (provider or "").strip().lower()
    spec = _ALL_BY_ID.get(provider_id)
    if spec is None:
        raise LLMError(
            f"Неизвестный провайдер «{provider}». Доступные: {', '.join(_ALL_BY_ID)}"
        )
    chosen = (model or "").strip() or _default_model(spec)
    return provider_id, chosen, spec["base_url"]


def ping(cfg: LLMConfig, timeout: float = 20.0) -> tuple[bool, str]:
    """(ok, сообщение). Минимальный запрос к провайдеру — проверить ключ и сеть."""
    try:
        provider_id, model, base_url = resolve(cfg.provider, cfg.model)
    except LLMError as exc:
        return False, str(exc)
    spec = _ALL_BY_ID[provider_id]
    if spec["kind"] == "account":
        from . import accounts
        acc = accounts.active_account(spec["account_provider"], cfg.account_id)
        if not acc:
            return False, f"{spec['label']}: нет подключённого аккаунта — войди в Настройках"
        return accounts.test_account(acc["id"], model=model)

    api_key = _key_for(cfg, provider_id)
    if spec["needs_key"] and not api_key:
        return False, f"{spec['label']}: не указан API-ключ. Где взять: {spec['key_url']}"
    base_url = (cfg.base_url or "").strip() or base_url
    candidate = {
        "provider": provider_id,
        "label": spec["label"],
        "kind": spec["kind"],
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "account_id": cfg.account_id,
    }
    try:
        if spec["kind"] == "gemini":
            _call_gemini(candidate, "ping", cfg, timeout=timeout, max_tokens=1)
        else:
            _call_openai(candidate, "ping", cfg, timeout=timeout, max_tokens=1)
    except LLMError as exc:
        return False, str(exc)
    except Exception as exc:  # ping не должен ронять HTTP-ручку
        return False, f"{spec['label']}: непредвиденная ошибка — {str(exc)[:200]}"
    if api_key:
        return True, f"{spec['label']} / {model}: связь есть, ключ работает."
    # custom/ollama без ключа — это норма, не пугаем словом «ключ»
    return True, f"{spec['label']} / {model}: связь есть, ключ не нужен."


def pick_moments(
    segments: list[tuple[float, float, str]],
    *,
    n: int,
    min_dur: float,
    max_dur: float,
    duration: float,
    cfg: LLMConfig,
    log: Callable[[str], None] = print,
) -> list[Moment]:
    """До n моментов, отсортированных по времени. Бросает LLMError, если все провайдеры упали."""
    total = _source_duration(segments, duration)
    n = int(n)
    if n <= 0:
        return []
    min_dur = max(0.5, float(min_dur or 0.5))
    max_dur = max(min_dur, float(max_dur or min_dur))
    if total <= 0:
        raise LLMError("Не знаю длительность видео — не могу проверить границы моментов.")
    if not segments:
        raise LLMError("Расшифровка пустая: LLM нечего анализировать.")

    chain = _candidate_chain(cfg)
    errors: list[str] = []

    for candidate in chain:
        spec = _ALL_BY_ID[candidate["provider"]]
        label = spec["label"]
        if spec["kind"] == "account":
            from . import accounts
            acc = accounts.active_account(spec["account_provider"], candidate.get("account_id") or "")
            if not acc:
                message = f"{label}: нет подключённого аккаунта — провайдер пропущен"
                log(f"LLM: {message}")
                errors.append(message)
                continue
        elif spec["needs_key"] and not candidate["api_key"]:
            message = f"{label}: нет API-ключа — провайдер пропущен"
            log(f"LLM: {message}")
            errors.append(message)
            continue

        model_label = f"{label} / {candidate['model']}"
        for attempt in range(2):
            prompt = _build_prompt(
                segments, n=n, min_dur=min_dur, max_dur=max_dur,
                duration=total, strict=attempt > 0,
            )
            try:
                text = _call_provider(candidate, prompt, cfg, log=log)
            except LLMError as exc:
                log(f"LLM: {model_label} — {exc}")
                errors.append(f"{model_label}: {exc}")
                break
            try:
                moments = _validate_response(
                    text, n=n, min_dur=min_dur, max_dur=max_dur, duration=total,
                )
            except ValueError as exc:
                log(f"LLM: {model_label} вернул невалидный ответ ({exc}), попытка {attempt + 1}/2")
                if attempt == 0:
                    continue
                errors.append(f"{model_label}: ответ модели не разобрать ({exc})")
                break
            if attempt > 0:
                log(f"LLM: {model_label} — получилось со второй попытки")
            log(f"LLM: выбрано {len(moments)} моментов через {model_label}")
            return moments

    hint = "Проверь аккаунт/API-ключ, баланс на счёте провайдера и сеть (или запусти ollama локально)."
    if not errors:
        raise LLMError(
            "Ни один LLM-провайдер не настроен: подключи аккаунт или добавь API-ключ в Настройках. " + hint
        )
    tail = "\n".join(f"- {item}" for item in errors[-5:])
    raise LLMError(f"Ни один провайдер не отдал моменты:\n{tail}\n{hint}")


# ---------------------------------------------------------------- конфиг и цепочка

def _key_for(cfg: LLMConfig, provider_id: str) -> str:
    """Ключ провайдера: сначала cfg.keys, общий api_key — только для основного провайдера."""
    keys = cfg.keys if isinstance(cfg.keys, dict) else {}
    key = str(keys.get(provider_id) or "").strip()
    if key:
        return key
    if provider_id == (cfg.provider or "").strip().lower():
        return str(cfg.api_key or "").strip()
    return ""


def _candidate_chain(cfg: LLMConfig) -> list[dict]:
    """Основной провайдер + (если fallback) остальные по FALLBACK_ORDER."""
    primary = (cfg.provider or "deepseek").strip().lower()
    if primary not in _ALL_BY_ID:
        raise LLMError(
            f"Неизвестный провайдер «{cfg.provider}». Доступные: {', '.join(_ALL_BY_ID)}"
        )
    order = (primary,)
    if cfg.fallback:
        order += tuple(provider for provider in FALLBACK_ORDER if provider != primary)

    chain: list[dict] = []
    for provider_id in order:
        spec = _ALL_BY_ID[provider_id]
        is_primary = provider_id == primary
        model = ""
        if is_primary:
            model = (cfg.model or "").strip()
        chain.append({
            "provider": provider_id,
            "label": spec["label"],
            "kind": spec["kind"],
            "model": model or _default_model(spec),
            # base_url из настроек имеет смысл только для основного провайдера
            "base_url": ((cfg.base_url or "").strip() if is_primary else "") or spec["base_url"],
            "api_key": _key_for(cfg, provider_id),
            "account_id": cfg.account_id if is_primary else "",
        })
    return chain


# ---------------------------------------------------------------- запросы к провайдерам

def _post(label: str, url: str, *, headers: dict, payload: dict, params: dict | None,
          timeout: float) -> requests.Response:
    try:
        return requests.post(
            url, headers=headers, params=params, json=payload, timeout=timeout,
        )
    except requests.Timeout as exc:
        raise LLMError(f"{label}: нет ответа за {timeout:.0f} с — проверь сеть/прокси") from exc
    except requests.RequestException as exc:
        raise LLMError(f"{label}: не удалось подключиться — проверь сеть/прокси ({exc})") from exc


def _call_provider(candidate: dict, prompt: str, cfg: LLMConfig,
                   *, timeout: float = TIMEOUT, max_tokens: int = MAX_OUTPUT_TOKENS,
                   log: Callable[[str], None] = print) -> str:
    if candidate["kind"] == "account":
        from . import accounts
        spec = _ALL_BY_ID[candidate["provider"]]
        acc = accounts.active_account(spec["account_provider"], candidate.get("account_id") or "")
        if not acc:
            raise LLMError(f"{candidate['label']}: нет подключённого аккаунта")
        try:
            return accounts.query_account(
                acc, prompt, system=_SYSTEM, model=candidate["model"], timeout=timeout, log=log,
            )
        except accounts.AccountError as exc:
            raise LLMError(str(exc)) from exc
    if candidate["kind"] == "gemini":
        return _call_gemini(candidate, prompt, cfg, timeout=timeout, max_tokens=max_tokens)
    return _call_openai(candidate, prompt, cfg, timeout=timeout, max_tokens=max_tokens)


def _call_openai(candidate: dict, prompt: str, cfg: LLMConfig,
                 *, timeout: float = TIMEOUT, max_tokens: int = MAX_OUTPUT_TOKENS) -> str:
    """OpenAI-совместимый /chat/completions (deepseek, openrouter, groq, ollama)."""
    label = candidate["label"]
    url = _endpoint_openai(candidate["base_url"])
    headers = _headers_openai(candidate["provider"], candidate["api_key"])
    payload: dict[str, Any] = {
        "model": candidate["model"],
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": max(0.0, float(cfg.temperature or 0.0)),
        "max_tokens": max_tokens,
        "stream": False,
    }
    if max_tokens > 1:  # JSON-режим не нужен для ping с одним токеном
        payload["response_format"] = {"type": "json_object"}

    response = _post(label, url, headers=headers, payload=payload, params=None, timeout=timeout)
    if response.status_code == 400 and "response_format" in payload:
        # Часть free-моделей не умеет JSON-режим — повторяем без него, промпт всё равно просит JSON.
        body = _body_text(response).lower()
        if "response_format" in body or "json_object" in body or "json mode" in body:
            payload.pop("response_format", None)
            response = _post(label, url, headers=headers, payload=payload,
                             params=None, timeout=timeout)
    if response.status_code == 404 and candidate.get("provider") == "custom":
        # Свой сервер чаще всего живёт на /v1: 404 — это почти всегда забытый суффикс.
        raise LLMError(_custom_404_hint(label, candidate.get("base_url", ""), response))
    _ensure_ok(label, response)
    data = _json_body(label, response)
    choices = data.get("choices") or []
    if not choices:
        raise LLMError(f"{label}: в ответе нет choices — модель не вернула текст")
    content = (choices[0].get("message") or {}).get("content")
    return _content_text(content)


def _call_gemini(candidate: dict, prompt: str, cfg: LLMConfig,
                 *, timeout: float = TIMEOUT, max_tokens: int = MAX_OUTPUT_TOKENS) -> str:
    """Gemini REST: ключ в ?key=, JSON-ответ через responseMimeType."""
    label = candidate["label"]
    url = _endpoint_gemini(candidate["base_url"], candidate["model"])
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": f"{_SYSTEM}\n\n{prompt}"}]}],
        "generationConfig": {
            "temperature": max(0.0, float(cfg.temperature or 0.0)),
            "responseMimeType": "application/json",
            "maxOutputTokens": max_tokens,
        },
    }
    params = {"key": candidate["api_key"]} if candidate["api_key"] else {}
    response = _post(label, url, headers={"Content-Type": "application/json"},
                     payload=payload, params=params or None, timeout=timeout)
    _ensure_ok(label, response)
    data = _json_body(label, response)
    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback") or {}
        reason = feedback.get("blockReason") or "нет кандидатов в ответе"
        raise LLMError(f"{label}: запрос отклонён — {reason}")
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    return _content_text([part.get("text") for part in parts if isinstance(part, dict)])


def _headers_openai(provider_id: str, api_key: str) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider_id == "openrouter":
        # OpenRouter просит имя приложения, иначе часть моделей отвечает медленнее
        headers["X-Title"] = "Cutter"
    return headers


def _endpoint_openai(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMError("Не задан base_url провайдера")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _endpoint_gemini(base_url: str, model: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMError("Не задан base_url провайдера")
    if ":generateContent" in base:
        return base
    return f"{base}/models/{model}:generateContent"


def _custom_404_hint(label: str, base_url: str, response: requests.Response) -> str:
    """Свой сервер: 404 на /chat/completions — почти всегда base_url без /v1."""
    message = (
        f"{label}: сервер ответил 404 — проверь, что base_url оканчивается на /v1 "
        f"(сейчас {base_url or 'пусто'})"
    )
    body = _body_text(response)
    if body:
        message += f". Ответ: {body[:300]}"
    return message


def _ensure_ok(label: str, response: requests.Response) -> None:
    if response.status_code < 400:
        return
    code = response.status_code
    if code >= 500:
        hint = f"сервер провайдера упал ({code}) — попробуй позже"
    else:
        hint = _STATUS_HINTS.get(code, "провайдер отклонил запрос — проверь ключ, баланс и сеть")
    body = _body_text(response)
    # Тело без ключа: провайдеры его не эхо-тят, но обрезаем на всякий случай.
    message = f"{label}: {hint}"
    if body:
        message += f". Ответ: {body[:300]}"
    raise LLMError(message)


def _json_body(label: str, response: requests.Response) -> dict:
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError(f"{label}: ответ не JSON — возможно, кончился баланс или сломался API") from exc
    if not isinstance(data, dict):
        raise LLMError(f"{label}: ожидал JSON-объект в ответе")
    return data


def _body_text(response: requests.Response) -> str:
    try:
        return str(response.text or "").strip()
    except Exception:
        return ""


def _content_text(content: Any) -> str:
    """Текст ответа: строка или список частей (некоторые провайдеры отдают list)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
            elif isinstance(part, str):
                chunks.append(part)
        return "".join(chunks).strip()
    return ""


# ---------------------------------------------------------------- промпт и расшифровка

def _build_prompt(segments: list[tuple[float, float, str]], *, n: int, min_dur: float,
                  max_dur: float, duration: float, strict: bool) -> str:
    transcript, sampled = _format_transcript(segments)
    lines = [
        f"Ниже расшифровка видео с таймкодами, длительность исходника {duration:.0f} с.",
        f"Выбери до {n} самодостаточных моментов под вертикальные шортсы.",
        f"Каждый момент длится от {min_dur:.0f} до {max_dur:.0f} секунд.",
        "Правила:",
        "1. Момент понятен без остального видео: есть завязка и финал мысли.",
        "2. Хук — самая яркая фраза, она должна прозвучать в первые 2 секунды.",
        "3. Моменты не пересекаются и не выходят за пределы видео.",
        "4. hook — реплика-крючок, title — заголовок до 60 символов, hashtags — 3-8 тегов без #.",
        "5. reason — одной фразой, почему момент залетит.",
        "6. score — целое 0-100: оценка шанса на виральность.",
    ]
    if strict:
        lines += [
            "ПОВТОР: предыдущий ответ был невалидным. Верни ТОЛЬКО JSON-объект,",
            'начинающийся с {"clips": ...}, без markdown, без текста до и после.',
        ]
    lines += [
        'Формат ответа: {"clips": [{"start": 12.5, "end": 34.0, "hook": "...",',
        '"title": "...", "hashtags": ["тег"], "reason": "...", "score": 87}]}',
        "start и end — секунды числом (12.5) или в формате мм:сс.",
    ]
    if sampled:
        lines.append("Расшифровка ужата: показаны равномерно распределённые фрагменты видео.")
    lines.append("")
    lines.append("Расшифровка:")
    lines.append(transcript)
    return "\n".join(lines)


def _format_transcript(segments: list[tuple[float, float, str]]) -> tuple[str, bool]:
    """Строки «[таймкод] текст». Длинный транскрипт ужимаем равномерно, а не обрезаем хвост."""
    lines: list[str] = []
    for start, end, text in segments:
        clean = " ".join(str(text or "").split())
        if not clean:
            continue
        if len(clean) > SEGMENT_MAX_CHARS:
            clean = clean[:SEGMENT_MAX_CHARS].rstrip() + "…"
        lines.append(f"[{_fmt_ts(start)}-{_fmt_ts(end)}] {clean}")
    if not lines:
        return "", False
    total = sum(len(line) + 1 for line in lines)
    if total <= TRANSCRIPT_BUDGET:
        return "\n".join(lines), False
    step = max(1, math.ceil(total / TRANSCRIPT_BUDGET))
    picked = lines[::step]
    result: list[str] = []
    used = 0
    for line in picked:
        if used + len(line) + 1 > TRANSCRIPT_BUDGET:
            break
        result.append(line)
        used += len(line) + 1
    return "\n".join(result), True


def _fmt_ts(seconds: float) -> str:
    value = max(0.0, float(seconds or 0.0))
    total = int(value)
    return f"{total // 60:02d}:{total % 60:02d}"


# ---------------------------------------------------------------- разбор ответа

def _validate_response(text: str, *, n: int, min_dur: float, max_dur: float,
                       duration: float) -> list[Moment]:
    """JSON -> моменты с валидацией границ, длительности и пересечений. ValueError — мусор."""
    data = _extract_json(text)
    clips = _extract_clips(data)
    moments: list[Moment] = []
    for item in clips:
        if not isinstance(item, dict):
            continue
        moment = _make_moment(item, min_dur=min_dur, max_dur=max_dur, duration=duration)
        if moment is not None:
            moments.append(moment)
    if not moments:
        raise ValueError("в ответе нет ни одного момента в допустимых границах")
    moments = _drop_overlaps(moments)
    if not moments:
        raise ValueError("все моменты пересекаются и были отброшены")
    if len(moments) > n:
        # Берём лучшие по оценке, но отдаём в хронологическом порядке.
        moments = sorted(moments, key=lambda m: (-m.score, m.start))[:n]
    return sorted(moments, key=lambda m: m.start)


def _extract_json(text: str) -> Any:
    """Достаём JSON: терпим ```json-обёртки, текст до/после и одиночный объект."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("модель вернула пустой ответ")
    for chunk in _fenced_chunks(raw):
        parsed = _try_json(chunk)
        if parsed is not None:
            return parsed
    sliced = _slice_json(raw)
    if sliced:
        parsed = _try_json(sliced)
        if parsed is not None:
            return parsed
    raise ValueError("в ответе модели не нашлось JSON")


def _fenced_chunks(text: str) -> list[str]:
    chunks = [match.group(1) for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)]
    chunks.append(text)
    return chunks


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _slice_json(text: str) -> str:
    """Первый сбалансированный {} или [] с учётом строк — чтобы отрезать болтовню вокруг."""
    start = -1
    for index, char in enumerate(text):
        if char in "{[":
            start = index
            break
    if start < 0:
        return ""
    stack: list[str] = []
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            continue
        if char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]":
            if not stack or stack.pop() != char:
                return ""
            if not stack:
                return text[start:index + 1]
    return ""


def _extract_clips(data: Any) -> list:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        raise ValueError("ожидал JSON-объект с полем clips")
    for key in ("clips", "moments", "items", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    if "start" in data or "end" in data:
        return [data]
    raise ValueError("в ответе нет списка clips")


def _make_moment(item: dict, *, min_dur: float, max_dur: float,
                 duration: float) -> Moment | None:
    start = _parse_time(item.get("start"))
    end = _parse_time(item.get("end"))
    if start is None or end is None:
        return None
    if end < start:
        start, end = end, start
    start = max(0.0, start)
    end = min(float(duration), end)
    if end - start <= 0.2:
        return None

    # Длинный момент режем по хвосту, короткий растягиваем — иначе ролик обрывается на полуслове.
    if end - start > max_dur:
        end = start + max_dur
    if end - start < min_dur:
        end = min(float(duration), start + min_dur)
        if end - start < min_dur:
            start = max(0.0, end - min_dur)
        if end - start < min_dur - OVERLAP_EPS:
            return None

    return Moment(
        start=round(start, 3),
        end=round(end, 3),
        hook=_short_text(item.get("hook"), 160),
        title=_short_text(item.get("title"), 120),
        hashtags=_clean_hashtags(item.get("hashtags")),
        reason=_short_text(item.get("reason"), 240),
        score=_parse_score(item.get("score")),
    )


def _drop_overlaps(moments: list[Moment]) -> list[Moment]:
    """Жадный отбор по score: при пересечении остаётся момент с большей оценкой."""
    ordered = sorted(moments, key=lambda m: (-m.score, -(m.end - m.start), m.start))
    kept: list[Moment] = []
    for moment in ordered:
        overlaps = any(
            moment.start < other.end - OVERLAP_EPS and other.start < moment.end - OVERLAP_EPS
            for other in kept
        )
        if not overlaps:
            kept.append(moment)
    return kept


def _parse_time(value: Any) -> float | None:
    """Секунды: число, «12.5», «1:05», «01:02:03», «12,5 сек»."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    parts = text.split(":")
    try:
        if len(parts) == 2 and all(re.fullmatch(r"\d+(?:\.\d+)?", part.strip()) for part in parts):
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|сек|секунд[а-я]*)?", text, re.I)
    if match:
        return float(match.group(1))
    return None


def _parse_score(value: Any) -> int:
    try:
        number = float(str(value).strip().replace(",", ".").rstrip("%"))
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number):
        return 0
    return max(0, min(100, int(round(number))))


def _short_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text[:limit].strip()


def _clean_hashtags(value: Any) -> list[str]:
    if value is None:
        return []
    chunks: list[str] = []
    raw_items = value if isinstance(value, (list, tuple)) else [value]
    for item in raw_items:
        chunks += re.split(r"[\s,]+", str(item))
    result: list[str] = []
    for chunk in chunks:
        tag = re.sub(r"[^\w]+", "", chunk.strip().lstrip("#"), flags=re.UNICODE)
        if tag and tag not in result:
            result.append(tag)
    return result[:10]


# ---------------------------------------------------------------- утилиты

def _source_duration(segments: list[tuple[float, float, str]], duration: float) -> float:
    total = float(duration or 0.0)
    if total > 0:
        return total
    ends = []
    for item in segments or []:
        try:
            ends.append(float(item[1]))
        except (TypeError, ValueError, IndexError):
            continue
    return max(ends) if ends else 0.0


def rewrite_for_brainrot(
    segments: list[tuple[float, float, str]],
    *,
    cfg: LLMConfig,
    target_lang: str = "ru",
    log: Callable[[str], None] = print,
) -> list[tuple[float, float, str]]:
    """Переписывает/переводит реплики в формат вирусного пересказа китайской дорамы с сохранением таймкодов."""
    if not segments:
        return []
    indexed = [
        (i, float(s), float(e), " ".join(str(t or "").split()))
        for i, (s, e, t) in enumerate(segments)
        if str(t or "").strip()
    ]
    if not indexed:
        return []

    chain = _candidate_chain(cfg)
    lang_label = "русском" if target_lang == "ru" else "английском"
    lines_in = []
    for idx, s, e, txt in indexed[:140]:
        max_words = max(4, int(round(max(1.0, e - s) * 2.8)))
        lines_in.append(f"{idx}|[{s:.1f}-{e:.1f}s, ~{max_words}сл]|{txt[:180]}")

    prompt = (
        f"Перепиши и переведи реплики ниже на {lang_label} язык в стиле вирусного пересказа китайской ИИ-дорамы / брейнрот-истории.\n"
        "Правила:\n"
        "1. Первая реплика — жёсткий интригующий хук (например: 'Никто не верил, что обычный курьер окажется...').\n"
        "2. Стиль: короткие рубленые фразы, драма, накал, сюжетные повороты, без воды.\n"
        "3. Строго укладывайся в бюджет слов (~сл) для каждой строки, чтобы диктор успел прочитать в тайминг.\n"
        '4. Верни ТОЛЬКО JSON: {"lines": [{"i": 0, "text": "..."}]}\n\n'
        + "\n".join(lines_in)
    )

    for candidate in chain:
        spec = _ALL_BY_ID[candidate["provider"]]
        if spec["kind"] == "account":
            from . import accounts
            if not accounts.active_account(spec["account_provider"], candidate.get("account_id") or ""):
                continue
        elif spec["needs_key"] and not candidate["api_key"]:
            continue
        try:
            raw = _call_provider(candidate, prompt, cfg, log=log)
            data = _extract_json(raw)
            rows = data.get("lines") if isinstance(data, dict) else (data if isinstance(data, list) else None)
            if not isinstance(rows, list):
                continue
            by_idx = {}
            for row in rows:
                if isinstance(row, dict) and "i" in row and row.get("text"):
                    try:
                        by_idx[int(row["i"])] = " ".join(str(row["text"]).split())
                    except (TypeError, ValueError):
                        continue
            if by_idx:
                out = [(s, e, by_idx.get(i, txt)) for i, s, e, txt in indexed]
                log(f"LLM: пересказ дорамы готов ({len(by_idx)}/{len(indexed)} реплик)")
                return out
        except Exception as exc:
            log(f"LLM пересказ ({candidate['label']}): {str(exc)[:140]}")
            continue

    raise LLMError("Не удалось переписать текст через LLM для режима дорамы")
