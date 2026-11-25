# -*- coding: utf-8 -*-
"""Рендерит тестовые видео с новыми функциями Cutter в demo/."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core import effects as FX  # noqa: E402
from app.core import integrator  # noqa: E402
from app.core import pipeline as P  # noqa: E402
from app.core import settings as S  # noqa: E402
from app.core import tts  # noqa: E402

DEMO = ROOT / "demo"


def _make_base_clip(out: Path, *, duration: float = 10.0, text_label: str = "CUTTER 1.1") -> None:
    ff = S.ffmpeg_path()
    subprocess.run([
        ff, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i",
        "gradients=s=1080x1920:rate=30:c0=0x0b1020:c1=0x16324f:c2=0x0e7490:c3=0x111827:n=4:speed=0.01:type=linear",
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration:.2f}",
        "-t", f"{duration:.2f}",
        "-vf", f"drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='{text_label}':x=(w-text_w)/2:y=260:fontsize=54:fontcolor=white@0.85",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        str(out),
    ], check=True)


def render_parts_demo(work: Path) -> Path:
    src = work / "parts_src.mp4"
    _make_base_clip(src, duration=14.0, text_label="PARTS SPLIT DEMO")
    segs = [
        P.Segment(0.0, 4.5, "Разбиваем длинное видео на логические части по фразам."),
        P.Segment(4.8, 9.2, "Каждая часть не обрывается на полуслове и получает субтитры."),
        P.Segment(9.6, 13.8, "Работает даже без API-ключа и внешних облаков."),
    ]
    all_words = [
        P.Word(s.start + i * 0.45, s.start + (i + 1) * 0.42, tok)
        for s in segs
        for i, tok in enumerate(s.text.split())
    ]
    parts = P.split_into_parts(segs, n=2, min_dur=4.0, max_dur=7.5, duration=14.0, words=all_words)
    part = parts[0]
    cues = P.build_cues(all_words, part.start, part.end)
    ass = work / "parts.ass"
    style = P.STYLES["classic"]
    P.write_ass(ass, cues, style, "Arial Black", bool(style["uppercase"]))
    out = DEMO / "demo_07_parts_split.mp4"
    P.render_clip(
        src, part.start, part.end, ass, out,
        workdir=work, ratio=1080 / 1920, has_audio=True,
        crf=21, fps=30, background="crop", effect="cinema",
        music=FX.music_path("lofi-chill"), music_volume=0.22,
    )
    return out


def render_brainrot_demo(work: Path) -> Path:
    ref = Path.home() / "Downloads" / "китайскиеистории.mp4"
    src = work / "drama_src.mp4"
    if ref.is_file():
        subprocess.run([
            S.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", "12", "-t", "12", "-i", str(ref),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", str(src),
        ], check=True)
    else:
        _make_base_clip(src, duration=12.0, text_label="AI DRAMA DUB")

    dub_lines = [
        tts.DubSegment(0.0, 5.2, "Оказалось, незадолго до этого парень проверил прогноз и увидел, что придет сильнейший град."),
        tts.DubSegment(5.4, 11.8, "Он сразу предупредил всю деревню, чтобы спасти урожай, над которым все трудились целый год."),
    ]
    dub = tts.build_dub_track(
        dub_lines, clip_start=0.0, clip_end=12.0,
        workdir=work / "tts", voice_id="canon_bright", log=print,
    )
    words = [P.Word(s, e, t) for s, e, t in dub.words]
    cues = P.build_cues(words, 0.0, 12.0)
    ass = work / "drama.ass"
    style = P.STYLES["drama"]
    P.write_ass(ass, cues, style, "Arial Black", bool(style["uppercase"]))

    info = P.probe(src)
    ratio = (info["width"] / info["height"]) if info["height"] else 9 / 16
    out = DEMO / "demo_08_brainrot_drama.mp4"
    P.render_clip(
        src, 0.0, 12.0, ass, out,
        workdir=work, ratio=ratio, has_audio=info["has_audio"],
        crf=21, fps=30, background="black", effect="none",
        music=FX.music_path("brainrot-twist"), music_volume=0.20,
        dub=dub.audio_path, source_volume=0.05,
    )
    return out


def render_integration_demo(work: Path) -> Path:
    base = work / "ad_base.mp4"
    ad_vid = work / "ad_vpn.mp4"
    banner = work / "ad_banner.png"
    _make_base_clip(base, duration=8.0, text_label="MAIN CLIP + AD")
    ff = S.ffmpeg_path()

    # Ролик-интеграция (2.5с)
    subprocess.run([
        ff, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=0x0f172a:s=1080x1920:d=2.5",
        "-f", "lavfi", "-i", "sine=f=520:d=2.5",
        "-vf",
        "drawbox=x=90:y=720:w=900:h=480:color=0x38bdf8@0.18:t=fill,"
        "drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='FAST VPN':x=(w-text_w)/2:y=860:fontsize=72:fontcolor=0x38bdf8,"
        "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text='YOUTUBE BEZ TORMOZOV':x=(w-text_w)/2:y=970:fontsize=40:fontcolor=white",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(ad_vid),
    ], check=True)

    # Баннер-плашка (PNG)
    subprocess.run([
        ff, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=0x111827:s=760x130",
        "-vf",
        "drawbox=x=0:y=0:w=760:h=130:color=0x22d3ee:t=4,"
        "drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='PROMO -50%  @CUTTER_VPN':expansion=none:x=(w-text_w)/2:y=(h-text_h)/2:fontsize=34:fontcolor=white",
        "-frames:v", "1", str(banner),
    ], check=True)

    mid = work / "with_insert.mp4"
    integrator.apply_integration(
        base, integrator.IntegrationSpec(ad_vid, mode="insert", position="after_hook", hook_sec=2.5),
        mid, log=print,
    )
    out = DEMO / "demo_09_ad_integration.mp4"
    integrator.apply_integration(
        mid, integrator.IntegrationSpec(banner, mode="overlay", position="bottom", start_sec=5.5, duration_sec=4.0, scale_pct=78),
        out, log=print,
    )
    return out


def main() -> int:
    DEMO.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="cutter_new_demos_"))
    try:
        p1 = render_parts_demo(work)
        p2 = render_brainrot_demo(work)
        p3 = render_integration_demo(work)
        for p in (p1, p2, p3):
            info = P.probe(p)
            print(f"OK {p.name}: {info['duration']:.2f}s, {info['width']}x{info['height']}, {p.stat().st_size // 1024} KB")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
