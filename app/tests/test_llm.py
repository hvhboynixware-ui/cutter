# -*- coding: utf-8 -*-
"""Тесты llm.py: полностью офлайн, сеть подменяется фейковым requests.post."""

from __future__ import annotations

import json

import pytest
import requests

from app.core import llm

SEGMENTS = [
    (0.0, 10.0, "привет"),
    (10.0, 25.0, "вторая часть"),
    (25.0, 60.0, "и финал"),
]


# ---------------------------------------------------------------- фейки

class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, text: str | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        if text is not None:
            self.text = text
        elif payload is not None:
            self.text = json.dumps(payload, ensure_ascii=False)
        else:
            self.text = ""

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def openai_reply(content: str) -> FakeResponse:
    return FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def gemini_reply(content: str) -> FakeResponse:
    return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": content}]}}]})


def clips_json(clips: list) -> str:
    return json.dumps({"clips": clips}, ensure_ascii=False)


def clip(start, end, score=50, **extra) -> dict:
    data = {
        "start": start,
        "end": end,
        "hook": "фраза-хук",
        "title": "заголовок",
        "hashtags": ["#тег"],
        "reason": "почему залетит",
        "score": score,
    }
    data.update(extra)
    return data


def make_cfg(**kwargs) -> llm.LLMConfig:
    values = {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "api_key": "test-key",
        "temperature": 1.0,
        "fallback": False,
    }
    values.update(kwargs)
    return llm.LLMConfig(**values)


def patch_post(monkeypatch, handler):
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        # копируем payload: провайдерский код может менять его после первой попытки
        record = dict(kwargs)
        if "json" in record:
            record["json"] = json.loads(json.dumps(record["json"], ensure_ascii=False))
        calls.append({"url": url, **record})
        return handler(url, kwargs)

    monkeypatch.setattr(llm.requests, "post", fake_post)
    return calls


def run_pick(monkeypatch, handler, *, cfg=None, n=3, min_dur=10.0, max_dur=60.0, duration=120.0):
    calls = patch_post(monkeypatch, handler)
    logs: list[str] = []
    moments = llm.pick_moments(
        SEGMENTS, n=n, min_dur=min_dur, max_dur=max_dur, duration=duration,
        cfg=cfg or make_cfg(), log=logs.append,
    )
    return moments, calls, logs


# ---------------------------------------------------------------- провайдеры

def test_available_providers_shape():
    providers = llm.available_providers()
    assert [item["id"] for item in providers] == ["deepseek", "openrouter", "gemini", "groq", "ollama"]
    for item in providers:
        assert set(item) == {"id", "label", "models", "needs_key", "key_url", "note"}
        assert item["models"], item["id"]
        for model in item["models"]:
            assert set(model) == {"id", "label", "free"}
            assert isinstance(model["free"], bool)
    by_id = {item["id"]: item for item in providers}
    assert by_id["ollama"]["needs_key"] is False
    assert by_id["deepseek"]["needs_key"] is True
    assert by_id["openrouter"]["models"][0]["free"] is True


def test_resolve_base_urls():
    expected = {
        "deepseek": "https://api.deepseek.com",
        "openrouter": "https://openrouter.ai/api/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
        "groq": "https://api.groq.com/openai/v1",
        "ollama": "http://127.0.0.1:11434/v1",
    }
    for provider, base_url in expected.items():
        provider_id, model, resolved = llm.resolve(provider)
        assert provider_id == provider
        assert resolved == base_url
        assert model
    assert llm.resolve("deepseek")[1] == "deepseek-flash"
    assert llm.resolve("openrouter")[1] == "deepseek/deepseek-chat-v3-0324:free"
    assert llm.resolve("gemini")[1] == "gemini-2.0-flash"
    assert llm.resolve("groq")[1] == "llama-3.3-70b-versatile"
    assert llm.resolve("ollama")[1] == "llama3.1"
    assert llm.resolve("groq", "my-custom-model") == ("groq", "my-custom-model", expected["groq"])
    with pytest.raises(llm.LLMError):
        llm.resolve("unknown")


