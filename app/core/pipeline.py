# -*- coding: utf-8 -*-
"""Пайплайн нарезки: звук -> транскрипт -> моменты от LLM -> рендер вертикальных клипов.

Модуль не знает про HTTP и UI. На вход — файл и настройки, на выход — список клипов.
Прогресс и отмена идут через объект job (см. app/jobs.py).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import settings as S

W, H = 1080, 1920
MAX_LINE_CHARS = 19

# Готовые пресеты субтитров. Цвета ASS: &HAABBGGRR (альфа, синий, зелёный, красный).
STYLES: dict[str, dict] = {
    "classic": {
        "label": "Классика",
        "fontsize": 72, "primary": "&H00FFFFFF", "outline": "&H00000000",
        "outline_w": 9, "uppercase": True, "margin_v": 400, "bold": -1, "background": "blur",
    },
    "bold": {
        "label": "Жирный",
        "fontsize": 84, "primary": "&H0000FFFF", "outline": "&H00000000",
        "outline_w": 12, "uppercase": True, "margin_v": 430, "bold": -1, "background": "blur",
    },
    "clean": {
        "label": "Чистый",
        "fontsize": 64, "primary": "&H00FFFFFF", "outline": "&H00202020",
        "outline_w": 7, "uppercase": False, "margin_v": 380, "bold": 0, "background": "blur",
    },
    "neon": {
        "label": "Неон",
        "fontsize": 76, "primary": "&H00FFFF00", "outline": "&H00000000",
        "outline_w": 10, "uppercase": True, "margin_v": 400, "bold": -1, "background": "black",
    },
}


class PipelineError(RuntimeError):
    pass


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class RenderedClip:
    index: int
    file: str
    preview: str
    start: float
    end: float
    duration: float
    title: str = ""
    hook: str = ""
    hashtags: list = field(default_factory=list)
    reason: str = ""
    score: int = 0
    words: int = 0
    wpm: int = 0
    effect: str = "none"
    protection: str = "off"
    music: str = "none"

    def to_dict(self) -> dict:
        return {
            "id": f"{self.index}",
            "file": self.file,
            "preview": self.preview,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration": round(self.duration, 2),
            "title": self.title,
            "hook": self.hook,
            "hashtags": self.hashtags,
            "reason": self.reason,
            "score": self.score,
            "words": self.words,
            "wpm": self.wpm,
            "effect": self.effect,
            "protection": self.protection,
            "music": self.music,
        }


# ---------------------------------------------------------------- медиа

def _run(cmd: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise PipelineError(
            "не найден ffmpeg или ffprobe. Поставь ffmpeg в PATH или запусти собранную версию приложения"
        ) from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-1200:]
        raise PipelineError(f"ffmpeg упал: {tail}")
    return result


def probe(path: Path) -> dict:
    result = _run([
        S.ffprobe_path(), "-v", "error", "-show_entries",
        "stream=codec_type,width,height:format=duration",
        "-of", "json", str(path),
    ])
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    return {
        "width": int((video or {}).get("width") or 0),
        "height": int((video or {}).get("height") or 0),
        "has_video": video is not None,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
    }


def extract_audio(src: Path, wav: Path) -> None:
    _run([
        S.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(wav),
    ])


def load_wav_16k(path: Path):
    import numpy as np

    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getframerate() != 16000 or handle.getsampwidth() != 2:
            raise PipelineError("ожидал wav 16kHz mono 16bit")
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype("float32") / 32768.0


# ---------------------------------------------------------------- транскрипт

def transcribe(
    wav: Path,
    model_path: str,
    language: str,
    duration: float,
    *,
    on_progress: Callable[[float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    log: Callable[[str], None] = print,
) -> tuple[list[Segment], list[Word]]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise PipelineError("нет faster-whisper — поставь зависимости из requirements.txt") from exc

    try:
        import ctranslate2
        has_cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        has_cuda = False

    # CUDA включаем только по явному флагу: ctranslate2 видит GPU, но без cublas
    # падает или зависает на первом encode. CPU int8 работает везде.
    if has_cuda and not S.env_flag("CUTTER_USE_CUDA"):
        log("GPU найден, но CUDA выключена (включить: CUTTER_USE_CUDA=1). Считаю на CPU.")
        has_cuda = False

    device = "cuda" if has_cuda else "cpu"
    compute = "float16" if has_cuda else "int8"
    log(f"whisper: {Path(str(model_path)).name}, {device}/{compute}")

    def build(dev: str, comp: str):
        return WhisperModel(str(model_path), device=dev, compute_type=comp)

    model = build(device, compute)
    audio = load_wav_16k(wav)
    total = max(1.0, duration or (len(audio) / 16000.0))

    def decode(active):
        stream, info = active.transcribe(
            audio,
            language=None if language == "auto" else language,
            word_timestamps=True,
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
        )
        segments: list[Segment] = []
        words: list[Word] = []
        for seg in stream:
            if cancel and cancel():
                raise PipelineError("отменено")
            text = (seg.text or "").strip()
            if text:
                segments.append(Segment(float(seg.start), float(seg.end), text))
                for word in (seg.words or []):
                    token = (word.word or "").strip()
                    if token:
                        words.append(Word(float(word.start), float(word.end), token))
            if on_progress:
                on_progress(min(99.0, float(seg.end) / total * 100.0))
        return segments, words, info

    try:
        segments, words, info = decode(model)
    except Exception as exc:
        if device != "cuda":
            raise PipelineError(f"распознавание упало: {exc}") from exc
        log(f"CUDA не завелась ({str(exc)[:80]}), считаю на CPU")
        model = build("cpu", "int8")
        segments, words, info = decode(model)

    if not segments:
        raise PipelineError("в файле не нашлось речи — нечего нарезать")
    log(f"язык: {getattr(info, 'language', '?')}, фрагментов: {len(segments)}, слов: {len(words)}")
    return segments, words


# ---------------------------------------------------------------- субтитры

def ass_timestamp(seconds: float) -> str:
    total_cs = int(round(max(0.0, seconds) * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    return f"{total_s // 3600}:{(total_s // 60) % 60:02d}:{total_s % 60:02d}.{cs:02d}"


def wrap_caption(text: str) -> str:
    if len(text) <= MAX_LINE_CHARS:
        return text
    parts = text.split()
    if len(parts) < 2:
        return text
    best: tuple[int, str, str] | None = None
    for index in range(1, len(parts)):
        left, right = " ".join(parts[:index]), " ".join(parts[index:])
        score = max(len(left), len(right))
        if best is None or score < best[0]:
            best = (score, left, right)
    assert best
    return f"{best[1]}\\N{best[2]}"


def build_cues(words: Sequence[Word], start: float, end: float) -> list[tuple[float, float, str]]:
    picked = [w for w in words if w.end > start + 0.05 and w.start < end - 0.05]
    cues: list[tuple[float, float, str]] = []
    buffer: list[tuple[float, float, str]] = []

    def flush() -> None:
        nonlocal buffer
        if buffer:
            cues.append((buffer[0][0], max(x[1] for x in buffer), " ".join(x[2] for x in buffer)))
            buffer = []

    for word in picked:
        token = word.text.strip()
        if not token:
            continue
        ws = max(0.0, word.start - start)
        we = max(ws + 0.10, min(word.end - start, end - start))
        if buffer:
            current = " ".join(x[2] for x in buffer)
            if (ws - buffer[-1][1] > 0.6
                    or len(current) + len(token) + 1 > MAX_LINE_CHARS
                    or len(buffer) >= 4
                    or we - buffer[0][0] > 2.6):
                flush()
        buffer.append((ws, we, token))
        if token.endswith((".", "!", "?", "…")) and len(buffer) >= 2:
            flush()
    flush()

    clean: list[tuple[float, float, str]] = []
    for cue_start, cue_end, text in cues:
        text = re.sub(r"\s+", " ", text).strip().replace("\\", "").replace("{", "").replace("}", "")
        if text:
            clean.append((max(0.0, cue_start - 0.04), min(end - start, cue_end + 0.10), text))
    for index in range(len(clean) - 1):
        s, e, t = clean[index]
        nxt = clean[index + 1][0]
        if e > nxt - 0.02:
            clean[index] = (s, max(s + 0.05, nxt - 0.02), t)
    return [c for c in clean if c[1] > c[0]]


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary},{primary2},{outline},&H80000000,{bold},0,0,0,100,100,0,0,1,{outline_w},0,2,70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass(path: Path, cues: Iterable[tuple[float, float, str]], style: dict, font: str, uppercase: bool) -> None:
    header = ASS_HEADER.format(
        w=W, h=H, font=font, size=style["fontsize"], primary=style["primary"],
        primary2=style["primary"], outline=style["outline"], bold=style["bold"],
        outline_w=style["outline_w"], margin_v=style["margin_v"],
    )
    lines = [header]
    for start, end, text in cues:
        if end <= start:
            continue
        shown = text.upper() if uppercase else text
        lines.append(
            f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Default,,0,0,0,,"
            f"{{\\fad(90,50)}}{wrap_caption(shown)}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- рендер

def build_video_filter(ass_name: str, ratio: float, background: str,
                       chain: str = "", speed: float = 1.0) -> str:
    """Собирает граф кадра: кадрирование -> эффекты/защита -> скорость -> субтитры."""
    extra = ""
    if chain:
        extra += f",{chain}"
    if speed and abs(speed - 1.0) > 0.001:
        extra += f",setpts=PTS/{speed:.4f}"
    if extra:
        # защита/зум меняют размер на пару пикселей — возвращаем ровно 1080x1920
        extra += f",scale={W}:{H}:flags=lanczos,setsar=1"

    if ratio <= 0.62 or background == "crop":
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}{extra},ass={ass_name}[v]")
    if background == "black":
        return (f"[0:v]scale={W}:-2:flags=lanczos,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0a0a0f{extra},ass={ass_name}[v]")
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"gblur=sigma=42,eq=brightness=-0.12[bg2];"
        f"[fg]scale={W}:-2:flags=lanczos[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2{extra},ass={ass_name}[v]"
    )


def render_clip(
    src: Path,
    start: float,
    end: float,
    ass_path: Path,
    out_path: Path,
    *,
    workdir: Path,
    ratio: float,
    has_audio: bool,
    crf: int,
    fps: int,
    background: str,
    effect: str = "none",
    protection: str = "off",
    music: Path | None = None,
    music_volume: float = 0.18,
) -> None:
    from . import effects as FX

    duration = max(0.5, end - start)
    chain = FX.compose_video_chain(effect, protection)
    _, protection_cfg = FX.protection_filter(protection)
    video_graph = build_video_filter(ass_path.name, ratio, background,
                                     chain=chain, speed=protection_cfg.speed or 1.0)
    audio_args, audio_graph = FX.build_audio_graph(
        has_source_audio=has_audio, music=music, music_volume=music_volume,
    )

    graph = video_graph if not audio_graph else f"{video_graph};{audio_graph}"
    cmd = [
        S.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src.resolve()),
    ]
    cmd += audio_args
    cmd += ["-filter_complex", graph, "-map", "[v]"]
    if audio_graph:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-shortest"]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-movflags", "+faststart",
        str(out_path.resolve()),
    ]
    _run(cmd, cwd=workdir)


def make_preview(video: Path, image: Path, at: float = 1.0) -> None:
    _run([
        S.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0.1, at):.2f}", "-i", str(video),
        "-frames:v", "1", "-vf", "scale=384:-2", str(image),
    ])


# ---------------------------------------------------------------- основной сценарий

@dataclass
class SimpleMoment:
    """Момент без LLM — для демо-режима и тестов пайплайна."""
    start: float
    end: float
    hook: str = ""
    title: str = ""
    hashtags: list = field(default_factory=list)
    reason: str = ""
    score: int = 0


def fallback_moments(segments: Sequence[Segment], n: int, min_dur: float, max_dur: float, duration: float) -> list[SimpleMoment]:
    """Демо-режим: берём самые длинные реплики и растягиваем до целевой длины."""
    picked: list[SimpleMoment] = []
    for seg in sorted(segments, key=lambda s: len(s.text), reverse=True):
        if len(picked) >= n:
            break
        wanted = min(max_dur, max(min_dur, (seg.end - seg.start) + 8.0))
        start = max(0.0, seg.start - 1.5)
        end = min(duration, start + wanted)
        start = max(0.0, end - wanted)
        if end - start < min_dur:
            continue
        if any(not (end <= m.start or start >= m.end) for m in picked):
            # пересекается с уже выбранным — сдвигаем окно вправо и пробуем ещё раз
            shift = max(m.end for m in picked if not (end <= m.start or start >= m.end)) + 0.4
            start, end = shift, min(duration, shift + wanted)
            if end - start < min_dur:
                continue
            if any(not (end <= m.start or start >= m.end) for m in picked):
                continue
        title = re.sub(r"\s+", " ", seg.text).strip()[:70]
        picked.append(SimpleMoment(start=start, end=end, hook=title, title=title, score=55))
    return sorted(picked, key=lambda m: m.start)


def analyze(
    *,
    job,
    source: Path,
    workdir: Path,
    settings: dict,
    on_log: Callable[[str], None] | None = None,
    progress_offset: float = 0.0,
) -> dict:
    """Полный цикл для одного исходника. Возвращает результат для job.result."""
    log = on_log or job.log
    floor = max(0.0, min(95.0, float(progress_offset)))

    def prog(percent: float, stage: str = "", message: str = "") -> None:
        """Прогресс с учётом уже потраченного времени на скачивание."""
        job.set_progress(floor + (100.0 - floor) * percent / 100.0, stage, message)

    log(f"источник: {source.name}")

    info = probe(source)
    if not info["has_video"]:
        raise PipelineError("в файле нет видеодорожки")
    duration = info["duration"]
    if duration < 5:
        raise PipelineError("слишком короткое видео — меньше 5 секунд")
    log(f"длительность: {int(duration // 60)}:{int(duration % 60):02d}, "
        f"{info['width']}x{info['height']}")

    workdir.mkdir(parents=True, exist_ok=True)
    clips_dir = workdir / "clips"
    clips_dir.mkdir(exist_ok=True)
    previews_dir = workdir / "previews"
    previews_dir.mkdir(exist_ok=True)
    work = workdir / ".work"
    work.mkdir(exist_ok=True)

    prog(3, "звук", "вытаскиваю аудиодорожку")
    wav = work / "audio.wav"
    extract_audio(source, wav)

    model_path, installed = S.whisper_model_path(settings)
    if not installed:
        raise PipelineError(
            f"модель whisper «{settings['stt']['whisper_size']}» не скачана. "
            "Скачай её в Настройках (кнопка «Скачать модель»)"
        )

    prog(8, "распознавание", "слушаю исходник")
    started = time.time()

    def stt_progress(pct: float) -> None:
        prog(8 + pct * 0.42, "распознавание", f"расшифровка {pct:.0f}%")

    segments, words = transcribe(
        wav, model_path, settings["stt"]["language"], duration,
        on_progress=stt_progress, cancel=job.cancelled, log=log,
    )
    log(f"расшифровка заняла {time.time() - started:.0f} с")
    if job.cancelled():
        raise PipelineError("отменено")

    prog(52, "выбор моментов", "LLM ищет, что залетит")
    render_cfg = settings["render"]

    if S.env_flag("CUTTER_FAKE_LLM"):
        # демо-режим: без API, детерминированный выбор. Нужен для проверки пайплайна и UI.
        log("демо-режим: моменты выбираются без LLM (CUTTER_FAKE_LLM)")
        moments = fallback_moments(segments, int(render_cfg["clips"]), float(render_cfg["min_dur"]),
                                   float(render_cfg["max_dur"]), duration)
    else:
        from . import llm  # ленивый импорт: модуль не нужен для рендера

        cfg = llm.LLMConfig(
            provider=settings["llm"]["provider"],
            model=settings["llm"]["model"],
            api_key=S.resolve_key(settings, settings["llm"]["provider"]),
            base_url=settings["llm"].get("base_url", ""),
            temperature=float(settings["llm"].get("temperature", 1.1)),
            fallback=bool(settings["llm"].get("fallback", True)),
            keys={k: v for k, v in (settings["llm"].get("keys") or {}).items() if v},
        )
        try:
            moments = llm.pick_moments(
                [(s.start, s.end, s.text) for s in segments],
                n=int(render_cfg["clips"]),
                min_dur=float(render_cfg["min_dur"]),
                max_dur=float(render_cfg["max_dur"]),
                duration=duration,
                cfg=cfg,
                log=log,
            )
        except llm.LLMError as exc:
            raise PipelineError(f"LLM не смогла выбрать моменты: {exc}") from exc

    if not moments:
        raise PipelineError("LLM не вернула ни одного подходящего момента")
    log(f"выбрано моментов: {len(moments)}")
    if job.cancelled():
        raise PipelineError("отменено")

    style_key = render_cfg.get("style", "classic")
    style = STYLES.get(style_key, STYLES["classic"])
    effect_key = render_cfg.get("effect", "none")
    protection_key = render_cfg.get("protection", "off")
    music_id = render_cfg.get("music", "none")
    music_volume = float(render_cfg.get("music_volume", 0.18))

    from . import effects as FX
    music_file = FX.music_path(music_id)
    if music_id and music_id != FX.MUSIC_ID_NONE and music_file is None:
        log(f"музыка «{music_id}» не найдена — рендерю без неё")
    elif music_file is not None:
        log(f"подложка: {music_file.name}, громкость {int(music_volume * 100)}%")
    if effect_key != "none":
        log(f"эффект кадра: {FX.get_effect(effect_key).label}")
    if protection_key != "off":
        log(f"защита от повторов: {protection_key}")

    ratio = (info["width"] / info["height"]) if info["height"] else 16 / 9
    clips: list[RenderedClip] = []

    for index, moment in enumerate(moments, start=1):
        if job.cancelled():
            raise PipelineError("отменено")
        share = 56.0 / max(1, len(moments))
        prog(54 + share * (index - 1), "рендер", f"клип {index} из {len(moments)}")

        ass_path = work / f"clip_{index:02d}.ass"
        cues = build_cues(words, moment.start, moment.end)
        if not cues:
            log(f"клип {index}: в выбранном диапазоне нет слов, пропускаю")
            continue
        write_ass(ass_path, cues, style, "Arial Black", bool(style["uppercase"]))

        file_name = f"clip_{index:02d}.mp4"
        preview_name = f"clip_{index:02d}.jpg"
        render_clip(
            source, moment.start, moment.end, ass_path,
            clips_dir / file_name,
            workdir=work, ratio=ratio, has_audio=info["has_audio"],
            crf=int(render_cfg.get("crf", 20)), fps=int(render_cfg.get("fps", 30)),
            background=style.get("background", "blur"),
            effect=effect_key, protection=protection_key,
            music=music_file, music_volume=music_volume,
        )
        make_preview(clips_dir / file_name, previews_dir / preview_name)

        clip_words = [w for w in words if w.end > moment.start and w.start < moment.end]
        duration = max(0.1, moment.end - moment.start)
        clip = RenderedClip(
            index=index, file=file_name, preview=preview_name,
            start=moment.start, end=moment.end, duration=moment.end - moment.start,
            title=moment.title, hook=moment.hook, hashtags=list(moment.hashtags),
            reason=moment.reason, score=int(moment.score),
            words=len(clip_words),
            wpm=int(round(len(clip_words) / duration * 60)),
            effect=effect_key, protection=protection_key,
            music=music_id if music_file is not None else "none",
        )
        clips.append(clip)
        log(f"клип {index}: {int(clip.duration)}с, {clip.words} сл., {clip.title[:50] or 'без заголовка'}")

    if not clips:
        raise PipelineError("ни один момент не удалось отрендерить")

    if not S.env_flag("CUTTER_KEEP_TEMP"):
        shutil.rmtree(work, ignore_errors=True)

    manifest = {
        "source": source.name,
        "duration": round(duration, 2),
        "style": style_key,
        "clips": [c.to_dict() for c in clips],
    }
    (workdir / "clips.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    job.set_progress(100, "готово", f"{len(clips)} клипов")
    return {"clips_dir": str(clips_dir), "previews_dir": str(previews_dir), "clips": [c.to_dict() for c in clips]}
