# -*- coding: utf-8 -*-
"""Тесты модуля моделей: точные размеры, проверка полноты, удаление."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import models as M  # noqa: E402


def test_sizes_are_exact_and_sorted():
    ids = [item["id"] for item in M.SIZES]
    assert ids == ["tiny", "base", "small", "medium", "large-v3"]
    sizes = [item["bytes"] for item in M.SIZES]
    assert sizes == sorted(sizes), "размеры должны расти от tiny к large"
    assert all(size > 10 * 1024 * 1024 for size in sizes)


def test_size_info_and_expected():
    assert M.size_info("small")["id"] == "small"
    assert M.size_info("нет") is None
    assert M.expected_bytes("medium") == M.size_info("medium")["bytes"]


def test_installed_models_ignores_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(M.S, "MODELS_DIR", tmp_path)
    assert M.installed_models() == []

    folder = tmp_path / "faster-whisper-tiny"
    folder.mkdir()
    (folder / "model.bin").write_bytes(b"\x00" * 2048)
    rows = M.installed_models()
    assert len(rows) == 1
    assert rows[0]["size"] == "tiny"
    assert rows[0]["complete"] is False, "недокачанная модель не должна считаться готовой"


def test_is_installed_requires_expected_size(tmp_path, monkeypatch):
    monkeypatch.setattr(M.S, "MODELS_DIR", tmp_path)
    folder = tmp_path / "faster-whisper-tiny"
    folder.mkdir()
    (folder / "model.bin").write_bytes(b"\x00" * 2048)
    assert M.is_installed("tiny") is False


def test_delete_model(tmp_path, monkeypatch):
    monkeypatch.setattr(M.S, "MODELS_DIR", tmp_path)
    folder = tmp_path / "faster-whisper-base"
    folder.mkdir()
    (folder / "model.bin").write_bytes(b"\x00" * 1024)
    assert M.delete_model("base") is True
    assert not folder.exists()
    assert M.delete_model("base") is False


def test_human_sizes():
    assert M._human(5 * 1024 * 1024) == "5.0 МБ"
    assert M._human(2 * 1024 * 1024 * 1024).endswith("ГБ")