def test_available_providers_default_models_free_flags():
    providers = {item["id"]: item for item in llm.available_providers()}
    assert providers["deepseek"]["models"][0]["id"] == "deepseek-flash"
    assert providers["deepseek"]["models"][0]["free"] is False
    assert providers["openrouter"]["models"][0]["free"] is True
    assert providers["ollama"]["models"][0]["free"] is True


# ---------------------------------------------------------------- разбор и валидация

def test_parse_markdown_wrapper_and_mm_ss(monkeypatch):
    content = "```json\n" + clips_json([clip("01:05", "01:35", score="87")]) + "\n```"
    moments, calls, _ = run_pick(monkeypatch, lambda url, kw: openai_reply(content), max_dur=45.0)
    assert len(calls) == 1
    assert len(moments) == 1
    moment = moments[0]
    assert moment.start == pytest.approx(65.0)
    assert moment.end == pytest.approx(95.0)
    assert isinstance(moment.score, int) and moment.score == 87
    assert moment.hashtags == ["тег"]


def test_parse_seconds_numeric_and_sort_by_time(monkeypatch):
    content = clips_json([clip(200.0, 220.0), clip(20.0, 40.0)])
    moments, *_ = run_pick(
        monkeypatch, lambda url, kw: openai_reply(content), duration=300.0, max_dur=60.0,
    )
    assert [round(item.start) for item in moments] == [20, 200]
    assert all(isinstance(item.score, int) for item in moments)


def test_validation_trims_long_and_drops_overlaps(monkeypatch):
    content = clips_json([
        clip(0.0, 90.0, score=95),
        clip(30.0, 55.0, score=80),
        clip(70.0, 100.0, score=70),
        clip(105.0, 130.0, score=60),
    ])
    moments, *_ = run_pick(
        monkeypatch, lambda url, kw: openai_reply(content),
        n=5, min_dur=10.0, max_dur=60.0, duration=120.0,
    )
    assert [(item.start, item.end) for item in moments] == [
        (0.0, 60.0), (70.0, 100.0), (105.0, 120.0),
    ]
    for item in moments:
        assert 10.0 - 0.05 <= item.end - item.start <= 60.0 + 0.05


def test_short_clip_extended_to_min_dur(monkeypatch):
    content = clips_json([clip(50.0, 53.0)])
    moments, *_ = run_pick(monkeypatch, lambda url, kw: openai_reply(content))
    assert len(moments) == 1
    assert moments[0].start == pytest.approx(50.0)
    assert moments[0].end == pytest.approx(60.0)


def test_overlap_keeps_higher_score(monkeypatch):
    content = clips_json([clip(0.0, 40.0, score=10), clip(20.0, 50.0, score=90)])
    moments, *_ = run_pick(monkeypatch, lambda url, kw: openai_reply(content))
    assert [(item.start, item.end) for item in moments] == [(20.0, 50.0)]


def test_n_limit_keeps_best_scores_and_sorts(monkeypatch):
    content = clips_json([
        clip(0.0, 20.0, score=50),
        clip(30.0, 50.0, score=99),
        clip(60.0, 80.0, score=70),
    ])
    moments, *_ = run_pick(monkeypatch, lambda url, kw: openai_reply(content), n=2)
    assert [(item.start, item.end) for item in moments] == [(30.0, 50.0), (60.0, 80.0)]


def test_score_and_time_helpers():
    assert llm._parse_time(12.5) == 12.5
    assert llm._parse_time("12,5") == 12.5
    assert llm._parse_time("01:05") == 65.0
    assert llm._parse_time("1:02:03") == 3723.0
    assert llm._parse_time("12 сек") == 12.0
    assert llm._parse_time(None) is None
    assert llm._parse_time("мусор") is None
    assert llm._parse_score(150) == 100
    assert llm._parse_score(-5) == 0
    assert llm._parse_score("45") == 45
    assert llm._parse_score(None) == 0


