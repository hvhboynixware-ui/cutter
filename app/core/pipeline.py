# -*- coding: utf-8 -*-
"""Пайплайн нарезки: звук -> транскрипт -> моменты от LLM -> рендер вертикальных клипов.

Модуль не знает про HTTP и UI. На вход — файл и настройки, на выход — список клипов.
Прогресс и отмена идут через объект job (см. app/jobs.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Sequence

from . import settings as S

if TYPE_CHECKING:
    from . import llm

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
    "drama": {
        "label": "Дорама",
        "fontsize": 48, "primary": "&H00FFFFFF", "outline": "&H00000000",
        "outline_w": 4, "uppercase": False, "margin_v": 250, "bold": -1, "background": "black",
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


# ---------------------------------------------------------------- рендер и ускорение

_DETECTED_ENCODER: str | None = None
_ENCODER_LOCK = threading.Lock()


def detect_video_encoder(preferred: str = "auto") -> str:
    """Выбирает рабочий видеокодек (nvenc/qsv/amf или cpu libx264) с проверкой на 1 кадре."""
    pref = (preferred or "auto").strip().lower()
    if pref == "cpu":
        return "cpu"

    global _DETECTED_ENCODER
    if pref == "auto" and _DETECTED_ENCODER is not None:
        return _DETECTED_ENCODER

    candidates = ["nvenc", "qsv", "amf"] if pref == "auto" else [pref]
    codec_map = {"nvenc": "h264_nvenc", "qsv": "h264_qsv", "amf": "h264_amf"}
    chosen = "cpu"
    try:
        probe_res = subprocess.run(
            [S.ffmpeg_path(), "-hide_banner", "-encoders"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
        )
        encoders_out = probe_res.stdout or ""
    except Exception:
        encoders_out = ""

    for name in candidates:
        codec = codec_map.get(name)
        if not codec or codec not in encoders_out:
            continue
        try:
            test = subprocess.run(
                [
                    S.ffmpeg_path(), "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=black:s=128x128:d=0.05",
                    "-frames:v", "1", "-c:v", codec, "-f", "null", "-",
                ],
                capture_output=True, timeout=8,
            )
            if test.returncode == 0:
                chosen = name
                break
        except Exception:
            continue

    if pref == "auto":
        with _ENCODER_LOCK:
            _DETECTED_ENCODER = chosen
    return chosen


def encoder_args(encoder: str, crf: int) -> list[str]:
    if encoder == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
    if encoder == "qsv":
        return ["-c:v", "h264_qsv", "-global_quality", str(crf)]
    if encoder == "amf":
        return ["-c:v", "h264_amf", "-quality", "speed", "-qp_i", str(crf), "-qp_p", str(crf)]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf)]


def build_video_filter(ass_name: str, ratio: float, background: str,
                       chain: str = "", speed: float = 1.0) -> str:
    """Собирает граф кадра: кадрирование -> эффекты/защита -> скорость -> субтитры."""
    extra = ""
    if chain:
        extra += f",{chain}"
    if speed and abs(speed - 1.0) > 0.001:
        extra += f",setpts=PTS/{speed:.4f}"

    subs = f",ass={ass_name}" if ass_name else ""

    if background == "original":
        scale_fix = ",scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1"
        if extra or scale_fix:
            return f"[0:v]scale='min(1920,iw)':'min(1920,ih)':force_original_aspect_ratio=decrease{extra}{scale_fix}{subs}[v]"
        return f"[0:v]null{subs}[v]"

    if extra:
        # защита/зум меняют размер на пару пикселей — возвращаем ровно 1080x1920
        extra += f",scale={W}:{H}:flags=lanczos,setsar=1"

    if ratio <= 0.62 or background == "crop":
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}{extra}{subs}[v]")
    if background == "black":
        return (f"[0:v]scale={W}:-2:flags=lanczos,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0a0a0f{extra}{subs}[v]")
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"gblur=sigma=42,eq=brightness=-0.12[bg2];"
        f"[fg]scale={W}:-2:flags=lanczos[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2{extra}{subs}[v]"
    )


def render_clip(
    src: Path,
    start: float,
    end: float,
    ass_path: Path | None,
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
    dub: Path | None = None,
    source_volume: float = 1.0,
    encoder: str = "cpu",
) -> None:
    from . import effects as FX

    duration = max(0.5, end - start)
    chain = FX.compose_video_chain(effect, protection)
    _, protection_cfg = FX.protection_filter(protection)
    ass_name = ass_path.name if ass_path is not None else ""
    video_graph = build_video_filter(ass_name, ratio, background,
                                     chain=chain, speed=protection_cfg.speed or 1.0)
    audio_args, audio_graph = FX.build_audio_graph(
        has_source_audio=has_audio, music=music, music_volume=music_volume,
        dub=dub, source_volume=source_volume,
    )

    graph = video_graph if not audio_graph else f"{video_graph};{audio_graph}"
    base_cmd = [
        S.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src.resolve()),
    ]
    base_cmd += audio_args
    base_cmd += ["-filter_complex", graph, "-map", "[v]"]
    if audio_graph:
        base_cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-shortest"]

    def build_full(enc_name: str) -> list[str]:
        return base_cmd + encoder_args(enc_name, crf) + [
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-movflags", "+faststart",
            str(out_path.resolve()),
        ]

    try:
        _run(build_full(encoder), cwd=workdir)
    except PipelineError:
        if encoder == "cpu":
            raise
        # Откат на libx264, если аппаратный энкодер не принял профиль/фильтр.
        _run(build_full("cpu"), cwd=workdir)


def make_preview(video: Path, image: Path, at: float = 1.0) -> None:
    _run([
        S.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0.1, at):.2f}", "-i", str(video),
        "-frames:v", "1", "-vf", "scale=384:-2", str(image),
    ])


# ---------------------------------------------------------------- кэш транскрипта

def _transcript_cache_key(source: Path, model_size: str, language: str) -> str:
    h = hashlib.sha256()
    stat = source.stat()
    h.update(f"{stat.st_size}:{model_size}:{language}".encode("utf-8"))
    chunk = 1 << 20
    with open(source, "rb") as handle:
        h.update(handle.read(chunk))
        if stat.st_size > chunk:
            handle.seek(max(0, stat.st_size - chunk))
            h.update(handle.read(chunk))
    return h.hexdigest()[:24]


def _transcript_cache_path(key: str) -> Path:
    folder = S.DATA_DIR / "cache" / "transcripts"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{key}.json"


def _load_cached_transcript(key: str) -> tuple[list[Segment], list[Word]] | None:
    path = _transcript_cache_path(key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        segments = [Segment(float(s[0]), float(s[1]), str(s[2])) for s in data.get("segments") or []]
        words = [Word(float(w[0]), float(w[1]), str(w[2])) for w in data.get("words") or []]
        if not segments:
            return None
        return segments, words
    except Exception:
        return None


def _save_cached_transcript(key: str, segments: Sequence[Segment], words: Sequence[Word]) -> None:
    try:
        path = _transcript_cache_path(key)
        payload = {
            "segments": [[round(s.start, 3), round(s.end, 3), s.text] for s in segments],
            "words": [[round(w.start, 3), round(w.end, 3), w.text] for w in words],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- основной сценарий

@dataclass
class SimpleMoment:
    """Момент без LLM — для демо-режима, нарезки на части и тестов пайплайна."""
    start: float
    end: float
    hook: str = ""
    title: str = ""
    hashtags: list = field(default_factory=list)
    reason: str = ""
    score: int = 0


def split_into_parts(
    segments: Sequence[Segment],
    *,
    n: int,
    min_dur: float,
    max_dur: float,
    duration: float,
    words: Sequence[Word] = (),
) -> list[SimpleMoment]:
    """Последовательно режет видео на части по границам фраз и пауз, не обрывая слова."""
    total = float(duration or 0.0)
    if total <= 0.5:
        return []
    min_d = max(4.0, float(min_dur or 15.0))
    max_d = max(min_d, float(max_dur or min_d))
    target_d = (min_d + max_d) / 2.0
    limit = int(n) if int(n or 0) > 0 else 10_000

    ordered = sorted((s for s in segments if s.end > s.start), key=lambda s: s.start)
    parts: list[SimpleMoment] = []

    if not ordered:
        cursor = 0.0
        idx = 1
        while cursor < total - 1.0 and len(parts) < limit:
            end = min(total, cursor + target_d)
            if total - end < max(3.0, min_d * 0.45) and end < total:
                end = total
            if end - cursor < 3.0:
                break
            parts.append(SimpleMoment(
                start=round(cursor, 3), end=round(end, 3),
                title=f"Часть {idx}", hook="", reason="равномерное разбиение", score=80,
            ))
            cursor = end
            idx += 1
        return parts

    # Если отдельная длинная реплика превышает max_d — дробим её по словам.
    units: list[Segment] = []
    for seg in ordered:
        if seg.end - seg.start <= max_d:
            units.append(seg)
            continue
        seg_words = [w for w in words if w.end > seg.start + 0.02 and w.start < seg.end - 0.02]
        if seg_words:
            buf_s = seg_words[0].start
            buf_tokens: list[str] = []
            for w in seg_words:
                if buf_tokens and (w.end - buf_s > max_d):
                    units.append(Segment(buf_s, w.start, " ".join(buf_tokens)))
                    buf_s = w.start
                    buf_tokens = []
                buf_tokens.append(w.text)
                is_punct = w.text.endswith((".", "!", "?", "…"))
                if (is_punct and w.end - buf_s >= min_d) or (w.end - buf_s >= target_d):
                    units.append(Segment(buf_s, w.end, " ".join(buf_tokens)))
                    buf_s = w.end
                    buf_tokens = []
            if buf_tokens:
                units.append(Segment(buf_s, seg_words[-1].end, " ".join(buf_tokens)))
        else:
            cur = seg.start
            while cur < seg.end - 0.5:
                nxt = min(seg.end, cur + target_d)
                units.append(Segment(cur, nxt, seg.text))
                cur = nxt

    cur_start = max(0.0, units[0].start if units[0].start < 2.5 else 0.0)
    cur_texts: list[str] = []
    last_end = cur_start

    def flush(end_t: float) -> None:
        nonlocal cur_start, cur_texts, last_end
        if end_t - cur_start < 2.0:
            return
        idx = len(parts) + 1
        snippet = re.sub(r"\s+", " ", " ".join(cur_texts)).strip()
        short = (snippet[:44].rstrip() + "…") if len(snippet) > 44 else snippet
        title = f"Часть {idx} · {short}" if short else f"Часть {idx}"
        parts.append(SimpleMoment(
            start=round(cur_start, 3),
            end=round(min(total, end_t), 3),
            hook=snippet[:120],
            title=title,
            reason="разбиение на части по фразам",
            score=80,
        ))
        cur_texts = []

    for idx, seg in enumerate(units):
        if len(parts) >= limit:
            break
        if cur_texts and (seg.end - cur_start > max_d) and (last_end - cur_start >= min_d):
            flush(last_end)
            cur_start = max(last_end, seg.start - 0.1)
        if not cur_texts and seg.start > cur_start + 3.0:
            cur_start = max(0.0, seg.start - 0.2)
        cur_texts.append(seg.text)
        last_end = seg.end
        dur = last_end - cur_start

        next_seg = units[idx + 1] if idx + 1 < len(units) else None
        gap = (next_seg.start - seg.end) if next_seg else 99.0
        ends_sentence = seg.text.rstrip().endswith((".", "!", "?", "…"))

        should_cut = False
        if dur >= max_d:
            should_cut = True
        elif dur >= target_d and (ends_sentence or gap >= 0.35 or (next_seg and next_seg.end - cur_start > max_d)):
            should_cut = True
        elif dur >= min_d and ends_sentence and gap >= 0.45:
            should_cut = True

        if should_cut:
            cut_at = min(total, seg.end + min(0.2, max(0.0, gap * 0.4)))
            flush(cut_at)
            if next_seg:
                cur_start = max(cut_at, next_seg.start - min(0.15, max(0.0, gap * 0.3)))

    if cur_texts and len(parts) < limit:
        tail_end = min(total, last_end + 0.25)
        if tail_end - cur_start < max(3.0, min_d * 0.4) and parts and (tail_end - parts[-1].start) <= max_d * 1.25:
            prev = parts[-1]
            prev.end = round(tail_end, 3)
        elif tail_end - cur_start >= 2.5:
            flush(tail_end)

    return parts


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


def _make_llm_cfg(settings: dict) -> "llm.LLMConfig":
    from . import llm

    llm_s = settings.get("llm") or {}
    return llm.LLMConfig(
        provider=llm_s.get("provider", "deepseek"),
        model=llm_s.get("model", "deepseek-flash"),
        api_key=S.resolve_key(settings, llm_s.get("provider", "deepseek")),
        base_url=llm_s.get("base_url", ""),
        account_id=str(llm_s.get("account_id") or ""),
        temperature=float(llm_s.get("temperature", 1.1)),
        fallback=bool(llm_s.get("fallback", True)),
        keys={k: v for k, v in (llm_s.get("keys") or {}).items() if v},
    )


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

    render_cfg = settings["render"]
    mode = str(render_cfg.get("mode") or "moments").strip().lower()
    use_subs = bool(render_cfg.get("subtitles", True))
    whisper_size = settings["stt"]["whisper_size"]
    stt_lang = settings["stt"]["language"]

    model_path, installed = S.whisper_model_path(settings)
    if not installed:
        raise PipelineError(
            f"модель whisper «{whisper_size}» не скачана. "
            "Скачай её в Настройках (кнопка «Скачать модель»)"
        )

    cache_key = _transcript_cache_key(source, whisper_size, stt_lang)
    cached = _load_cached_transcript(cache_key)
    if cached is not None:
        segments, words = cached
        log(f"транскрипт взят из кэша ({len(segments)} фраз, {len(words)} сл.)")
        prog(48, "распознавание", "транскрипт из кэша")
    else:
        prog(3, "звук", "вытаскиваю аудиодорожку")
        wav = work / "audio.wav"
        extract_audio(source, wav)

        prog(8, "распознавание", "слушаю исходник")
        started = time.time()

        def stt_progress(pct: float) -> None:
            prog(8 + pct * 0.42, "распознавание", f"расшифровка {pct:.0f}%")

        segments, words = transcribe(
            wav, model_path, stt_lang, duration,
            on_progress=stt_progress, cancel=job.cancelled, log=log,
        )
        log(f"расшифровка заняла {time.time() - started:.0f} с")
        _save_cached_transcript(cache_key, segments, words)

    if job.cancelled():
        raise PipelineError("отменено")

    clip_n = int(render_cfg.get("clips", 5))
    min_dur = float(render_cfg.get("min_dur", 18))
    max_dur = float(render_cfg.get("max_dur", 60))

    if mode == "parts":
        prog(52, "выбор моментов", "разбиваю видео на части по фразам")
        moments = split_into_parts(
            segments, n=clip_n, min_dur=min_dur, max_dur=max_dur, duration=duration, words=words,
        )
        log(f"разбиение на части: {len(moments)} шт.")
    elif mode == "brainrot":
        prog(52, "выбор моментов", "готовлю сценарий китайской истории")
        if clip_n <= 0:
            moments = split_into_parts(
                segments, n=0, min_dur=min_dur, max_dur=max_dur, duration=duration, words=words,
            )
        elif S.env_flag("CUTTER_FAKE_LLM"):
            moments = split_into_parts(
                segments, n=clip_n, min_dur=min_dur, max_dur=max_dur, duration=duration, words=words,
            )
        else:
            # В режиме историй части идут по хронологии (если clip_n >= 1, берём либо LLM, либо части).
            moments = split_into_parts(
                segments, n=clip_n, min_dur=min_dur, max_dur=max_dur, duration=duration, words=words,
            )
        log(f"режим дорамы: {len(moments)} частей под переозвучку")
    elif S.env_flag("CUTTER_FAKE_LLM"):
        prog(52, "выбор моментов", "демо-режим выбора моментов")
        log("демо-режим: моменты выбираются без LLM (CUTTER_FAKE_LLM)")
        moments = fallback_moments(segments, max(1, clip_n), min_dur, max_dur, duration)
    else:
        prog(52, "выбор моментов", "LLM ищет, что залетит")
        from . import llm
        cfg = _make_llm_cfg(settings)
        try:
            moments = llm.pick_moments(
                [(s.start, s.end, s.text) for s in segments],
                n=max(1, clip_n),
                min_dur=min_dur,
                max_dur=max_dur,
                duration=duration,
                cfg=cfg,
                log=log,
            )
        except llm.LLMError as exc:
            raise PipelineError(f"LLM не смогла выбрать моменты: {exc}") from exc

    if not moments:
        raise PipelineError("не удалось сформировать ни одного фрагмента")
    log(f"выбрано фрагментов: {len(moments)}")
    if job.cancelled():
        raise PipelineError("отменено")

    # В режиме brainrot переписываем реплики (если включено) и готовим TTS.
    dub_segments_all: list[tuple[float, float, str]] = [(s.start, s.end, s.text) for s in segments]
    if mode == "brainrot" and bool(render_cfg.get("brainrot_rewrite", True)) and not S.env_flag("CUTTER_FAKE_LLM"):
        try:
            from . import llm
            prog(53, "выбор моментов", "переписываю текст в стиль пересказа дорамы")
            cfg = _make_llm_cfg(settings)
            rewritten = llm.rewrite_for_brainrot(
                dub_segments_all,
                cfg=cfg,
                target_lang="en" if str(render_cfg.get("voice") or "").endswith("_en") else "ru",
                log=log,
            )
            if rewritten:
                dub_segments_all = rewritten
        except Exception as exc:
            log(f"пересказ LLM пропущен ({str(exc)[:100]}), озвучиваю исходный текст")

    style_key = render_cfg.get("style", "classic")
    style = STYLES.get(style_key, STYLES["classic"])
    framing = str(render_cfg.get("framing") or "auto").strip().lower()
    bg_mode = style.get("background", "blur") if framing == "auto" else framing

    effect_key = render_cfg.get("effect", "none")
    protection_key = render_cfg.get("protection", "off")
    music_id = render_cfg.get("music", "none")
    if mode == "brainrot" and music_id == "none":
        music_id = "drama-guzheng"
    music_volume = float(render_cfg.get("music_volume", 0.18))
    voice_id = str(render_cfg.get("voice") or "canon_bright")
    orig_audio_vol = float(render_cfg.get("orig_audio_vol", 0.08))
    integration_id = str(render_cfg.get("integration_id") or "").strip()

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

    encoder = detect_video_encoder(str(render_cfg.get("encoder") or "auto"))
    log(f"видеокодек: {encoder}")

    integration_spec = None
    if integration_id:
        from . import integrator
        item = integrator.get_integration(integration_id)
        ad_file = integrator.get_integration_file(integration_id)
        if item and ad_file:
            integration_spec = integrator.IntegrationSpec(
                file_path=ad_file,
                mode=str(item.get("mode") or "insert"),
                position=str(item.get("position") or "after_hook"),
                hook_sec=float(item.get("hook_sec") or 3.0),
                start_sec=float(item.get("start_sec") or 0.0),
                duration_sec=float(item.get("duration_sec") or 0.0),
                scale_pct=int(item.get("scale_pct") or 75),
            )
            log(f"интеграция: {item.get('name')} ({integration_spec.mode}/{integration_spec.position})")

    ratio = (info["width"] / info["height"]) if info["height"] else 16 / 9
    crf = int(render_cfg.get("crf", 20))
    fps = int(render_cfg.get("fps", 30))

    max_workers = 2 if encoder != "cpu" else min(3, max(1, (os.cpu_count() or 2) // 2))
    max_workers = max(1, min(max_workers, len(moments)))
    done_count = 0
    progress_lock = threading.Lock()

    def render_one(index: int, moment) -> RenderedClip | None:
        nonlocal done_count
        if job.cancelled():
            return None

        clip_work = work / f"c_{index:02d}"
        clip_work.mkdir(parents=True, exist_ok=True)
        dub_wav: Path | None = None
        active_words = words

        if mode == "brainrot":
            from . import tts
            seg_slice = [
                tts.DubSegment(max(moment.start, s), min(moment.end, e), txt)
                for s, e, txt in dub_segments_all
                if e > moment.start + 0.05 and s < moment.end - 0.05
            ]
            dub_res = tts.build_dub_track(
                seg_slice,
                clip_start=moment.start,
                clip_end=moment.end,
                workdir=clip_work / "tts",
                voice_id=voice_id,
                log=log,
            )
            dub_wav = dub_res.audio_path
            if dub_res.words:
                active_words = [Word(moment.start + ws, moment.start + we, wt) for ws, we, wt in dub_res.words]

        ass_path: Path | None = None
        if use_subs:
            cues = build_cues(active_words, moment.start, moment.end)
            if cues:
                ass_path = clip_work / f"clip_{index:02d}.ass"
                write_ass(ass_path, cues, style, "Arial Black", bool(style["uppercase"]))
            elif mode == "moments":
                log(f"клип {index}: в выбранном диапазоне нет слов, пропускаю")
                return None

        file_name = f"clip_{index:02d}.mp4"
        preview_name = f"clip_{index:02d}.jpg"
        out_mp4 = clips_dir / file_name

        render_clip(
            source, moment.start, moment.end, ass_path, out_mp4,
            workdir=clip_work, ratio=ratio, has_audio=info["has_audio"],
            crf=crf, fps=fps, background=bg_mode,
            effect=effect_key, protection=protection_key,
            music=music_file, music_volume=music_volume,
            dub=dub_wav, source_volume=orig_audio_vol if mode == "brainrot" else 1.0,
            encoder=encoder,
        )

        if integration_spec is not None:
            from . import integrator
            tmp_ad = clip_work / f"clip_{index:02d}_ad.mp4"
            integrator.apply_integration(
                out_mp4, integration_spec, tmp_ad,
                crf=crf, fps=fps, video_encoder_args=encoder_args(encoder, crf), log=log,
            )
            tmp_ad.replace(out_mp4)

        make_preview(out_mp4, previews_dir / preview_name)

        clip_words = [w for w in active_words if w.end > moment.start and w.start < moment.end]
        clip_dur = max(0.1, moment.end - moment.start)
        clip = RenderedClip(
            index=index, file=file_name, preview=preview_name,
            start=moment.start, end=moment.end, duration=clip_dur,
            title=moment.title, hook=moment.hook, hashtags=list(moment.hashtags),
            reason=moment.reason, score=int(moment.score),
            words=len(clip_words),
            wpm=int(round(len(clip_words) / clip_dur * 60)),
            effect=effect_key, protection=protection_key,
            music=music_id if music_file is not None else "none",
        )
        with progress_lock:
            done_count += 1
            prog(54 + 44.0 * done_count / max(1, len(moments)), "рендер", f"клип {done_count} из {len(moments)}")
        log(f"клип {index}: {int(clip.duration)}с, {clip.words} сл., {clip.title[:50] or 'без заголовка'}")
        return clip

    prog(54, "рендер", f"рендерю {len(moments)} клипов ({max_workers} поток.)")
    rendered_by_idx: dict[int, RenderedClip] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(render_one, idx, mom): idx for idx, mom in enumerate(moments, start=1)}
        for fut in as_completed(futures):
            idx = futures[fut]
            clip = fut.result()
            if clip is not None:
                rendered_by_idx[idx] = clip

    clips = [rendered_by_idx[i] for i in sorted(rendered_by_idx)]
    if not clips:
        raise PipelineError("ни один момент не удалось отрендерить")

    if not S.env_flag("CUTTER_KEEP_TEMP"):
        shutil.rmtree(work, ignore_errors=True)

    manifest = {
        "source": source.name,
        "source_path": str(source),
        "duration": round(duration, 2),
        "style": style_key,
        "mode": mode,
        "clips": [c.to_dict() for c in clips],
    }
    (workdir / "clips.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    job.set_progress(100, "готово", f"{len(clips)} клипов")
    return {"clips_dir": str(clips_dir), "previews_dir": str(previews_dir), "clips": [c.to_dict() for c in clips]}
