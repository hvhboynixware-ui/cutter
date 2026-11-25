# -*- coding: utf-8 -*-
"""Тесты новых модулей: аккаунты подписки, разбиение на части, TTS, интеграции и стабильность YouTube."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import accounts, effects as FX, integrator, llm, pipeline as P, settings as S, tts, youtube  # noqa: E402


# ---------------------------------------------------------------- аккаунты и LLM по подписке

def test_accounts_crud_and_masking(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    saved = accounts.upsert_account({
        "id": "acc_1",
        "provider": "chatgpt",
        "auth_method": "oauth",
        "label": "plus@example.com",
        "email": "plus@example.com",
        "plan": "plus",
        "tokens": {"access_token": "secret-access", "refresh_token": "secret-refresh", "expires_at": 9999999999},
        "status": "ready",
    })
    assert "tokens" not in saved
    assert saved["has_token"] is True
    assert saved["plan"] == "plus"

    listed = accounts.list_accounts("chatgpt", mask=True)
    assert len(listed) == 1
    assert listed[0]["id"] == "acc_1"

    active = accounts.active_account("chatgpt_sub")
    assert active is not None and active["id"] == "acc_1"
    assert accounts.remove_account("acc_1") is True
    assert accounts.list_accounts("chatgpt") == []


def test_extract_openai_claims():
    payload = base64.urlsafe_b64encode(json.dumps({
        "email": "user@test.dev",
        "https://api.openai.com/auth": {"chatgpt_account_id": "org_123", "chatgpt_plan_type": "pro"},
    }).encode()).decode().rstrip("=")
    jwt = f"eyJ0eXAiOiJKV1QifQ.{payload}.sig"
    email, acc_id, plan = accounts._extract_openai_claims(jwt, "")
    assert email == "user@test.dev"
    assert acc_id == "org_123"
    assert plan == "pro"


def test_llm_routes_to_chatgpt_subscription(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    accounts.upsert_account({
        "id": "acc_gpt",
        "provider": "chatgpt",
        "auth_method": "oauth",
        "label": "sub",
        "tokens": {"access_token": "tok", "refresh_token": "ref", "expires_at": 9999999999, "account_id": "a1"},
        "status": "ready",
    })
    monkeypatch.setattr(
        accounts, "query_account",
        lambda acc, prompt, **kw: json.dumps({"clips": [{"start": 1.0, "end": 18.0, "title": "Топ", "score": 91}]}),
    )
    cfg = llm.LLMConfig(provider="chatgpt_sub", model="gpt-5.1-codex-mini", account_id="acc_gpt", fallback=False)
    moments = llm.pick_moments([(0.0, 20.0, "текст")], n=1, min_dur=10, max_dur=30, duration=60.0, cfg=cfg, log=lambda _: None)
    assert len(moments) == 1
    assert moments[0].title == "Топ"
    assert moments[0].score == 91


def test_rewrite_for_brainrot_preserves_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    accounts.upsert_account({
        "id": "acc_g",
        "provider": "google",
        "auth_method": "browser",
        "label": "g@test",
        "profile_dir": "google_acc_g",
        "status": "ready",
    })
    monkeypatch.setattr(
        accounts, "query_account",
        lambda acc, prompt, **kw: json.dumps({"lines": [{"i": 0, "text": "Никто не знал правды!"}, {"i": 1, "text": "Но он достал чек."}]}),
    )
    cfg = llm.LLMConfig(provider="google_sub", model="gemini-web", account_id="acc_g", fallback=False)
    out = llm.rewrite_for_brainrot([(0.0, 3.0, "hello"), (3.2, 6.0, "world")], cfg=cfg, log=lambda _: None)
    assert out == [(0.0, 3.0, "Никто не знал правды!"), (3.2, 6.0, "Но он достал чек.")]


# ---------------------------------------------------------------- разбиение на части и кадр

def test_split_into_parts_respects_sentence_boundaries():
    segs = [
        P.Segment(0.0, 8.0, "Первая реплика без точки"),
        P.Segment(8.2, 16.0, "конец первого предложения."),
        P.Segment(17.0, 25.0, "Второе предложение начинается тут."),
        P.Segment(25.3, 35.0, "Финальная часть ролика."),
    ]
    parts = P.split_into_parts(segs, n=0, min_dur=12.0, max_dur=22.0, duration=36.0)
    assert len(parts) == 2
    assert parts[0].start == pytest.approx(0.0)
    assert 15.0 <= parts[0].end <= 17.0
    assert parts[1].start >= parts[0].end - 0.01
    assert parts[1].title.startswith("Часть 2")


def test_split_into_parts_handles_single_long_segment_with_words():
    words = [P.Word(i * 1.0, i * 1.0 + 0.8, f"слово{i}" + ("." if i in (14, 29) else "")) for i in range(40)]
    segs = [P.Segment(0.0, 40.0, " ".join(w.text for w in words))]
    parts = P.split_into_parts(segs, n=3, min_dur=10.0, max_dur=18.0, duration=40.0, words=words)
    assert len(parts) >= 2
    for p in parts:
        assert p.end - p.start <= 22.0


def test_video_filter_without_subs_and_original_framing():
    no_subs = P.build_video_filter("", 1.777, "blur")
    assert "ass=" not in no_subs
    assert no_subs.endswith("[v]")

    orig = P.build_video_filter("s.ass", 1.777, "original")
    assert "crop=" not in orig
    assert "pad=" not in orig
    assert ",ass=s.ass[v]" in orig


# ---------------------------------------------------------------- TTS и аудио с озвучкой

def test_tts_atempo_chain_and_word_timing():
    assert tts._atempo_chain(1.5) == "atempo=1.5000"
    assert tts._atempo_chain(2.5) == "atempo=2.0,atempo=1.2500"
    words = tts._words_for_line(1.0, 4.0, "один два три")
    assert len(words) == 3
    assert words[0][0] == pytest.approx(1.0)
    assert words[-1][1] == pytest.approx(4.0)


def test_audio_graph_dub_with_music(tmp_path):
    dub = tmp_path / "dub.wav"
    mus = tmp_path / "mus.mp3"
    dub.write_bytes(b"RIFF")
    mus.write_bytes(b"ID3")
    args, graph = FX.build_audio_graph(has_source_audio=True, music=mus, music_volume=0.22, dub=dub, source_volume=0.08)
    assert "-i" in args
    assert "sidechaincompress" in graph
    assert "volume=0.080" in graph
    assert "[a]" in graph


# ---------------------------------------------------------------- интеграции

def test_integration_library_save_list_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    ad = tmp_path / "vpn_ad.mp4"
    ad.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    saved = integrator.save_integration(ad, name="VPN Вставка", mode="insert", position="after_hook", hook_sec=3.5)
    assert saved["mode"] == "insert"
    assert saved["position"] == "after_hook"
    assert len(integrator.list_integrations()) == 1
    assert integrator.get_integration_file(saved["id"]).is_file()
    assert integrator.delete_integration(saved["id"]) is True
    assert integrator.list_integrations() == []


# ---------------------------------------------------------------- YouTube стабилизация

def test_youtube_retries_transient_error_with_fallback_client(monkeypatch, tmp_path):
    attempts: list[dict] = []

    class FlakyYDL:
        def __init__(self, opts: dict) -> None:
            self.opts = opts
            attempts.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url: str, download: bool = False):
            if len(attempts) == 1:
                raise RuntimeError("HTTP Error 403: Forbidden")
            if download:
                out = Path(self.opts["outtmpl"]["default"].replace("%(ext)s", "mp4"))
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"video" * 256)
            return {"id": "abc123XYZ", "title": "Ok Video", "duration": 20.0, "uploader": "Ch", "formats": [{"height": 720}]}

    monkeypatch.setattr(youtube, "yt_dlp", SimpleNamespace(YoutubeDL=FlakyYDL))
    res = youtube.download("https://www.youtube.com/watch?v=abc123XYZ", tmp_path)
    assert res.path.is_file()
    assert len(attempts) >= 2
    assert attempts[1].get("force_ipv4") is True
    assert attempts[1]["extractor_args"]["youtube"]["player_client"] == ["android", "web"]


# ---------------------------------------------------------------- Playwright TMP & OAuth port fix

def test_browser_ensure_temp_dir_recreates_missing_tmp(tmp_path, monkeypatch):
    from app.core.upload import browsers

    missing_tmp = tmp_path / "missing_tmp"
    monkeypatch.setenv("TMP", str(missing_tmp))
    monkeypatch.setenv("TEMP", str(missing_tmp))
    assert not missing_tmp.exists()
    browsers._ensure_temp_dir(tmp_path / "fallback")
    assert missing_tmp.is_dir()


def test_oauth_reuse_http_server_sets_allow_reuse_address():
    with accounts._OAuthCallbackServer("test-state", port=0) as srv:
        assert srv._server is not None
        assert getattr(srv._server, "allow_reuse_address", False) is True


def test_web_ui_serves_redesigned_studio_html_and_assets():
    from fastapi.testclient import TestClient
    from app.server import app as fastapi_app

    client = TestClient(fastapi_app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Cutter Workbench" in resp.text
    assert 'id="preview-phone"' in resp.text
    assert 'id="stage-player-wrap"' in resp.text
    assert 'id="auth-console"' in resp.text
    assert "no-store" in (resp.headers.get("cache-control") or "")
    assert client.get("/assets/app.css").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    sample_resp = client.get("/api/voices/canon_bright/sample")
    assert sample_resp.status_code == 200
    assert len(sample_resp.content) > 10000


def test_tts_merge_dub_segments_combines_short_phrases():
    merged = tts.merge_dub_segments([
        tts.DubSegment(0.0, 1.5, "Оказалось, незадолго до этого"),
        tts.DubSegment(1.7, 3.8, "парень проверил прогноз погоды."),
        tts.DubSegment(4.0, 6.2, "Он предупредил всю деревню."),
    ])
    assert len(merged) == 2
    assert merged[0].start == pytest.approx(0.0)
    assert merged[0].end == pytest.approx(3.8)
    assert "прогноз погоды." in merged[0].text
    assert tts.CANON_SAMPLE_1.is_file()
    assert tts.CANON_SAMPLE_2.is_file()