# ---------------------------------------------------------------- ретраи и ошибки

def test_empty_clips_retries_once_then_raises(monkeypatch):
    calls = patch_post(monkeypatch, lambda url, kw: openai_reply('{"clips": []}'))
    with pytest.raises(llm.LLMError) as error:
        llm.pick_moments(SEGMENTS, n=3, min_dur=10, max_dur=60, duration=120,
                         cfg=make_cfg(), log=lambda _: None)
    assert len(calls) == 2
    assert "ответ модели не разобрать" in str(error.value)


def test_retry_with_stricter_prompt_then_success(monkeypatch):
    replies = ["это вообще не json", clips_json([clip(0.0, 20.0)])]

    def handler(url, kwargs):
        return openai_reply(replies.pop(0))

    moments, calls, logs = run_pick(monkeypatch, handler)
    assert len(moments) == 1
    assert len(calls) == 2
    prompt = calls[1]["json"]["messages"][1]["content"]
    assert "ПОВТОР" in prompt
    assert any("вторая попытки" in line or "со второй" in line for line in logs)


def test_bad_json_twice_raises(monkeypatch):
    calls = patch_post(monkeypatch, lambda url, kw: openai_reply("<html>ошибка</html>"))
    with pytest.raises(llm.LLMError):
        llm.pick_moments(SEGMENTS, n=3, min_dur=10, max_dur=60, duration=120,
                         cfg=make_cfg(), log=lambda _: None)
    assert len(calls) == 2


def test_no_key_skips_provider_without_network(monkeypatch):
    calls = patch_post(monkeypatch, lambda url, kw: (_ for _ in ()).throw(AssertionError("сеть нельзя")))
    with pytest.raises(llm.LLMError) as error:
        llm.pick_moments(SEGMENTS, n=3, min_dur=10, max_dur=60, duration=120,
                         cfg=make_cfg(api_key="", fallback=False), log=lambda _: None)
    assert calls == []
    assert "API-ключ" in str(error.value)


# ---------------------------------------------------------------- фолбэк-цепочка

def test_fallback_switches_to_next_provider(monkeypatch):
    good = clips_json([clip(10.0, 40.0, score=77)])

    def handler(url, kwargs):
        if "deepseek" in url:
            return FakeResponse(401, text='{"error": "invalid api key"}')
        if "openrouter" in url:
            return openai_reply(good)
        raise AssertionError(f"неожиданный url: {url}")

    cfg = make_cfg(api_key="bad-key", keys={"openrouter": "or-key"}, fallback=True)
    moments, calls, logs = run_pick(monkeypatch, handler, cfg=cfg)
    assert len(moments) == 1
    assert [call["url"] for call in calls] == [
        "https://api.deepseek.com/chat/completions",
        "https://openrouter.ai/api/v1/chat/completions",
    ]
    assert calls[1]["json"]["model"] == "deepseek/deepseek-chat-v3-0324:free"
    assert any("401" in line for line in logs)


def test_fallback_all_fail_has_hints(monkeypatch):
    def handler(url, kwargs):
        if "deepseek" in url:
            return FakeResponse(401, text='{"error": "invalid api key"}')
        raise requests.exceptions.ConnectionError("connection refused")

    cfg = make_cfg(api_key="bad-key", fallback=True)
    with pytest.raises(llm.LLMError) as error:
        llm.pick_moments(SEGMENTS, n=3, min_dur=10, max_dur=60, duration=120,
                         cfg=cfg, log=lambda _: None)
    message = str(error.value)
    assert "Ни один провайдер" in message
    assert "API-ключ" in message
    assert "Ollama" in message


# ---------------------------------------------------------------- ping

def test_ping_ok_and_unauthorized(monkeypatch):
    patch_post(monkeypatch, lambda url, kw: openai_reply("pong"))
    ok, message = llm.ping(make_cfg())
    assert ok is True
    assert "DeepSeek" in message

    patch_post(monkeypatch, lambda url, kw: FakeResponse(401, text="bad key"))
    ok, message = llm.ping(make_cfg())
    assert ok is False
    assert "ключ" in message.lower()


