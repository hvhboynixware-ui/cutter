# -*- coding: utf-8 -*-
"""Озвучка и тайминг-синхронизация для режима «Китайская история / Brainrot».

По умолчанию использует плавные нейро-голоса (Edge Multilingual / RU) с естественной
подачей без искажения формант, поддерживает опциональный клон эталонных голосов через
ElevenLabs (из app/assets/voices/canon_narrator_*.mp3) и офлайн-фолбэк на Piper.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import requests

from . import settings as S

VOICES_DIR = S.APP_DIR / "assets" / "voices"
CANON_SAMPLE_1 = VOICES_DIR / "canon_narrator_1.mp3"
CANON_SAMPLE_2 = VOICES_DIR / "canon_narrator_2.mp3"

VOICES: tuple[dict, ...] = (
    {
        "id": "canon_bright",
        "label": "Рассказчик · Канон 1 (живой)",
        "voice": "en-US-BrianMultilingualNeural",
        "fallbacks": ("en-US-AndrewMultilingualNeural", "en-AU-WilliamMultilingualNeural"),
        "rate": "+5%",
        "sample": "canon_narrator_1.mp3",
        "piper": "ru_RU-dmitri-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium",
        "speed": 1.0,
        "pitch": 1.0,
        "note": "Чистый энергичный голос рассказчика под китайские истории (эталон 1)",
    },
    {
        "id": "canon_deep",
        "label": "Рассказчик · Канон 2 (глубокий)",
        "voice": "en-US-AndrewMultilingualNeural",
        "fallbacks": ("en-US-BrianMultilingualNeural", "en-AU-WilliamMultilingualNeural"),
        "rate": "+3%",
        "sample": "canon_narrator_2.mp3",
        "piper": "ru_RU-dmitri-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium",
        "speed": 1.0,
        "pitch": 1.0,
        "note": "Спокойный глубокий тембр для пересказа и драмы от первого лица (эталон 2)",
    },
    {
        "id": "canon_calm",
        "label": "Рассказчик · Размеренный",
        "voice": "en-AU-WilliamMultilingualNeural",
        "fallbacks": ("en-US-BrianMultilingualNeural", "en-US-AndrewMultilingualNeural"),
        "rate": "+4%",
        "sample": "canon_narrator_1.mp3",
        "piper": "ru_RU-dmitri-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium",
        "speed": 1.0,
        "pitch": 1.0,
        "note": "Ровная мягкая подача без резких перепадов",
    },
    {
        "id": "eleven_clone_1",
        "label": "ElevenLabs · Клон эталона 1",
        "voice": "eleven_1",
        "fallbacks": ("en-US-BrianMultilingualNeural", "en-US-AndrewMultilingualNeural"),
        "rate": "+5%",
        "sample": "canon_narrator_1.mp3",
        "piper": "ru_RU-dmitri-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium",
        "speed": 1.0,
        "pitch": 1.0,
        "note": "Клонирует эталон 1 через ключ ElevenLabs (без ключа — авто на Канон 1)",
    },
    {
        "id": "eleven_clone_2",
        "label": "ElevenLabs · Клон эталона 2",
        "voice": "eleven_2",
        "fallbacks": ("en-US-AndrewMultilingualNeural", "en-US-BrianMultilingualNeural"),
        "rate": "+3%",
        "sample": "canon_narrator_2.mp3",
        "piper": "ru_RU-dmitri-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium",
        "speed": 1.0,
        "pitch": 1.0,
        "note": "Клонирует эталон 2 через ключ ElevenLabs (без ключа — авто на Канон 2)",
    },
    {
        "id": "svetlana_drama",
        "label": "Женский (дорама)",
        "voice": "en-US-AvaMultilingualNeural",
        "fallbacks": ("ru-RU-SvetlanaNeural", "en-US-EmmaMultilingualNeural"),
        "rate": "+4%",
        "sample": "",
        "piper": "ru_RU-irina-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium",
        "speed": 1.0,
        "pitch": 1.0,
        "note": "Выразительный женский голос для дорам и историй",
    },
    {
        "id": "dmitry_drama",
        "label": "Дмитрий (нативный / авто)",
        "voice": "ru-RU-DmitryNeural",
        "fallbacks": ("en-US-BrianMultilingualNeural", "en-US-AndrewMultilingualNeural"),
        "rate": "+5%",
        "sample": "canon_narrator_1.mp3",
        "piper": "ru_RU-dmitri-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium",
        "speed": 1.0,
        "pitch": 1.0,
        "note": "Пробует нативный ru-RU-DmitryNeural, при отказе Microsoft берёт Канон 1",
    },
    {
        "id": "piper_dmitri",
        "label": "Дмитрий (офлайн Piper)",
        "voice": "piper",
        "fallbacks": (),
        "rate": "+0%",
        "sample": "",
        "piper": "ru_RU-dmitri-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium",
        "speed": 1.04,
        "pitch": 1.0,
        "note": "100% локальная нейро-озвучка без интернета",
    },
    {
        "id": "andrew_en",
        "label": "Andrew (English)",
        "voice": "en-US-AndrewMultilingualNeural",
        "fallbacks": ("en-US-BrianMultilingualNeural",),
        "rate": "+6%",
        "sample": "",
        "piper": "en_US-lessac-medium",
        "piper_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium",
        "speed": 1.0,
        "pitch": 1.0,
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
    out = []
    for item in VOICES:
        row = dict(item)
        sample_name = str(item.get("sample") or "")
        row["has_sample"] = bool(sample_name and (VOICES_DIR / sample_name).is_file())
        out.append(row)
    return out


def get_voice(voice_id: str | None) -> dict:
    return _VOICE_BY_ID.get((voice_id or "").strip(), VOICES[0])


def sample_file(voice_id: str | None) -> Path | None:
    spec = get_voice(voice_id)
    name = str(spec.get("sample") or "")
    if not name:
        return None
    p = VOICES_DIR / name
    return p if p.is_file() else None


def merge_dub_segments(
    segments: Sequence[DubSegment],
    *,
    max_gap: float = 0.75,
    max_dur: float = 8.5,
    max_chars: int = 175,
) -> list[DubSegment]:
    """Склеивает мелкие Whisper-строки в цельные фразы, чтобы озвучка звучала связно и без спешки."""
    merged: list[DubSegment] = []
    for raw in segments:
        txt = " ".join(str(raw.text or "").split()).strip()
        if not txt:
            continue
        s = float(raw.start)
        e = float(raw.end)
        if e <= s + 0.05:
            continue
        if not merged:
            merged.append(DubSegment(start=s, end=e, text=txt))
            continue
        prev = merged[-1]
        gap = s - prev.end
        prev_terminal = prev.text.endswith((".", "!", "?", "…"))
        combined_dur = e - prev.start
        combined_len = len(prev.text) + 1 + len(txt)
        if not prev_terminal and gap <= max_gap and combined_dur <= max_dur and combined_len <= max_chars:
            prev.end = e
            prev.text = f"{prev.text} {txt}"
        elif gap <= 0.35 and (prev.end - prev.start) < 2.0 and combined_dur <= max_dur and combined_len <= max_chars:
            prev.end = e
            prev.text = f"{prev.text}, {txt}"
        else:
            merged.append(DubSegment(start=s, end=e, text=txt))
    return merged


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


async def _synth_edge_async(text: str, out_mp3: Path, *, voice: str, rate: str = "+4%", proxy: str = "") -> None:
    import edge_tts

    kwargs: dict = {"text": text, "voice": voice}
    if rate and rate not in {"+0%", "0%"}:
        kwargs["rate"] = rate
    if proxy:
        kwargs["proxy"] = proxy
    comm = edge_tts.Communicate(**kwargs)
    await asyncio.wait_for(comm.save(str(out_mp3)), timeout=16.0)


def _synth_edge_sync(text: str, out_mp3: Path, *, voice: str, rate: str = "+4%", proxy: str = "") -> None:
    try:
        asyncio.run(_synth_edge_async(text, out_mp3, voice=voice, rate=rate, proxy=proxy))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_synth_edge_async(text, out_mp3, voice=voice, rate=rate, proxy=proxy))
        finally:
            loop.close()


def _ensure_eleven_voice(slot_idx: int, api_key: str, *, proxy: str = "") -> str:
    """Берёт сохранённый voice_id из настроек или один раз клонирует эталон в ElevenLabs."""
    settings = S.load()
    tts_cfg = settings.get("tts") or {}
    field = f"eleven_voice_{slot_idx}"
    existing = str(tts_cfg.get(field) or "").strip()
    if existing:
        return existing

    sample = CANON_SAMPLE_1 if slot_idx == 1 else CANON_SAMPLE_2
    if not sample.is_file():
        return ""

    proxies = {"http": proxy, "https": proxy} if proxy else None
    with sample.open("rb") as fh:
        resp = requests.post(
            "https://api.elevenlabs.io/v1/voices/add",
            headers={"xi-api-key": api_key},
            data={"name": f"Cutter Canon Narrator {slot_idx}", "description": "Chinese drama clean Russian narration"},
            files={"files": (sample.name, fh, "audio/mpeg")},
            timeout=45.0,
            proxies=proxies,
        )
    if resp.status_code >= 400:
        raise TTSError(f"ElevenLabs voice clone HTTP {resp.status_code}: {resp.text[:200]}")
    vid = str((resp.json() or {}).get("voice_id") or "").strip()
    if vid:
        S.update({"tts": {field: vid}})
    return vid


def _synth_elevenlabs(text: str, out_mp3: Path, slot_idx: int, api_key: str, *, proxy: str = "") -> Path:
    vid = _ensure_eleven_voice(slot_idx, api_key, proxy=proxy)
    if not vid:
        raise TTSError("ElevenLabs voice_id пуст")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
        headers={
            "xi-api-key": api_key,
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.55, "similarity_boost": 0.85, "style": 0.15, "use_speaker_boost": True},
        },
        timeout=35.0,
        proxies=proxies,
    )
    if resp.status_code >= 400 or len(resp.content) < 256:
        raise TTSError(f"ElevenLabs TTS HTTP {resp.status_code}: {resp.text[:200]}")
    out_mp3.write_bytes(resp.content)
    return out_mp3


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


def synthesize_line(text: str, out_mp3: Path, voice_id: str = "canon_bright") -> Path:
    """Синтезирует реплику: ElevenLabs (если есть ключ) -> Edge Multilingual цепочка -> Piper офлайн."""
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        raise TTSError("пустая строка для озвучки")
    if clean[-1] not in ".!?…:;":
        clean = clean + "."

    spec = get_voice(voice_id)
    out_mp3.parent.mkdir(parents=True, exist_ok=True)

    if spec["voice"] == "piper":
        return _synth_piper(clean, out_mp3, spec)

    settings = S.load()
    proxy = str((settings.get("network") or {}).get("proxy") or "").strip()
    el_key = str((settings.get("tts") or {}).get("elevenlabs_key") or "").strip()

    if spec["voice"] in {"eleven_1", "eleven_2"} and el_key:
        slot_idx = 1 if spec["voice"] == "eleven_1" else 2
        try:
            return _synth_elevenlabs(clean, out_mp3, slot_idx, el_key, proxy=proxy)
        except Exception:
            pass

    edge_candidates: list[str] = []
    if not str(spec["voice"]).startswith("eleven_"):
        edge_candidates.append(str(spec["voice"]))
    for fb in spec.get("fallbacks") or ():
        if fb not in edge_candidates:
            edge_candidates.append(fb)

    rate = str(spec.get("rate") or "+4%")
    edge_err: Exception | None = None
    try:
        import edge_tts  # noqa: F401
        for edge_voice in edge_candidates:
            for attempt in range(2):
                try:
                    _synth_edge_sync(clean, out_mp3, voice=edge_voice, rate=rate, proxy=proxy)
                    if out_mp3.is_file() and out_mp3.stat().st_size >= 128:
                        return out_mp3
                except Exception as exc:
                    edge_err = exc
                    out_mp3.unlink(missing_ok=True)
                    if attempt == 0:
                        time.sleep(0.2)
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
    voice_id: str = "canon_bright",
    log: Callable[[str], None] = print,
) -> DubResult:
    """Создаёт естественную WAV-дорожку переозвучки на [clip_start, clip_end] и слова для субтитров."""
    clip_dur = max(0.5, float(clip_end) - float(clip_start))
    workdir.mkdir(parents=True, exist_ok=True)
    out_wav = workdir / "dub_voice.wav"

    raw_local: list[DubSegment] = []
    for seg in segments:
        txt = " ".join(str(seg.text or "").split()).strip()
        if not txt:
            continue
        s = max(0.0, float(seg.start) - clip_start)
        e = min(clip_dur, float(seg.end) - clip_start)
        if e - s < 0.25:
            continue
        raw_local.append(DubSegment(start=s, end=e, text=txt))

    local_segs = merge_dub_segments(raw_local)

    if not local_segs:
        _run_ffmpeg([
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={clip_dur:.3f}",
            "-c:a", "pcm_s16le", str(out_wav),
        ])
        return DubResult(audio_path=out_wav, cues=[], words=[])

    fitted_files: list[tuple[float, Path]] = []
    dub_words: list[tuple[float, float, str]] = []
    cues: list[tuple[float, float, str]] = []

    spec = get_voice(voice_id)
    base_speed = float(spec.get("speed") or 1.0)
    pitch_factor = max(0.85, min(1.15, float(spec.get("pitch") or 1.0)))

    cursor = 0.0
    for idx, seg in enumerate(local_segs):
        start_at = max(seg.start, cursor)
        remaining = clip_dur - start_at
        if remaining < 0.35:
            break
        next_start = local_segs[idx + 1].start if idx + 1 < len(local_segs) else clip_dur
        slot = max(0.45, min(remaining, max(seg.end - start_at, next_start - start_at - 0.05)))

        raw_mp3 = workdir / f"seg_{idx:03d}.mp3"
        fit_wav = workdir / f"seg_{idx:03d}.wav"

        synthesize_line(seg.text, raw_mp3, voice_id=voice_id)
        spoken_dur = _probe_duration(raw_mp3) or slot
        needed = spoken_dur / max(0.5, slot)
        # Если фраза чуть длиннее, расширяем слот в свободную паузу вместо сильного ускорения (> 1.24x).
        if needed > 1.22 and remaining > slot:
            slot = min(remaining, max(slot, spoken_dur / 1.18))
            needed = spoken_dur / max(0.5, slot)
        target_speed = max(base_speed, min(1.24, needed))

        af_parts = [
            "silenceremove=start_periods=1:start_threshold=-46dB:stop_periods=-1:stop_threshold=-46dB:stop_duration=0.14",
            "aresample=48000",
        ]
        if abs(pitch_factor - 1.0) > 0.01:
            af_parts.append(f"asetrate={int(round(48000 * pitch_factor))},aresample=48000")
        tempo = target_speed / pitch_factor
        if abs(tempo - 1.0) > 0.02:
            af_parts.append(_atempo_chain(tempo))
        actual_dur = spoken_dur / target_speed

        _run_ffmpeg([
            "-i", str(raw_mp3),
            "-af", ",".join(af_parts),
            "-t", f"{slot:.3f}",
            "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le",
            str(fit_wav),
        ])
        measured = min(remaining, _probe_duration(fit_wav) or min(slot, actual_dur))
        line_end = min(clip_dur, start_at + max(0.25, measured))
        fitted_files.append((start_at, fit_wav))
        cues.append((start_at, line_end, seg.text))
        dub_words.extend(_words_for_line(start_at, line_end, seg.text))
        cursor = line_end + 0.04

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
        f"highpass=f=70,equalizer=f=220:width_type=o:width=1.4:g=1.6,"
        f"acompressor=threshold=-16dB:ratio=2.2:attack=10:release=120,"
        f"alimiter=limit=0.95,loudnorm=I=-15:TP=-1.5:LRA=9[out]"
    )
    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[out]", "-t", f"{clip_dur:.3f}",
        "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    _run_ffmpeg(cmd)
    log(f"озвучка: собрано {len(fitted_files)} фраз ({clip_dur:.1f}с, голос {spec['label']})")
    return DubResult(audio_path=out_wav, cues=cues, words=dub_words)
