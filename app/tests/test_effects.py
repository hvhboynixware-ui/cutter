# -*- coding: utf-8 -*-
"""Тесты эффектов кадра, защиты от повторов и музыкальной подложки."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import effects as FX  # noqa: E402
from app.core import pipeline as P  # noqa: E402


def test_effect_ids_present():
    ids = {item["id"] for item in FX.effect_ids()}
    assert {"none", "cinema", "aura", "vhs", "noir", "warm"} <= ids


def test_unknown_effect_falls_back():
    assert FX.get_effect("нет-такого").id == "none"
    assert FX.get_effect(None).id == "none"
    assert FX.get_effect("cinema").id == "cinema"


def test_protection_levels():
    off, cfg_off = FX.protection_filter("off")
    light, cfg_light = FX.protection_filter("light")
    strong, cfg_strong = FX.protection_filter("strong")
    assert off == "" and cfg_off.zoom == 1.0
    assert "hflip" in light and cfg_light.zoom > 1.0
    assert "hflip" in strong and cfg_strong.speed > 1.0


def test_compose_chain_order():
    chain = FX.compose_video_chain("cinema", "light")
    assert chain.index("hflip") < chain.index("colorbalance")
    assert FX.compose_video_chain("none", "off") == ""
    assert FX.compose_video_chain("cinema", "off").startswith("eq=contrast")


def test_video_filter_has_effect_and_keeps_subs():
    graph = P.build_video_filter("subs.ass", 1.777, "blur", chain="hflip,eq=contrast=1.05", speed=1.02)
    assert "hflip" in graph
    assert "setpts=PTS/1.0200" in graph
    assert ",ass=subs.ass[v]" in graph
    assert "gblur" in graph


def test_video_filter_without_extras_unchanged():
    graph = P.build_video_filter("subs.ass", 0.5, "blur")
    assert graph == "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,ass=subs.ass[v]"


def test_audio_graph_without_music():
    args, graph = FX.build_audio_graph(has_source_audio=True, music=None)
    assert args == []
    assert "loudnorm" in graph and "[a]" in graph

    args, graph = FX.build_audio_graph(has_source_audio=False, music=None)
    assert args == [] and graph == ""


def test_audio_graph_with_music(tmp_path):
    music = tmp_path / "track.mp3"
    music.write_bytes(b"\x00" * 10)
    args, graph = FX.build_audio_graph(has_source_audio=True, music=music, music_volume=0.2)
    assert args[:2] == ["-stream_loop", "-1"]
    assert "sidechaincompress" in graph
    assert "volume=0.200" in graph
    assert "[a]" in graph


def test_audio_graph_music_only(tmp_path):
    music = tmp_path / "track.mp3"
    music.write_bytes(b"\x00" * 10)
    args, graph = FX.build_audio_graph(has_source_audio=False, music=music)
    assert "anull" in graph


def test_music_path_none():
    assert FX.music_path("none") is None
    assert FX.music_path("") is None
    assert FX.music_path("нет-такого") is None
