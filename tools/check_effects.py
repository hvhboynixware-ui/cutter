# -*- coding: utf-8 -*-
"""Быстрая проверка рендера с эффектом, защитой и музыкой (без сервера)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core import pipeline as P  # noqa: E402
from app.core import settings as S  # noqa: E402


class FakeJob:
    def __init__(self):
        self.logs = []
        self.progress = 0.0

    def log(self, text):
        self.logs.append(text)

    def set_progress(self, value, stage="", message=""):
        self.progress = value

    def cancelled(self):
        return False


def main() -> int:
    assets = ROOT / ".test-assets"
    assets.mkdir(exist_ok=True)
    source = assets / "demo_source.mp4"
    if not source.is_file():
        print("нет тестового видео", source)
        return 1

    music = assets / "test_music.mp3"
    if not music.is_file():
        ff = S.ffmpeg_path()
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=220:duration=30",
                        "-c:a", "libmp3lame", "-b:a", "128k", str(music)], check=True)

    out = assets / "render_check"
    if out.exists():
        import shutil
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir()

    settings = S.load()
    settings["render"].update({
        "clips": 2, "style": "bold", "effect": "cinema", "protection": "light",
        "music": "none", "music_volume": 0.2, "min_dur": 8, "max_dur": 18,
    })
    os.environ["CUTTER_FAKE_LLM"] = "1"

    job = FakeJob()

    # напрямую рендерим два клипа с музыкой, чтобы проверить граф целиком
    info = P.probe(source)
    ratio = info["width"] / info["height"]
    work = out / ".work"
    work.mkdir()
    from app.core import effects as FX

    # пока пак музыки не готов, подсовываем синтетический трек через music_path-обход
    import app.core.effects as E
    original_music_path = E.music_path
    E.music_path = lambda music_id: music if music_id == "none" else original_music_path(music_id)

    result = P.analyze(job=job, source=source, workdir=out, settings=settings, on_log=print)
    clips = result["clips"]
    print(f"клипов: {len(clips)}")
    assert clips, "ни одного клипа"

    for clip in clips:
        path = out / "clips" / clip["file"]
        assert path.is_file(), path
        probe = P.probe(path)
        print(f"  {clip['file']}: {probe['width']}x{probe['height']} "
              f"{probe['duration']:.1f}s audio={probe['has_audio']} words={clip['words']} wpm={clip['wpm']}")
        assert probe["has_audio"], "в клипе нет звука"
        assert (probe["width"], probe["height"]) == (1080, 1920)

    manifest = (out / "clips.json").read_text(encoding="utf-8")
    assert '"effect": "cinema"' in manifest or '"effect":"cinema"' in manifest
    print("Render OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