def test_ping_without_key_returns_false(monkeypatch):
    calls = patch_post(monkeypatch, lambda url, kw: openai_reply("pong"))
    ok, message = llm.ping(make_cfg(api_key=""))
    assert ok is False
    assert calls == []
    assert "API-ключ" in message


def test_gemini_ping_uses_key_in_query(monkeypatch):
    calls = patch_post(monkeypatch, lambda url, kw: gemini_reply("pong"))
    cfg = make_cfg(provider="gemini", model="gemini-2.0-flash", api_key="g-key")
    ok, _ = llm.ping(cfg)
    assert ok is True
    call = calls[0]
    assert "generativelanguage.googleapis.com" in call["url"]
    assert call["url"].endswith("/models/gemini-2.0-flash:generateContent")
    assert call["params"] == {"key": "g-key"}


# ---------------------------------------------------------------- Gemini REST

def test_gemini_request_format_and_parse(monkeypatch):
    content = clips_json([clip(5.0, 25.0, score=88)])
    calls = patch_post(monkeypatch, lambda url, kw: gemini_reply(content))
    cfg = make_cfg(provider="gemini", model="gemini-2.0-flash", api_key="g-key")
    moments = llm.pick_moments(SEGMENTS, n=2, min_dur=10, max_dur=60, duration=120,
                               cfg=cfg, log=lambda _: None)
    assert len(moments) == 1
    call = calls[0]
    assert call["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.0-flash:generateContent"
    )
    assert call["params"] == {"key": "g-key"}
    config = call["json"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert "contents" in call["json"] and "messages" not in call["json"]


def test_base_url_and_temperature_overrides(monkeypatch):
    calls = patch_post(
        monkeypatch,
        lambda url, kw: openai_reply(clips_json([clip(5.0, 25.0)])),
    )
    cfg = make_cfg(base_url="http://127.0.0.1:9999/v1/", temperature=0.3)
    llm.pick_moments(SEGMENTS, n=1, min_dur=10, max_dur=60, duration=120,
                     cfg=cfg, log=lambda _: None)
    assert calls[0]["url"] == "http://127.0.0.1:9999/v1/chat/completions"
    assert calls[0]["json"]["temperature"] == pytest.approx(0.3)
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}


def test_provider_http_error_reports_code_and_body(monkeypatch):
    patch_post(monkeypatch, lambda url, kw: FakeResponse(500, text="upstream boom"))
    cfg = make_cfg(fallback=False)
    with pytest.raises(llm.LLMError) as error:
        llm.pick_moments(SEGMENTS, n=3, min_dur=10, max_dur=60, duration=120,
                         cfg=cfg, log=lambda _: None)
    message = str(error.value)
    assert "DeepSeek" in message
    assert "500" in message
    assert "upstream boom" in message


def test_response_format_retry_without_json_mode(monkeypatch):
    """Часть free-моделей отвечает 400 на response_format — повторяем без него."""
    good = clips_json([clip(5.0, 25.0)])

    def handler(url, kwargs):
        if "response_format" in kwargs["json"]:
            return FakeResponse(400, text='{"error":"response_format json_object not supported"}')
        return openai_reply(good)

    cfg = make_cfg(provider="openrouter", model="deepseek/deepseek-chat-v3-0324:free")
    moments, calls, _ = run_pick(monkeypatch, handler, cfg=cfg)
    assert len(moments) == 1
    assert len(calls) == 2
    assert "response_format" in calls[0]["json"]
    assert "response_format" not in calls[1]["json"]


def test_zero_clips_returns_early_without_network(monkeypatch):
    calls = patch_post(monkeypatch, lambda url, kw: (_ for _ in ()).throw(AssertionError("сеть нельзя")))
    moments = llm.pick_moments(SEGMENTS, n=0, min_dur=10, max_dur=60, duration=120,
                               cfg=make_cfg(), log=lambda _: None)
    assert moments == []
    assert calls == []
