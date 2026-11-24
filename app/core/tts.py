# -*- coding: utf-8 -*-
"""Озвучка и тайминг-синхронизация для режима «Китайская история / Brainrot».

Генерирует нейро-озвучку (edge-tts) по репликам с точной подгонкой скорости
(atempo) под хронометраж каждого сегмента, смешивает с оригиналом/музыкой и
отдаёт новые таймкоды слов для синхронных субтитров.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import settings as S

VOICES: tuple[dict, ...] = (
    {
        "id": "dmitry_drama",
        "label": "Дмитрий (канон дорамы)",
        "voice": "ru-RU-DmitryNeural",
        "piper": "ru_RU-dmitri-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium",
        "speed": 1.18,
        "pitch": 0.98,
        "note": "Каноничный быстрый и плотный голос для пересказов китайских историй (авто-фолбэк на локальный Piper)",
    },
    {
        "id": "dmitry_deep",
        "label": "Дмитрий (глубокий)",
        "voice": "ru-RU-DmitryNeural",
        "piper": "ru_RU-dmitri-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium",
        "speed": 1.12,
        "pitch": 0.93,
        "note": "Более низкий тембр для мрачных сюжетов и плот-твистов",
    },
    {
        "id": "dmitry_fast",
        "label": "Дмитрий (турбо байт)",
        "voice": "ru-RU-DmitryNeural",
        "piper": "ru_RU-dmitri-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium",
        "speed": 1.24,
        "pitch": 1.0,
        "note": "Максимально динамичная подача без пауз",
    },
    {
        "id": "svetlana_drama",
        "label": "Светлана (женский)",
        "voice": "ru-RU-SvetlanaNeural",
        "piper": "ru_RU-irina-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium",
        "speed": 1.14,
        "pitch": 0.98,
        "note": "Выразительный женский голос для дорам и историй",
    },
    {
        "id": "piper_dmitri",
        "label": "Дмитрий (офлайн Piper)",
        "voice": "piper",
        "piper": "ru_RU-dmitri-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium",
        "speed": 1.15,
        "pitch": 0.96,
        "note": "100% локальная нейро-озвучка без сети и лимитов",
    },
    {
        "id": "andrew_en",
        "label": "Andrew (English)",
        "voice": "en-US-AndrewMultilingualNeural",
        "piper": "en_US-lessac-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium",
        "speed": 1.14,
        "pitch": 0.98,
        "note": "Англоязычный голос для зарубежных Shorts/Reels",
    },
)
_VOICE_BY_ID = {item["id"]: item for item in VOICES}


class TTSError(RuntimeError):
    pass


@dataclass
class DubSegment:
    start: float
    end: float
    text: str


@dataclass
class DubResult:
    audio_path: Path
    cues: list[tuple[float, float, str]]
    words: list[tuple[float, float, str]]


def available_voices() -> list[dict]:
    return [dict(item) for item in VOICES]


def get_voice(voice_id: str | None) -> dict:
    return _VOICE_BY_ID.get((voice_id or "").strip(), VOICES[0])


def _probe_duration(path: Path) -> float:
    res = subprocess.run(
        [S.ffprobe_path(), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if res.returncode != 0:
        return 0.0
    try:
        data = json.loads(res.stdout or "{}")
        return float((data.get("format") or {}).get("duration") or 0.0)
    except Exception:
        return 0.0


def _atempo_chain(speed: float) -> str:
    """ffmpeg atempo принимает [0.5 .. 2.0], большие значения раскладываем в цепочку."""
    speed = max(0.5, min(3.0, float(speed)))
    parts: list[str] = []
    cur = speed
    while cur > 2.0:
        parts.append("atempo=2.0")
        cur /= 2.0
    while cur < 0.5:
        parts.append("atempo=0.5")
        cur /= 0.5
    parts.append(f"atempo={cur:.4f}")
    return ",".join(parts)


async def _synth_edge_async(text: str, out_mp3: Path, *, voice: str) -> None:
    import edge_tts

    comm = edge_tts.Communicate(text=text, voice=voice, boundary="WordBoundary")
    await comm.save(str(out_mp3))


_PIPER_CACHE: dict[str, object] = {}


def _ensure_piper_voice(spec: dict):
    import urllib.request
    from piper import PiperVoice

    model_id = str(spec.get("piper") or "ru_RU-dmitri-medium")
    if model_id in _PIPER_CACHE:
        return _PIPER_CACHE[model_id]

    folder = S.MODELS_DIR / "piper"
    folder.mkdir(parents=True, exist_ok=True)
    onnx_path = folder / f"{model_id}.onnx"
    json_path = folder / f"{model_id}.onnx.json"
    base_url = str(spec.get("piper_url") or "")
    for ext, target in ((".onnx", onnx_path), (".onnx.json", json_path)):
        if not target.is_file() or target.stat().st_size < 128:
            if not base_url:
                raise TTSError(f"нет модели Piper {model_id}")
            tmp = target.with_suffix(target.suffix + ".part")
            urllib.request.urlretrieve(base_url + ext, tmp)
            tmp.replace(target)

    voice = PiperVoice.load(str(onnx_path), config_path=str(json_path))
    _PIPER_CACHE[model_id] = voice
    return voice


def _synth_piper(text: str, out_path: Path, spec: dict) -> Path:
    import wave
    from piper import SynthesisConfig

    voice = _ensure_piper_voice(spec)
    wav_tmp = out_path.with_suffix(".piper.wav")
    with wave.open(str(wav_tmp), "wb") as wf:
        voice.synthesize_wav(text, wf, syn_config=SynthesisConfig(length_scale=1.0))
    if out_path.suffix.lower() == ".wav":
        wav_tmp.replace(out_path)
    else:
        _run_ffmpeg(["-i", str(wav_tmp), "-c:a", "libmp3lame", "-b:a", "128k", str(out_path)])
        wav_tmp.unlink(missing_ok=True)
    return out_path


def synthesize_line(text: str, out_mp3: Path, voice_id: str = "dmitry_drama") -> Path:
    """Синтезирует реплику: сначала edge-tts (если доступен), при сбое/офлайне — локальный Piper."""
    import time

    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        raise TTSError("пустая строка для озвучки")
    # Microsoft Edge TTS чаще отбрасывает фразы без финальной пунктуации.
    if clean[-1] not in ".!?…:;":
        clean = clean + "."

    spec = get_voice(voice_id)
    out_mp3.parent.mkdir(parents=True, exist_ok=True)

    if spec["voice"] == "piper":
        return _synth_piper(clean, out_mp3, spec)

    edge_err: Exception | None = None
    try:
        import edge_tts  # noqa: F401
        for attempt in range(2):
            try:
                try:
                    asyncio.run(_synth_edge_async(clean, out_mp3, voice=spec["voice"]))
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    try:
                        loop.run_until_complete(_synth_edge_async(clean, out_mp3, voice=spec["voice"]))
                    finally:
                        loop.close()
                if out_mp3.is_file() and out_mp3.stat().st_size >= 128:
                    return out_mp3
            except Exception as exc:
                edge_err = exc
                if attempt == 0:
                    time.sleep(0.25)
    except ImportError as exc:
        edge_err = exc

    try:
        return _synth_piper(clean, out_mp3, spec)
    except Exception as piper_exc:
        raise TTSError(f"ошибка синтеза голоса ({spec['voice']} -> piper): {edge_err} | {piper_exc}") from piper_exc


def _run_ffmpeg(args: list[str]) -> None:
    res = subprocess.run(
        [S.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if res.returncode != 0:
        tail = (res.stderr or res.stdout or "").strip()[-800:]
        raise TTSError(f"ffmpeg tts упал: {tail}")


def _words_for_line(start: float, end: float, text: str) -> list[tuple[float, float, str]]:
    tokens = [t for t in str(text or "").split() if t.strip()]
    if not tokens:
        return []
    span = max(0.2, end - start)
    weights = [max(1, len(tok)) for tok in tokens]
    total_w = sum(weights)
    cursor = start
    out: list[tuple[float, float, str]] = []
    for tok, w in zip(tokens, weights):
        part = span * (w / total_w)
        out.append((cursor, min(end, cursor + part), tok))
        cursor += part
    return out


def build_dub_track(
    segments: Sequence[DubSegment],
    *,
    clip_start: float,
    clip_end: float,
    workdir: Path,
    voice_id: str = "dmitry_drama",
    log: Callable[[str], None] = print,
) -> DubResult:
    """Создаёт полную WAV-дорожку переозвучки на [clip_start, clip_end] и слова для субтитров."""
    clip_dur = max(0.5, float(clip_end) - float(clip_start))
    workdir.mkdir(parents=True, exist_ok=True)
    out_wav = workdir / "dub_voice.wav"

    # Нормализуем сегменты относительно начала клипа.
    local_segs: list[DubSegment] = []
    for seg in segments:
        txt = " ".join(str(seg.text or "").split()).strip()
        if not txt:
            continue
        s = max(0.0, float(seg.start) - clip_start)
        e = min(clip_dur, float(seg.end) - clip_start)
        if e - s < 0.25:
            continue
        local_segs.append(DubSegment(start=s, end=e, text=txt))

    if not local_segs:
        # Тишина на длину клипа, если реплик нет.
        _run_ffmpeg([
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={clip_dur:.3f}",
            "-c:a", "pcm_s16le", str(out_wav),
        ])
        return DubResult(audio_path=out_wav, cues=[], words=[])

    fitted_files: list[tuple[float, Path]] = []
    dub_words: list[tuple[float, float, str]] = []
    cues: list[tuple[float, float, str]] = []

    spec = get_voice(voice_id)
    base_speed = float(spec.get("speed") or 1.15)
    pitch_factor = max(0.75, min(1.25, float(spec.get("pitch") or 1.0)))

    for idx, seg in enumerate(local_segs):
        next_start = local_segs[idx + 1].start if idx + 1 < len(local_segs) else clip_dur
        slot = max(0.35, min(clip_dur - seg.start, max(seg.end - seg.start, next_start - seg.start - 0.04)))
        raw_mp3 = workdir / f"seg_{idx:03d}.mp3"
        fit_wav = workdir / f"seg_{idx:03d}.wav"

        synthesize_line(seg.text, raw_mp3, voice_id=voice_id)
        spoken_dur = _probe_duration(raw_mp3) or slot
        target_speed = max(base_speed, spoken_dur / slot)
        target_speed = min(1.95, target_speed)

        af_parts = [
            "silenceremove=start_periods=1:start_threshold=-45dB:stop_periods=-1:stop_threshold=-45dB:stop_duration=0.05",
            "aresample=48000",
        ]
        if abs(pitch_factor - 1.0) > 0.005:
            af_parts.append(f"asetrate={int(round(48000 * pitch_factor))},aresample=48000")
        tempo = target_speed / pitch_factor
        if abs(tempo - 1.0) > 0.01:
            af_parts.append(_atempo_chain(tempo))
        actual_dur = spoken_dur / target_speed

        _run_ffmpeg([
            "-i", str(raw_mp3),
            "-af", ",".join(af_parts),
            "-t", f"{slot:.3f}",
            "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le",
            str(fit_wav),
        ])
        measured = min(slot, _probe_duration(fit_wav) or min(slot, actual_dur))
        line_end = min(clip_dur, seg.start + max(0.25, measured))
        fitted_files.append((seg.start, fit_wav))
        cues.append((seg.start, line_end, seg.text))
        dub_words.extend(_words_for_line(seg.start, line_end, seg.text))

    # Сборка единой дорожки: пустая база + adelay каждого сегмента.
    cmd = ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={clip_dur:.3f}"]
    filter_parts: list[str] = []
    mix_labels = ["[0:a]"]
    for i, (offset_sec, seg_wav) in enumerate(fitted_files, start=1):
        cmd += ["-i", str(seg_wav)]
        delay_ms = max(0, int(round(offset_sec * 1000)))
        filter_parts.append(f"[{i}:a]adelay={delay_ms}|{delay_ms}[d{i}]")
        mix_labels.append(f"[d{i}]")

    mix_in = "".join(mix_labels)
    filter_parts.append(
        f"{mix_in}amix=inputs={len(mix_labels)}:duration=first:normalize=0,"
        f"highpass=f=85,acompressor=threshold=-18dB:ratio=3.2:attack=5:release=60,"
        f"treble=g=2.5:f=4500,loudnorm=I=-14:TP=-1.5:LRA=11[out]"
    )
    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[out]", "-t", f"{clip_dur:.3f}",
        "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    _run_ffmpeg(cmd)
    log(f"озвучка: собрано {len(fitted_files)} реплик ({clip_dur:.1f}с)")
    return DubResult(audio_path=out_wav, cues=cues, words=dub_words)
