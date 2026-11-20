# -*- coding: utf-8 -*-
"""Собирает демо-рендеры: разные стили субтитров × эффекты × музыка.

Результат: Cutter/demo/demo_*.mp4 + contact sheet Cutter/demo/contact-sheet.png
Запуск: python tools/make_demo.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core import effects as FX  # noqa: E402
from app.core import pipeline as P  # noqa: E402
from app.core import settings as S  # noqa: E402

ASSETS = ROOT / ".test-assets"
DEMO = ROOT / "demo"
SOURCE = ASSETS / "demo_visual.mp4"
WAV = ASSETS / "speech.wav"

# (файл, стиль субтитров, эффект, музыка, защита, комментарий)
COMBOS = [
    ("demo_01_cinema_classic", "classic", "cinema", "lofi-chill", "off"),
    ("demo_02_aura_bold_hype", "bold", "aura", "hype-trap", "light"),
    ("demo_03_vhs_neon_dark", "neon", "vhs", "dark-tension", "off"),
    ("demo_04_warm_clean_podcast", "clean", "warm", "podcast-intro", "off"),
    ("demo_05_noir_bold_emotional", "bold", "noir", "emotional-piano", "off"),
    ("demo_06_baseline_minimal", "classic", "none", "minimal-tech", "light"),
]


def ensure_source() -> None:
    ASSETS.mkdir(exist_ok=True)
    if SOURCE.is_file() and WAV.is_file():
        return
    print("готовлю тестовое видео...")
    if not WAV.is_file():
        ps = ASSETS / "tts.ps1"
        ps.write_text(
            'Add-Type -AssemblyName System.Speech\n'
            '$wav = Join-Path $args[0] "speech.wav"\n'
            '$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n'
            '$s.Rate = 0\n'
            '$s.SetOutputToWaveFile($wav)\n'
            '$text = "Nobody tells you this when you start, but the first twenty clips will fail. '
            'That is not a bug, that is the tuition fee. Every platform rewards one thing above all: '
            'the first two seconds. If the first two seconds are boring, nothing else matters. '
            'So here is the trick. You do not look for the best moment, you look for the loudest moment. '
            'Then you cut one second before it and stop one second after the reaction. That is the whole job."\n'
            '$s.Speak($text)\n$s.Dispose()\n',
            encoding="utf-8",
        )
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps), str(ASSETS)],
                       check=True)

    ff = S.ffmpeg_path()
    subprocess.run([
        ff, "-hide_banner", "-loglevel", "error", "-y",
        # кинематографичный градиент: нейтральная база, на которой видно эффекты
        "-f", "lavfi", "-i",
        "gradients=s=1080x1920:rate=30:c0=0x0a1220:c1=0x18405a:c2=0xb06a2c:c3=0x221018:n=4:speed=0.008:type=linear",
        "-i", str(WAV),
        "-t", "40",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        str(SOURCE),
    ], check=True)
    print("источник:", SOURCE)


def transcribe_once() -> tuple[list, list]:
    info = P.probe(SOURCE)
    wav = ASSETS / "demo_audio.wav"
    P.extract_audio(SOURCE, wav)
    model_path, installed = S.whisper_model_path(S.load())
    if not installed:
        raise SystemExit("нет модели whisper — скачай в настройках приложения")
    print("распознаю один раз...")
    segments, words = P.transcribe(wav, model_path, "en", info["duration"], log=print)
    return segments, words


def main() -> int:
    ensure_source()
    DEMO.mkdir(exist_ok=True)
    for old in DEMO.glob("demo_*.mp4"):
        old.unlink()

    segments, words = transcribe_once()
    info = P.probe(SOURCE)
    ratio = info["width"] / info["height"]
    duration_total = info["duration"]

    # окна по 10 секунд, сдвигаем по таймлайну, чтобы клипы отличались речью
    windows = []
    starts = [1.0, 11.0, 21.0, 4.0, 14.0, 24.0]
    for start in starts:
        end = min(start + 8.0, duration_total - 0.5)
        if end - start >= 6:
            windows.append((start, end))
    while len(windows) < len(COMBOS):
        windows.append((2.0, 12.0))

    work = ASSETS / "demo_work"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir()

    rendered = []
    for index, (name, style_key, effect, music_id, protection) in enumerate(COMBOS):
        start, end = windows[index]
        style = P.STYLES[style_key]
        cues = P.build_cues(words, start, end)
        if not cues:
            print(f"{name}: нет слов, пропускаю")
            continue
        ass_path = work / f"{name}.ass"
        P.write_ass(ass_path, cues, style, "Arial Black", bool(style["uppercase"]))

        music = FX.music_path(music_id)
        out = DEMO / f"{name}.mp4"
        chain = FX.compose_video_chain(effect, protection)
        print(f"[{index + 1}/{len(COMBOS)}] {name}: субтитры={style_key}, эффект={effect}, музыка={music_id}, защита={protection}")
        P.render_clip(
            SOURCE, start, end, ass_path, out,
            workdir=work, ratio=ratio, has_audio=True,
            crf=22, fps=30, background=style.get("background", "blur"),
            effect=effect, protection=protection, music=music, music_volume=0.2,
        )
        rendered.append((name, out, f"{style['label'] if 'label' in style else style_key} · {FX.get_effect(effect).label} · {music_id}"))

    if not rendered:
        print("ничего не отрендерилось")
        return 1

    make_contact_sheet(rendered)
    total = sum(path.stat().st_size for _, path, _ in rendered) / 1024 / 1024
    print(f"готово: {len(rendered)} демо, {total:.1f} МБ -> {DEMO}")
    return 0


def make_contact_sheet(rendered: list[tuple[str, Path, str]]) -> None:
    """Один кадр с каждого клипа + подпись, сетка 3x2."""
    ff = S.ffmpeg_path()
    work = ASSETS / "demo_work"
    tiles = []
    for index, (name, path, caption) in enumerate(rendered):
        tile = work / f"tile_{index}.png"
        # кадр на 4-й секунде, внизу подпись
        label = caption.replace(":", "-")
        subprocess.run([
            ff, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", "4", "-i", str(path), "-frames:v", "1",
            "-vf",
            "scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
            f"pad=270:540:0:0:color=0x0a0a0f,"
            f"drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='{name}':x=10:y=496:fontsize=15:fontcolor=0xF5A524,"
            f"drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text='{label}':x=10:y=516:fontsize=12:fontcolor=0xbfbcb4",
            str(tile),
        ], check=True)
        tiles.append(tile)

    rows = 2
    cols = 3
    while len(tiles) < rows * cols:
        blank = work / f"tile_blank_{len(tiles)}.png"
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=0x0a0a0f:s=270x540", "-frames:v", "1", str(blank)], check=True)
        tiles.append(blank)

    args = [ff, "-hide_banner", "-loglevel", "error", "-y"]
    for tile in tiles[:rows * cols]:
        args += ["-i", str(tile)]
    layout = []
    for i in range(rows * cols):
        x = (i % cols) * 270
        y = (i // cols) * 540
        layout.append(f"{x}_{y}")
    filter_complex = "".join(f"[{i}:v]" for i in range(rows * cols)) + \
        f"xstack=inputs={rows * cols}:layout=" + "|".join(layout) + ":fill=black[out]"
    args += ["-filter_complex", filter_complex, "-map", "[out]", str(DEMO / "contact-sheet.png")]
    subprocess.run(args, check=True)
    print("contact sheet:", DEMO / "contact-sheet.png")


if __name__ == "__main__":
    sys.exit(main())
