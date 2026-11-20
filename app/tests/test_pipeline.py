# -*- coding: utf-8 -*-
"""Тесты пайплайна: субтитры, дефолтные моменты, стили — без ffmpeg и сети."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import pipeline as P  # noqa: E402


def test_ass_timestamp():
    assert P.ass_timestamp(0) == "0:00:00.00"
    assert P.ass_timestamp(61.5) == "0:01:01.50"
    assert P.ass_timestamp(3661.234) == "1:01:01.23"


def test_wrap_caption_short_stays_one_line():
    assert P.wrap_caption("КОРОТКО") == "КОРОТКО"
    assert "\\N" not in P.wrap_caption("АБВ ГДЕ")


def test_wrap_caption_splits_long_line():
    text = "ЭТО ОЧЕНЬ ДЛИННАЯ СТРОКА ДЛЯ СУБТИТРОВ"
    wrapped = P.wrap_caption(text)
    assert "\\N" in wrapped
    left, right = wrapped.split("\\N")
    assert max(len(left), len(right)) <= len(text) // 2 + 6
    assert left + " " + right == text


def test_wrap_caption_single_long_word_untouched():
    word = "СУПЕРДЛИННОЕСЛОВОБЕЗПРОБЕЛОВ"
    assert P.wrap_caption(word) == word


def _words(text: str, start: float = 0.0, step: float = 0.4):
    words = []
    cursor = start
    for token in text.split():
        words.append(P.Word(cursor, cursor + step - 0.05, token))
        cursor += step
    return words


def test_build_cues_not_overlapping():
    words = _words("раз два три четыре пять шесть семь восемь девять десять", step=0.5)
    cues = P.build_cues(words, 0.0, 6.0)
    assert cues, "должны быть реплики"
    for index in range(len(cues) - 1):
        assert cues[index][1] <= cues[index + 1][0] + 0.001


def test_build_cues_respects_clip_bounds():
    words = _words("один два три четыре", step=1.0)
    cues = P.build_cues(words, 1.0, 3.0)
    assert cues
    for start, end, _ in cues:
        assert start >= 0
        assert end <= 2.0 + 0.15


def test_build_cues_uppercases_and_escapes():
    words = [P.Word(0.0, 0.5, "текст{с}скобками")]
    cues = P.build_cues(words, 0.0, 2.0)
    assert cues
    assert "{" not in cues[0][2] and "}" not in cues[0][2]


def _segments():
    return [
        P.Segment(0.0, 5.0, "короткая"),
        P.Segment(5.0, 12.0, "это длинная реплика, которая точно станет клипом"),
        P.Segment(12.0, 19.0, "ещё одна реплика для второго клипа"),
        P.Segment(19.0, 40.0, "третья реплика, тоже достаточно длинная"),
    ]


def test_fallback_moments_count_and_bounds():
    moments = P.fallback_moments(_segments(), n=2, min_dur=8, max_dur=20, duration=40.0)
    assert len(moments) == 2
    for moment in moments:
        assert 8 <= moment.end - moment.start <= 20.001
        assert 0 <= moment.start < moment.end <= 40.0
    starts = [m.start for m in moments]
    assert starts == sorted(starts)


def test_fallback_moments_no_overlap():
    moments = P.fallback_moments(_segments(), n=4, min_dur=5, max_dur=15, duration=40.0)
    for index in range(len(moments) - 1):
        assert moments[index].end <= moments[index + 1].start + 0.001


def test_styles_have_required_keys():
    required = {"label", "fontsize", "primary", "outline", "outline_w", "uppercase", "margin_v", "bold", "background"}
    for key, style in P.STYLES.items():
        assert required <= set(style), f"в стиле {key} не хватает полей"
        assert style["background"] in {"blur", "black", "crop"}


def test_write_ass(tmp_path):
    cues = [(0.0, 1.0, "привет"), (1.2, 2.5, "мир")]
    path = tmp_path / "test.ass"
    P.write_ass(path, cues, P.STYLES["classic"], "Arial Black", True)
    content = path.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert content.count("Dialogue:") == 2
    assert "ПРИВЕТ" in content


def test_build_video_filter_modes():
    portrait = P.build_video_filter("a.ass", 0.5, "blur")
    assert "split=2" not in portrait and "crop=1080:1920" in portrait

    landscape = P.build_video_filter("a.ass", 1.777, "blur")
    assert "split=2" in landscape and "gblur" in landscape and "ass=a.ass" in landscape

    black = P.build_video_filter("a.ass", 1.777, "black")
    assert "pad=1080:1920" in black
    assert "gblur" not in black


def test_build_video_filter_always_burns_subtitles():
    for ratio in (0.5, 1.0, 1.777):
        for background in ("blur", "black", "crop"):
            assert ",ass=subs.ass[v]" in P.build_video_filter("subs.ass", ratio, background)


def test_probe_rejects_missing_file(tmp_path):
    with pytest.raises(P.PipelineError):
        P.probe(tmp_path / "нет-такого.mp4")
