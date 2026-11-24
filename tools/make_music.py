# -*- coding: utf-8 -*-
"""Процедурный синтез оригинального музыкального пака для Cutter.

ВАЖНО: здесь нет ни одного чужого сэмпла. Всё — numpy: осцилляторы, шум,
аккорды, ударные, эквалайзер, свёрточный реверб, свёртка импусов. Поэтому
права на треки наши и Content ID/claim'ы на YouTube/TikTok не прилетают.

Цепочка трека:
    аранжировка (Bus: музыка / ударные / эффекты)
    -> FFT-эквалайзер (циклический, луп не рвётся)
    -> моно-низ (ниже 110 Гц в моно — фазовых проблем нет)
    -> свёрточный реверб с синтетическим импульсом (циклическая свёртка)
    -> стерео-расширение, мягкий tanh-глю
    -> нормализация по LUFS (EBU R128 через ffmpeg) и по пику (<= -1.5 dBFS)
    -> mp3 192 kbps / 44.1 kHz / стерео через libmp3lame

Все звуки упакованы в луп по построению: каждая нота добавляется в буфер
по модулю длины лупа, а огибающая любой ноты начинается и заканчивается
в нуле. Поэтому на стыке лупа нет щелчка — это математика, а не надежда.

Запуск:
    Cutter\\.venv\\Scripts\\python.exe tools\\make_music.py
    ... tools\\make_music.py --only lofi-chill --only hype-trap
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "app" / "assets" / "music"

SR = 44100
BEATS_PER_BAR = 4

# Мастер: пик в буфере -1.5 dBFS. mp3 при декоде не поднимает пик (проверено),
# поэтому после декода укладываемся в требование "не выше -1 dBFS".
PEAK_CEILING_DB = -1.5
TARGET_LUFS = -16.0
LUFS_TOLERANCE = 2.5  # насколько можно недобрать, если мешает пик

LICENSE = "original, generated for Cutter"


# ---------------------------------------------------------------------------
# Утилиты: числа, панорама, огибающие
# ---------------------------------------------------------------------------


def db_to_gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def gain_to_db(gain: float) -> float:
    return 20.0 * math.log10(max(float(gain), 1e-12))


def midi_hz(note: float) -> float:
    """MIDI-нота -> герцы. Ля первой октавы (69) = 440."""
    return 440.0 * 2.0 ** ((float(note) - 69.0) / 12.0)


def pan_gains(pan: float) -> tuple[float, float]:
    """Равномощная панорама: на краях 1.0, в центре -3 дБ; L^2+R^2 = 1."""
    pan = float(np.clip(pan, -1.0, 1.0))
    angle = (pan + 1.0) * 0.25 * math.pi
    return math.cos(angle), math.sin(angle)


def n_samples(seconds: float) -> int:
    return max(1, int(round(seconds * SR)))


def _raised_cos(n: int) -> np.ndarray:
    """Приподнятый косинус 0..1: нулевая производная на краях, без щелчков."""
    if n <= 0:
        return np.zeros(0)
    x = (np.arange(n) + 0.5) / n
    return 0.5 - 0.5 * np.cos(np.pi * x)


def env_shape(
    n: int,
    attack: float = 0.01,
    decay: float = 0.08,
    sustain: float = 0.7,
    release: float = 0.2,
) -> np.ndarray:
    """ADSR из четырёх гладких кусков. Гарантирует 0 в первом и последнем сэмпле."""
    n = max(2, int(n))
    sustain = float(np.clip(sustain, 0.0, 1.0))
    n_attack = int(min(max(1, round(attack * SR)), n // 2))
    n_release = int(min(max(1, round(release * SR)), max(1, n - n_attack - 1)))
    n_decay = int(min(max(0, round(decay * SR)), max(0, n - n_attack - n_release)))
    n_hold = max(0, n - n_attack - n_decay - n_release)

    parts = [_raised_cos(n_attack)]  # 0 -> 1
    if n_decay:
        # 1 -> sustain по косинусу: стыки без изломов
        curve = 0.5 + 0.5 * np.cos(np.pi * (np.arange(n_decay) + 0.5) / n_decay)
        parts.append(sustain + (1.0 - sustain) * curve)
    if n_hold:
        parts.append(np.full(n_hold, sustain))
    if n_release:
        parts.append(sustain * _raised_cos(n_release)[::-1])  # sustain -> 0

    env = np.concatenate(parts) if parts else np.zeros(0)
    if len(env) < n:
        env = np.concatenate([env, np.zeros(n - len(env))])
    env = env[:n].astype(np.float64)
    env[0] = 0.0
    env[-1] = 0.0
    return env


def env_perc(n: int, attack: float = 0.002, tau: float = 0.2) -> np.ndarray:
    """Перкуссионная огибающая: быстрый гладкий удар и экспоненциальный хвост."""
    n = max(2, int(n))
    t = np.arange(n) / SR
    env = np.exp(-np.maximum(t - attack, 0.0) / max(tau, 1e-4))
    n_attack = int(min(max(1, round(attack * SR)), n))
    env[:n_attack] *= _raised_cos(n_attack)
    env[-1] = 0.0
    env[0] = 0.0
    return env


def declick(sig: np.ndarray, fade_in: float = 0.0015, fade_out: float = 0.004) -> np.ndarray:
    """Короткие фейды по краям one-shot'а: одинокий сэмпл не должен щёлкать."""
    sig = np.asarray(sig, dtype=np.float64).copy()
    n = sig.shape[0]
    a = min(n, max(1, round(fade_in * SR)))
    b = min(n, max(1, round(fade_out * SR)))
    sig[:a] *= _raised_cos(a)
    sig[-b:] *= _raised_cos(b)[::-1]
    return sig


def add_wrapped(buf: np.ndarray, start: int, sig: np.ndarray, channel: int) -> None:
    """Добавляет сигнал в буфер по кругу (по модулю длины лупа).

    Это главный приём бесшовности: хвост, вылезший за конец лупа, попадает
    в начало — и в циклическом воспроизведении всё звучит непрерывно.
    """
    n = buf.shape[0]
    m = sig.shape[0]
    if m == 0:
        return
    pos = start % n
    i = 0
    while i < m:
        take = min(m - i, n - pos)
        buf[pos:pos + take, channel] += sig[i:i + take]
        i += take
        pos = 0


class Bus:
    """Стерео-шина: сложение нот с панорамой и с панорамой по каналам."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.data = np.zeros((n, 2), dtype=np.float64)

    def add(self, t: float, sig: np.ndarray, gain: float = 1.0, pan: float = 0.0) -> None:
        if gain == 0.0 or sig.size == 0:
            return
        gl, gr = pan_gains(pan)
        start = int(round(float(t) * SR))
        add_wrapped(self.data, start, sig * (gain * gl), 0)
        add_wrapped(self.data, start, sig * (gain * gr), 1)

    def add_stereo(self, t: float, sig: np.ndarray, gain: float = 1.0) -> None:
        if gain == 0.0 or sig.size == 0:
            return
        start = int(round(float(t) * SR))
        add_wrapped(self.data, start, sig[:, 0] * gain, 0)
        add_wrapped(self.data, start, sig[:, 1] * gain, 1)


@dataclass
class Ctx:
    """Сетка лупа: темп, число тактов, детерминированный ГПСЧ."""

    bpm: int
    bars: int
    seed: int = 0
    beats_per_bar: int = BEATS_PER_BAR

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.seed)

    @property
    def beat_len(self) -> float:
        return 60.0 / float(self.bpm)

    @property
    def bar_len(self) -> float:
        return self.beat_len * self.beats_per_bar

    @property
    def duration(self) -> float:
        return self.bars * self.bar_len

    @property
    def n(self) -> int:
        return n_samples(self.duration)

    def at(self, bar: int, beat: float = 0.0) -> float:
        return (float(bar) * self.beats_per_bar + float(beat)) * self.beat_len

    def jitter(self, amount: float = 0.004) -> float:
        """Лёгкий человеческий разброс тайминга."""
        return float(self.rng.uniform(-amount, amount))


# ---------------------------------------------------------------------------
# Осцилляторы и голоса
# ---------------------------------------------------------------------------


def _harmonic_limit(freq: float, top: float = 0.45 * SR) -> int:
    return max(1, int(top / max(freq, 1e-6)))


def additive(
    freq: float,
    n: int,
    amps: Sequence[float],
    *,
    phase0: float = 0.0,
    vibrato_hz: float = 0.0,
    vibrato_rad: float = 0.0,
    decay_taus: Sequence[float] | None = None,
    inharmonicity: float = 0.0,
) -> np.ndarray:
    """Аддитивный синтез без алиасинга: сумма k-х гармоник с весами amps[k-1]."""
    t = np.arange(n, dtype=np.float64) / SR
    limit = _harmonic_limit(freq)
    out = np.zeros(n, dtype=np.float64)
    for index, amp in enumerate(amps, start=1):
        if index > limit or amp == 0.0:
            break
        detune = 1.0 + inharmonicity * index * index
        phase = 2.0 * math.pi * freq * detune * t + phase0
        if vibrato_rad:
            phase = phase + vibrato_rad * np.sin(2.0 * math.pi * vibrato_hz * t)
        sig = np.sin(index * phase)
        if decay_taus is not None:
            sig = sig * np.exp(-t / max(decay_taus[index - 1], 1e-4))
        out += amp * sig
    return out


def _normalize_voice(sig: np.ndarray, peak: float = 0.9) -> np.ndarray:
    m = float(np.max(np.abs(sig))) if sig.size else 0.0
    if m <= 1e-12:
        return sig
    return sig * (peak / m)


def voice_pad(freq: float, dur: float, vel: float = 1.0, *, tone: float = 0.55, seed: int = 0) -> np.ndarray:
    """Тёплый струнно-синтовый пэд: три расстроенных слоя, медленная атака."""
    n = n_samples(dur)
    env = env_shape(n, attack=min(0.85, dur * 0.4), decay=min(0.7, dur * 0.28),
                    sustain=0.86, release=min(1.15, dur * 0.45))
    out = np.zeros(n, dtype=np.float64)
    for layer, cents in enumerate((-7.0, 0.0, 6.0)):
        f = freq * 2.0 ** (cents / 1200.0)
        top = _harmonic_limit(f)
        count = max(3, min(14 if tone > 0.4 else 9, top))
        amps = [1.0 / (k ** 1.55) for k in range(1, count + 1)]
        out += additive(f, n, amps, phase0=seed * 0.73 + layer * 2.4) * (1.0 if layer == 1 else 0.72)
    out = _normalize_voice(out, 0.85)
    return declick(out * env * vel, 0.004, 0.01)


def voice_string(freq: float, dur: float, vel: float = 1.0, *, seed: int = 0) -> np.ndarray:
    """Струнная секция: вибрато и четыре расстроенных голоса — кинематографично."""
    n = n_samples(dur)
    env = env_shape(n, attack=min(0.7, dur * 0.3), decay=min(0.9, dur * 0.3),
                    sustain=0.9, release=min(1.6, dur * 0.45))
    out = np.zeros(n, dtype=np.float64)
    for layer, cents in enumerate((-9.0, -3.0, 3.0, 9.0)):
        f = freq * 2.0 ** (cents / 1200.0)
        top = _harmonic_limit(f)
        count = max(3, min(16, top))
        amps = [1.0 / (k ** 1.4) for k in range(1, count + 1)]
        out += additive(
            f, n, amps,
            phase0=seed * 1.31 + layer * 1.7,
            vibrato_hz=4.7 + 0.45 * layer,
            vibrato_rad=0.09,
        ) * (1.0 if layer in (1, 2) else 0.7)
    out = _normalize_voice(out, 0.8)
    return declick(out * env * vel, 0.005, 0.012)


def voice_rhodes(freq: float, dur: float, vel: float = 1.0, *, seed: int = 0) -> np.ndarray:
    """Электропиано в духе Rhodes: FM-тин сверху + тёплая гармоническая основа.

    Гармоническая основа важна: без неё аккорд в низкой тесситуре звучит мутно
    и на мастер-шине остаётся только низ.
    """
    n = n_samples(dur)
    t = np.arange(n, dtype=np.float64) / SR
    env = env_shape(n, attack=0.004, decay=min(0.9, dur * 0.5), sustain=0.26,
                    release=min(0.4, dur * 0.45))
    index = 1.9 * np.exp(-t / 0.3)
    mod = index * np.sin(2.0 * math.pi * freq * 2.0 * t + 0.4 + seed * 0.13)
    tine = np.sin(2.0 * math.pi * freq * t + mod)
    top = _harmonic_limit(freq)
    count = max(3, min(9, top))
    amps = [1.0, 0.45, 0.26, 0.16, 0.1, 0.07, 0.05, 0.035, 0.025][:count]
    taus = [max(0.12, dur * 0.9 / (1.0 + 0.45 * (k - 1))) for k in range(1, count + 1)]
    body = additive(freq, n, amps, phase0=seed * 0.29, decay_taus=taus)
    out = _normalize_voice(tine * 0.75 + body * 0.6, 0.85)
    return declick(out * env * vel, 0.003, 0.01)


def voice_piano(freq: float, dur: float, vel: float = 1.0, *, hard: float = 0.7, seed: int = 0) -> np.ndarray:
    """Фортепиано: негармоничные партиалы с разными скоростями затухания."""
    n = n_samples(dur)
    t = np.arange(n, dtype=np.float64) / SR
    top = _harmonic_limit(freq)
    count = max(4, min(16, top))
    amps: list[float] = []
    taus: list[float] = []
    base_tau = 1.7 * (220.0 / max(freq, 30.0)) ** 0.32
    for k in range(1, count + 1):
        amps.append((1.0 / k ** 1.12) * math.exp(-k * 0.05 * (1.25 - hard)))
        taus.append(max(0.05, base_tau / (1.0 + 0.5 * (k - 1))))
    body = additive(freq, n, amps, phase0=seed * 0.41, decay_taus=taus, inharmonicity=0.00035)
    # удар молоточка: короткий шумовой щелчок, отфильтрованный и тихий
    hammer = np.random.default_rng(seed + 7717).standard_normal(min(n, n_samples(0.012)))
    hammer = filter_once(hammer, lo=1800.0, hi=6500.0, order=1) * np.exp(-np.arange(len(hammer)) / (0.0015 * SR))
    body[: len(hammer)] += hammer * 0.05 * (0.6 + 0.4 * hard)
    env = env_shape(n, attack=0.003, decay=0.02, sustain=0.999, release=min(0.16, dur * 0.2))
    out = _normalize_voice(body, 0.92)
    return declick(out * env * vel, 0.002, 0.012)


def voice_pluck(freq: float, dur: float, vel: float = 1.0, *, tone: float = 0.5, seed: int = 0) -> np.ndarray:
    """Короткий щипковый призвук: арпеджио в подкасте и минимал-техе."""
    n = n_samples(dur)
    t = np.arange(n, dtype=np.float64) / SR
    top = _harmonic_limit(freq)
    count = max(3, min(11, int(5200.0 / max(freq, 40.0)), top))
    amps = []
    taus = []
    for k in range(1, count + 1):
        amps.append(1.0 / k ** (1.25 + 0.35 * tone))
        taus.append(max(0.04, dur * 0.42 / (1.0 + 0.75 * (k - 1))))
    body = additive(freq, n, amps, phase0=seed * 0.9, decay_taus=taus)
    env = env_shape(n, attack=0.003, decay=0.06, sustain=0.16, release=min(0.2, dur * 0.5))
    out = _normalize_voice(body, 0.9)
    return declick(out * env * vel, 0.002, 0.012)


def voice_bell(freq: float, dur: float, vel: float = 1.0, *, ratio: float = 3.51, seed: int = 0) -> np.ndarray:
    """Колокол/гонг на FM: кинематографичные акценты и мелодия в трэпе."""
    n = n_samples(dur)
    t = np.arange(n, dtype=np.float64) / SR
    index = 2.6 * np.exp(-t / 0.5)
    mod = index * np.sin(2.0 * math.pi * freq * ratio * t + seed * 0.31)
    carrier = np.sin(2.0 * math.pi * freq * t + mod)
    shimmer = 0.18 * np.sin(2.0 * math.pi * freq * 2.01 * t + mod * 0.4)
    env = env_shape(n, attack=0.005, decay=dur * 0.4, sustain=0.12,
                    release=min(0.9, dur * 0.45))
    out = _normalize_voice(carrier + shimmer, 0.9)
    return declick(out * env * vel, 0.003, 0.015)


def voice_glock(freq: float, dur: float, vel: float = 1.0, *, seed: int = 0) -> np.ndarray:
    """Яркий колокольчик: дублирует мелодию на октаву выше в поп-треке."""
    n = n_samples(dur)
    t = np.arange(n, dtype=np.float64) / SR
    index = 1.6 * np.exp(-t / 0.16)
    mod = index * np.sin(2.0 * math.pi * freq * 5.01 * t)
    carrier = np.sin(2.0 * math.pi * freq * t + mod)
    env = env_perc(n, attack=0.002, tau=max(0.12, dur * 0.35))
    out = _normalize_voice(carrier, 0.9)
    return declick(out * env * vel, 0.0015, 0.01)


def voice_drone(freq: float, dur: float, vel: float = 1.0, *, tone: float = 0.3) -> np.ndarray:
    """Низкий дрон: синус + чуть гармоник, очень медленные атака и спад."""
    n = n_samples(dur)
    env = env_shape(n, attack=min(1.6, dur * 0.3), decay=0.4, sustain=0.95,
                    release=min(2.0, dur * 0.35))
    amps = [1.0, 0.22 * tone, 0.1 * tone, 0.05 * tone]
    body = additive(freq, n, amps, phase0=0.77)
    body += 0.35 * additive(freq * 2.0, n, [1.0, 0.2 * tone], phase0=2.1)
    out = _normalize_voice(body, 0.9)
    return declick(out * env * vel, 0.006, 0.02)


def voice_bass(freq: float, dur: float, vel: float = 1.0, *, tone: float = 0.45, seed: int = 0) -> np.ndarray:
    """Тёплый бас: синус + мягкие гармоники и лёгкий драйв."""
    n = n_samples(dur)
    env = env_shape(n, attack=0.008, decay=min(0.35, dur * 0.35), sustain=0.78,
                    release=min(0.18, dur * 0.4))
    body = additive(freq, n, [1.0, 0.3 * tone, 0.14 * tone * tone, 0.05 * tone],
                    phase0=seed * 0.37)
    body = np.tanh(body * 1.25) / 1.25
    out = _normalize_voice(body, 0.9)
    return declick(out * env * vel, 0.003, 0.012)


def voice_808(freq: float, dur: float, vel: float = 1.0, *, glide: float = 0.06, seed: int = 0) -> np.ndarray:
    """808-бас: глиссандо по высоте + насыщение, длинный хвост."""
    n = n_samples(dur)
    t = np.arange(n, dtype=np.float64) / SR
    pitch = freq * (1.0 + glide * np.exp(-t / 0.045))
    phase = 2.0 * math.pi * np.cumsum(pitch) / SR
    body = np.sin(phase) + 0.14 * np.sin(2.0 * phase + seed * 0.2)
    body = np.tanh(2.3 * body) / np.tanh(2.3)
    env = env_shape(n, attack=0.002, decay=0.08, sustain=0.92, release=min(0.14, dur * 0.3))
    out = _normalize_voice(body, 0.95)
    return declick(out * env * vel, 0.0015, 0.01)


def voice_lead(freq: float, dur: float, vel: float = 1.0, *, seed: int = 0) -> np.ndarray:
    """Яркий лид поп-трека: пила с вибрато, без алиасинга (аддитивно)."""
    n = n_samples(dur)
    env = env_shape(n, attack=0.012, decay=min(0.3, dur * 0.3), sustain=0.8,
                    release=min(0.22, dur * 0.4))
    top = _harmonic_limit(freq)
    count = max(4, min(18, top))
    amps = [1.0 / k ** 1.05 for k in range(1, count + 1)]
    body = additive(freq, n, amps, phase0=seed * 0.6, vibrato_hz=5.1, vibrato_rad=0.07)
    body = np.tanh(body * 1.15) / 1.15
    out = _normalize_voice(body, 0.9)
    return declick(out * env * vel, 0.003, 0.014)


# ---------------------------------------------------------------------------
# Ударные (тоже полностью синтетические)
# ---------------------------------------------------------------------------


def drum_kick(
    rng: np.random.Generator,
    vel: float = 1.0,
    *,
    length: float = 0.5,
    tone: float = 1.0,
    decay: float | None = None,
) -> np.ndarray:
    n = n_samples(length)
    t = np.arange(n, dtype=np.float64) / SR
    f0 = 118.0 + 16.0 * tone
    f1 = 44.0 + 6.0 * tone
    pitch = f1 + (f0 - f1) * np.exp(-t / 0.035)
    phase = 2.0 * math.pi * np.cumsum(pitch) / SR
    tau = float(decay) if decay is not None else (0.16 + 0.08 * tone)
    body = np.sin(phase) * np.exp(-t / tau)
    click = rng.standard_normal(min(n, n_samples(0.01))) * np.exp(-np.arange(min(n, n_samples(0.01))) / (0.0022 * SR))
    click = filter_once(click, lo=1200.0, hi=7000.0, order=1) * 0.28
    out = np.zeros(n, dtype=np.float64)
    out[: len(click)] += click
    out += body
    out = np.tanh(1.6 * out) / 1.6
    return declick(_normalize_voice(out, 0.95) * vel, 0.001, 0.01)


def drum_boom(rng: np.random.Generator, vel: float = 1.0, *, length: float = 1.6) -> np.ndarray:
    """Кинематографичный низкий «бум»: саб-свип плюс гул."""
    n = n_samples(length)
    t = np.arange(n, dtype=np.float64) / SR
    pitch = 28.0 + 34.0 * np.exp(-t / 0.09)
    phase = 2.0 * math.pi * np.cumsum(pitch) / SR
    body = np.sin(phase) * np.exp(-t / 0.55)
    rumble = filter_once(rng.standard_normal(n), lo=40.0, hi=180.0, order=2) * np.exp(-t / 0.35) * 0.5
    out = np.tanh(1.4 * (body + rumble)) / 1.4
    return declick(_normalize_voice(out, 0.95) * vel, 0.002, 0.03)


def drum_taiko(rng: np.random.Generator, vel: float = 1.0, *, length: float = 0.9) -> np.ndarray:
    n = n_samples(length)
    t = np.arange(n, dtype=np.float64) / SR
    pitch = 92.0 + 60.0 * np.exp(-t / 0.05)
    phase = 2.0 * math.pi * np.cumsum(pitch) / SR
    body = np.sin(phase) * np.exp(-t / 0.22)
    skin = filter_once(rng.standard_normal(n), lo=180.0, hi=1200.0, order=1) * np.exp(-t / 0.09) * 0.35
    out = np.tanh(1.3 * (body + skin)) / 1.3
    return declick(_normalize_voice(out, 0.95) * vel, 0.0015, 0.02)


def drum_snare(rng: np.random.Generator, vel: float = 1.0, *, length: float = 0.38) -> np.ndarray:
    n = n_samples(length)
    t = np.arange(n, dtype=np.float64) / SR
    noise = filter_once(rng.standard_normal(n), lo=900.0, hi=7500.0, order=2)
    noise *= np.exp(-t / 0.13)
    tone = np.sin(2.0 * math.pi * (188.0 * np.exp(-t / 0.06)) * t) * np.exp(-t / 0.07) * 0.45
    out = np.tanh(1.3 * (noise * 0.85 + tone)) / 1.3
    return declick(_normalize_voice(out, 0.95) * vel, 0.0015, 0.015)


def drum_clap(rng: np.random.Generator, vel: float = 1.0, *, length: float = 0.4) -> np.ndarray:
    n = n_samples(length)
    t = np.arange(n, dtype=np.float64) / SR
    noise = filter_once(rng.standard_normal(n), lo=1100.0, hi=6500.0, order=2)
    # три коротких «хлопка» подряд + общий хвост — типичная атака клэпа
    bursts = np.zeros(n, dtype=np.float64)
    for index, delay in enumerate((0.0, 0.009, 0.019)):
        head = np.exp(-((t - delay) / 0.0055) ** 2)
        if index == 0:
            head = np.exp(-t / 0.0055)
        bursts += head
    bursts += 0.25 * np.exp(-t / 0.11)
    out = np.tanh(1.35 * (noise * bursts)) / 1.35
    return declick(_normalize_voice(out, 0.95) * vel, 0.001, 0.02)


def drum_hat(rng: np.random.Generator, vel: float = 1.0, *, open_hat: bool = False) -> np.ndarray:
    n = n_samples(0.28 if open_hat else 0.09)
    t = np.arange(n, dtype=np.float64) / SR
    noise = filter_once(rng.standard_normal(n), lo=7200.0, hi=16000.0, order=2)
    tau = 0.16 if open_hat else 0.032
    out = noise * np.exp(-t / tau)
    return declick(_normalize_voice(out, 0.95) * vel, 0.0008, 0.012)


def drum_shaker(rng: np.random.Generator, vel: float = 1.0, *, length: float = 0.12) -> np.ndarray:
    n = n_samples(length)
    t = np.arange(n, dtype=np.float64) / SR
    noise = filter_once(rng.standard_normal(n), lo=3400.0, hi=10500.0, order=2)
    env = np.exp(-t / 0.05)
    env[: n_samples(0.012)] *= _raised_cos(n_samples(0.012))
    out = noise * env
    return declick(_normalize_voice(out, 0.95) * vel, 0.001, 0.015)


def drum_rim(rng: np.random.Generator, vel: float = 1.0, *, length: float = 0.07) -> np.ndarray:
    n = n_samples(length)
    t = np.arange(n, dtype=np.float64) / SR
    click = filter_once(rng.standard_normal(n), lo=1800.0, hi=5200.0, order=2) * np.exp(-t / 0.012)
    body = np.sin(2.0 * math.pi * 430.0 * t) * np.exp(-t / 0.018) * 0.5
    out = click + body
    return declick(_normalize_voice(out, 0.95) * vel, 0.0008, 0.01)


def drum_tick(rng: np.random.Generator, vel: float = 1.0, *, length: float = 0.1) -> np.ndarray:
    n = n_samples(length)
    t = np.arange(n, dtype=np.float64) / SR
    click = filter_once(rng.standard_normal(n), lo=4200.0, hi=9500.0, order=2) * np.exp(-t / 0.014)
    return declick(_normalize_voice(click, 0.95) * vel, 0.0006, 0.008)


def drum_crash(rng: np.random.Generator, vel: float = 1.0, *, length: float = 1.3) -> np.ndarray:
    n = n_samples(length)
    t = np.arange(n, dtype=np.float64) / SR
    noise = filter_once(rng.standard_normal(n), lo=5200.0, hi=15000.0, order=2)
    out = noise * np.exp(-t / 0.45)
    out[: n_samples(0.002)] *= _raised_cos(n_samples(0.002))
    return declick(_normalize_voice(out, 0.95) * vel, 0.001, 0.05)


# ---------------------------------------------------------------------------
# FFT-эффекты (всё циклическое: луп не рвётся на стыке)
# ---------------------------------------------------------------------------


def _as_2d_spectrum(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.fft.rfft(x, axis=0), np.fft.rfftfreq(x.shape[0], 1.0 / SR)


def fft_lowpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    spectrum, freqs = _as_2d_spectrum(x)
    h = 1.0 / np.sqrt(1.0 + (freqs / max(cutoff, 1.0)) ** (2 * order))
    return np.fft.irfft(spectrum * _shape(h, x), n=x.shape[0], axis=0)


def fft_highpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    spectrum, freqs = _as_2d_spectrum(x)
    safe = np.maximum(freqs, 1e-6)
    h = 1.0 / np.sqrt(1.0 + (max(cutoff, 1.0) / safe) ** (2 * order))
    return np.fft.irfft(spectrum * _shape(h, x), n=x.shape[0], axis=0)


def _shape(h: np.ndarray, x: np.ndarray) -> np.ndarray:
    return h[:, None] if x.ndim > 1 else h


def filter_once(x: np.ndarray, *, lo: float | None = None, hi: float | None = None, order: int = 2) -> np.ndarray:
    """Фильтр для one-shot: паддим нулями, чтобы хвост не заворачивался в начало."""
    n = x.shape[0]
    padded = np.concatenate([x, np.zeros(n, dtype=np.float64)])
    if hi is not None:
        padded = fft_lowpass(padded, hi, order)
    if lo is not None:
        padded = fft_highpass(padded, lo, order)
    return padded[:n]


def _biquad_magnitude(freqs: np.ndarray, kind: str, f0: float, gain_db: float, q: float) -> np.ndarray:
    """АЧХ биквада по формулам RBJ, посчитанная сразу на всех FFT-бинах."""
    f0 = float(np.clip(f0, 10.0, SR * 0.49))
    w0 = 2.0 * math.pi * f0 / SR
    cos_w = math.cos(w0)
    sin_w = math.sin(w0)
    amplitude = 10.0 ** (gain_db / 40.0)

    if kind == "peak":
        alpha = sin_w / (2.0 * max(q, 0.1))
        b = [1.0 + alpha * amplitude, -2.0 * cos_w, 1.0 - alpha * amplitude]
        a = [1.0 + alpha / amplitude, -2.0 * cos_w, 1.0 - alpha / amplitude]
    elif kind == "low_shelf":
        sqrt_a = math.sqrt(amplitude)
        alpha = sin_w / 2.0 * math.sqrt(2.0)  # S = 1
        b = [
            amplitude * ((amplitude + 1.0) - (amplitude - 1.0) * cos_w + 2.0 * sqrt_a * alpha),
            2.0 * amplitude * ((amplitude - 1.0) - (amplitude + 1.0) * cos_w),
            amplitude * ((amplitude + 1.0) - (amplitude - 1.0) * cos_w - 2.0 * sqrt_a * alpha),
        ]
        a = [
            (amplitude + 1.0) + (amplitude - 1.0) * cos_w + 2.0 * sqrt_a * alpha,
            -2.0 * ((amplitude - 1.0) + (amplitude + 1.0) * cos_w),
            (amplitude + 1.0) + (amplitude - 1.0) * cos_w - 2.0 * sqrt_a * alpha,
        ]
    elif kind == "high_shelf":
        sqrt_a = math.sqrt(amplitude)
        alpha = sin_w / 2.0 * math.sqrt(2.0)
        b = [
            amplitude * ((amplitude + 1.0) + (amplitude - 1.0) * cos_w + 2.0 * sqrt_a * alpha),
            -2.0 * amplitude * ((amplitude - 1.0) + (amplitude + 1.0) * cos_w),
            amplitude * ((amplitude + 1.0) + (amplitude - 1.0) * cos_w - 2.0 * sqrt_a * alpha),
        ]
        a = [
            (amplitude + 1.0) - (amplitude - 1.0) * cos_w + 2.0 * sqrt_a * alpha,
            2.0 * ((amplitude - 1.0) - (amplitude + 1.0) * cos_w),
            (amplitude + 1.0) - (amplitude - 1.0) * cos_w - 2.0 * sqrt_a * alpha,
        ]
    elif kind == "hp":
        alpha = sin_w / (2.0 * max(q, 0.1))
        b = [(1.0 + cos_w) / 2.0, -(1.0 + cos_w), (1.0 + cos_w) / 2.0]
        a = [1.0 + alpha, -2.0 * cos_w, 1.0 - alpha]
    elif kind == "lp":
        alpha = sin_w / (2.0 * max(q, 0.1))
        b = [(1.0 - cos_w) / 2.0, 1.0 - cos_w, (1.0 - cos_w) / 2.0]
        a = [1.0 + alpha, -2.0 * cos_w, 1.0 - alpha]
    else:
        return np.ones(freqs.shape[0], dtype=np.float64)

    z = np.exp(-1j * 2.0 * math.pi * freqs / SR)
    numerator = b[0] + b[1] * z + b[2] * z * z
    denominator = a[0] + a[1] * z + a[2] * z * z
    return np.abs(numerator / denominator)


def apply_eq(x: np.ndarray, bands: Sequence[tuple[str, float, float, float]]) -> np.ndarray:
    """Эквалайзер целым буфером: сумма биквадов на бинах rfft. Циклический."""
    if not bands:
        return x
    spectrum, freqs = _as_2d_spectrum(x)
    total = np.ones(freqs.shape[0], dtype=np.float64)
    for kind, f0, gain_db, q in bands:
        total *= _biquad_magnitude(freqs, kind, f0, gain_db, q)
    return np.fft.irfft(spectrum * _shape(total, x), n=x.shape[0], axis=0)


def make_ir(
    seconds: float,
    tau: float,
    *,
    hi: float = 6500.0,
    lo: float = 180.0,
    predelay: float = 0.018,
    seed: int = 0,
) -> np.ndarray:
    """Синтетический импульс: шум с экспоненциальным спадом и ранними отражениями."""
    n = n_samples(seconds)
    t = np.arange(n, dtype=np.float64) / SR
    rng = np.random.default_rng(seed)
    ir = np.zeros((n, 2), dtype=np.float64)
    for channel in range(2):
        noise = rng.standard_normal(n)
        noise = filter_once(noise, lo=lo, hi=hi, order=2)
        env = np.exp(-np.maximum(t - predelay, 0.0) / max(tau, 0.02))
        env[t < predelay] = 0.0
        env[: n_samples(0.004)] *= _raised_cos(n_samples(0.004))
        ir[:, channel] = noise * env
    # ранние отражения — они дают «объём помещения»
    taps = [(0.011, 0.7), (0.017, 0.55), (0.023, 0.62), (0.031, 0.45), (0.039, 0.5), (0.047, 0.36)]
    for index, (delay, gain) in enumerate(taps):
        position = n_samples(predelay + delay) % n
        ir[position, index % 2] += gain
        ir[(position + n_samples(0.002)) % n, (index + 1) % 2] += gain * 0.4
    ir[:, 0] = filter_once(ir[:, 0], lo=lo, hi=hi, order=1)
    ir[:, 1] = filter_once(ir[:, 1], lo=lo, hi=hi, order=1)
    energy = math.sqrt(float(np.sum(ir * ir)) + 1e-12)
    return ir / energy


def reverb_send(dry: np.ndarray, ir: np.ndarray, wet: float) -> np.ndarray:
    """Циклическая свёртка: хвост реверба заворачивается в начало лупа — бесшовно."""
    n = dry.shape[0]
    padded = np.zeros((n, 2), dtype=np.float64)
    m = min(n, ir.shape[0])
    padded[:m] = ir[:m]
    out = np.zeros((n, 2), dtype=np.float64)
    for channel in range(2):
        conv = np.fft.irfft(
            np.fft.rfft(dry[:, channel]) * np.fft.rfft(padded[:, channel]), n=n
        )
        out[:, channel] = conv
    return out * wet


def fft_delay(x: np.ndarray, delay_sec: float, *, feedback: float = 0.3, mix: float = 0.25, damp_hz: float = 3800.0) -> np.ndarray:
    """Дилей с обратной связью прямо в частотной области: геометрическая серия.

    H = 1 / (1 - fb * L(f) * exp(-j*w*d)) — точный бесконечный фидбэк,
    завёрнутый по кругу. Обратная связь с демпфированием, поэтому петля стабильна.
    """
    n = x.shape[0]
    spectrum, freqs = _as_2d_spectrum(x)
    damp = 1.0 / np.sqrt(1.0 + (freqs / max(damp_hz, 200.0)) ** 2)
    z = np.exp(-2j * math.pi * freqs * float(delay_sec))
    h = 1.0 / (1.0 - feedback * damp * z)
    wet = np.fft.irfft(spectrum * _shape(h, x), n=n, axis=0)
    return x + mix * wet


def sidechain_gain(
    n: int,
    triggers: Sequence[float],
    *,
    depth: float = 0.3,
    release: float = 0.24,
    attack: float = 0.006,
) -> np.ndarray:
    """Мягкий сайдчейн: огибающая от ударов бочки, завёрнутая по кругу."""
    if not triggers:
        return np.ones(n, dtype=np.float64)
    impulses = np.zeros(n, dtype=np.float64)
    for moment in triggers:
        impulses[int(round(float(moment) * SR)) % n] += 1.0
    t = np.arange(n, dtype=np.float64) / SR
    decay = np.exp(-t / max(release, 0.01))
    envelope = np.fft.irfft(np.fft.rfft(impulses) * np.fft.rfft(decay), n=n)
    peak = float(np.max(envelope))
    if peak > 1e-9:
        envelope /= peak
    n_attack = max(1, n_samples(attack))
    smooth = np.ones(n_attack, dtype=np.float64) / float(n_attack)
    envelope = np.fft.irfft(
        np.fft.rfft(envelope) * np.fft.rfft(np.concatenate([smooth, np.zeros(n - n_attack)])),
        n=n,
    )
    envelope = np.clip(envelope / max(float(np.max(envelope)), 1e-9), 0.0, 1.0)
    return 1.0 - float(depth) * envelope


def widen(x: np.ndarray, width: float = 1.1) -> np.ndarray:
    """Расширение стерео через mid/side; моно-совместимость сохраняем."""
    mid = 0.5 * (x[:, 0] + x[:, 1])
    side = 0.5 * (x[:, 0] - x[:, 1]) * float(width)
    return np.stack([mid + side, mid - side], axis=1)


def mono_bass(x: np.ndarray, cutoff: float = 110.0) -> np.ndarray:
    """Ниже cutoff — в моно: бас не «прыгает» и не разваливается на телефоне."""
    low = fft_lowpass(x, cutoff, order=2)
    mono = 0.5 * (low[:, 0] + low[:, 1])
    out = x - low
    out[:, 0] += mono
    out[:, 1] += mono
    return out


def vinyl_bed(n: int, rng: np.random.Generator, level: float = 0.0035) -> np.ndarray:
    """Винниловый шум: шипение + редкие трескучие импульсы. Синтетика, не сэмпл."""
    hiss = rng.standard_normal((n, 2))
    hiss = fft_lowpass(fft_highpass(hiss, 300.0, 1), 9000.0, 1)
    out = hiss * level * 0.5
    crackle = np.zeros(n, dtype=np.float64)
    count = max(1, int(n / SR * 26.0))
    positions = rng.integers(0, n, count)
    for pos in positions:
        length = int(rng.integers(8, 40))
        burst = rng.standard_normal(length) * np.exp(-np.arange(length) / (0.0009 * SR))
        end = min(n, pos + length)
        crackle[pos:end] += burst[: end - pos]
    crackle = fft_lowpass(crackle, 6000.0, 1) * 0.55
    out[:, 0] += crackle * level * 8.0
    out[:, 1] += crackle * level * 8.0 * 0.92
    return out


def noise_riser(rng: np.random.Generator, seconds: float, *, lo: float = 350.0, hi: float = 7200.0) -> np.ndarray:
    """Восходящий шумовой свист: воздушный, с плавными краями."""
    n = n_samples(seconds)
    t = np.arange(n, dtype=np.float64) / SR
    noise = filter_once(rng.standard_normal(n), lo=lo, hi=hi, order=2)
    envelope = (np.arange(n) / max(1, n - 1)) ** 2.2
    envelope[: n_samples(0.03)] *= _raised_cos(n_samples(0.03))
    envelope[-n_samples(0.03):] *= _raised_cos(n_samples(0.03))[::-1]
    return noise * envelope


# ---------------------------------------------------------------------------
# Мастеринг и экспорт
# ---------------------------------------------------------------------------


@dataclass
class TrackSpec:
    id: str
    title: str
    mood: str
    bpm: int
    bars: int
    seed: int
    build: Callable[[Ctx], tuple[Bus, Bus, Bus]]
    lufs: float = TARGET_LUFS
    width: float = 1.1
    drive: float = 1.0
    eq: Sequence[tuple[str, float, float, float]] = field(default_factory=tuple)
    reverb_seconds: float = 1.6
    reverb_tau: float = 0.5
    reverb_wet: float = 0.28
    reverb_hi: float = 6200.0
    reverb_lo: float = 180.0
    reverb_predelay: float = 0.02
    drum_reverb_send: float = 0.35
    # Целевые RMS шин в дБ: убирают «бас съел микс» ещё до мастеринга.
    music_rms: float | None = None
    drums_rms: float | None = None
    fx_rms: float | None = None


def fit_rms(x: np.ndarray, target_db: float | None) -> np.ndarray:
    """Масштабирует шину к целевому RMS. None — оставить как есть."""
    if target_db is None or x.size == 0:
        return x
    rms = float(np.sqrt(np.mean(x ** 2)))
    if rms < 1e-9:
        return x
    return x * (db_to_gain(target_db) / rms)


def mix_track(ctx: Ctx, spec: TrackSpec) -> np.ndarray:
    music, drums, fx = spec.build(ctx)
    music.data = fit_rms(music.data, spec.music_rms)
    drums.data = fit_rms(drums.data, spec.drums_rms)
    fx.data = fit_rms(fx.data, spec.fx_rms)
    mix = music.data + drums.data + fx.data
    mix = apply_eq(mix, spec.eq)
    mix = mono_bass(mix, 110.0)
    if spec.reverb_wet > 0:
        ir = make_ir(
            spec.reverb_seconds,
            spec.reverb_tau,
            hi=spec.reverb_hi,
            lo=spec.reverb_lo,
            predelay=spec.reverb_predelay,
            seed=ctx.seed + 911,
        )
        send = music.data * 0.85 + fx.data * 0.55 + drums.data * spec.drum_reverb_send
        mix = mix + reverb_send(send, ir, spec.reverb_wet)
    mix = widen(mix, spec.width)
    if spec.drive > 1.0:
        mix = np.tanh(spec.drive * mix) / math.tanh(spec.drive)
    return mix


def normalize_peak(x: np.ndarray, peak_db: float) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= 1e-12:
        return x
    return x * (db_to_gain(peak_db) / peak)


def pcm_bytes(x: np.ndarray) -> bytes:
    clipped = np.clip(x, -1.0, 1.0)
    return np.ascontiguousarray(clipped, dtype="<f4").tobytes()


def find_ffmpeg() -> str:
    for name in ("ffmpeg", "ffmpeg.exe"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (
        ROOT.parent / "Tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        Path(r"C:\Users\ogkysh\Tools\ffmpeg\bin\ffmpeg.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    raise SystemExit("ffmpeg не найден: положи его в PATH (нужен libmp3lame)")


def _run_ffmpeg(ffmpeg: str, args: Sequence[str], pcm: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", *args],
        input=pcm,
        capture_output=True,
    )


def measure_loudness(ffmpeg: str, audio: np.ndarray) -> tuple[float | None, float | None]:
    """LUFS (EBU R128) и true peak по мнению ffmpeg. None, если не распарсили."""
    result = _run_ffmpeg(
        ffmpeg,
        ["-nostats", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "pipe:0",
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        pcm_bytes(audio),
    )
    text = result.stderr.decode("utf-8", errors="replace")
    lufs = re.findall(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", text)
    peak = re.findall(r"Peak:\s+(-?\d+(?:\.\d+)?)\s+dBFS", text)
    return (
        float(lufs[-1]) if lufs else None,
        float(peak[-1]) if peak else None,
    )


def encode_mp3(ffmpeg: str, audio: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_ffmpeg(
        ffmpeg,
        ["-y", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "pipe:0",
         "-c:a", "libmp3lame", "-b:a", "192k", "-ar", str(SR), "-ac", "2",
         "-map_metadata", "-1", "-id3v2_version", "0", "-write_xing", "1", str(path)],
        pcm_bytes(audio),
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg не закодировал {path.name}: {result.stderr.decode(errors='replace')[:400]}")


def decode_mp3(ffmpeg: str, path: Path) -> np.ndarray:
    """Декодирует mp3 обратно в float-стерео: проверяем то, что реально уедет в приложение."""
    result = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"ffmpeg не декодировал {path.name}")
    pcm = np.frombuffer(result.stdout, dtype="<f4")
    if pcm.size % 2:
        pcm = pcm[:-1]
    return pcm.reshape(-1, 2).astype(np.float64)


def measure_loudness_file(ffmpeg: str, path: Path) -> tuple[float | None, float | None]:
    result = _run_ffmpeg(
        ffmpeg,
        ["-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
    )
    text = result.stderr.decode("utf-8", errors="replace")
    lufs = re.findall(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", text)
    peak = re.findall(r"Peak:\s+(-?\d+(?:\.\d+)?)\s+dBFS", text)
    return (
        float(lufs[-1]) if lufs else None,
        float(peak[-1]) if peak else None,
    )


# Разбор mp3-фреймов без внешних библиотек: битрейт/частота/каналы/длительность.
_MP3_BITRATES = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
_MP3_RATES = [44100, 48000, 32000, 0]


def mp3_info(path: Path) -> dict:
    """Проверяет целостность mp3 по заголовкам фреймов (без ffmpeg)."""
    data = path.read_bytes()
    offset = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
        offset = 10 + size
    frames = 0
    first: dict | None = None
    while offset + 4 <= len(data):
        header = data[offset:offset + 4]
        if header[0] != 0xFF or (header[1] & 0xE0) != 0xE0:
            break
        version = (header[1] >> 3) & 0x03
        layer = (header[1] >> 1) & 0x03
        bitrate_index = (header[2] >> 4) & 0x0F
        rate_index = (header[2] >> 2) & 0x03
        padding = (header[2] >> 1) & 0x01
        mode = (header[3] >> 6) & 0x03
        if version != 3 or layer != 1 or bitrate_index in (0, 15) or rate_index == 3:
            break
        bitrate = _MP3_BITRATES[bitrate_index]
        rate = _MP3_RATES[rate_index]
        frame_length = 144 * bitrate * 1000 // rate + padding
        if frame_length <= 0:
            break
        info = {"bitrate": bitrate, "sr": rate, "channels": 1 if mode == 3 else 2}
        if first is None:
            first = info
        elif info != first:
            raise ValueError(f"{path.name}: фреймы разного формата на {offset}")
        frames += 1
        offset += frame_length
    if not frames or first is None:
        raise ValueError(f"{path.name}: не найдено ни одного mp3-фрейма")
    duration = frames * 1152.0 / float(first["sr"])
    return {**first, "frames": frames, "duration": duration, "bytes": len(data)}


def seam_metric(audio: np.ndarray) -> float:
    """Во сколько раз скачок на стыке лупа больше типичного скачка сигнала (<=1 — ок)."""
    jumps = np.abs(np.diff(audio, axis=0))
    seam = float(np.max(np.abs(audio[0] - audio[-1])))
    typical = float(np.max(np.percentile(jumps, 99.9, axis=0)))
    return seam / max(typical, 1e-9)


def band_energy_share(audio: np.ndarray) -> dict[str, float]:
    """Доли энергии по полосам — проверка «не бубнит / не шипит» без прослушивания."""
    mono = 0.5 * (audio[:, 0] + audio[:, 1])
    spectrum = np.abs(np.fft.rfft(mono)) ** 2
    freqs = np.fft.rfftfreq(audio.shape[0], 1.0 / SR)
    total = float(np.sum(spectrum)) + 1e-12
    bands = {
        "sub": (20.0, 60.0),
        "low": (60.0, 250.0),
        "mid": (250.0, 2000.0),
        "high": (2000.0, 8000.0),
        "air": (8000.0, 16000.0),
    }
    return {
        name: 100.0 * float(np.sum(spectrum[(freqs >= lo) & (freqs < hi)])) / total
        for name, (lo, hi) in bands.items()
    }


def render_track(ffmpeg: str, spec: TrackSpec, out_dir: Path = OUT_DIR) -> dict:
    ctx = Ctx(bpm=spec.bpm, bars=spec.bars, seed=spec.seed)
    mix = mix_track(ctx, spec)
    mix = normalize_peak(mix, -3.0)

    lufs0, _ = measure_loudness(ffmpeg, mix)
    gain_db = spec.lufs - lufs0 if lufs0 is not None else 0.0
    mix = mix * db_to_gain(max(-12.0, min(12.0, gain_db)))

    peak_before = gain_to_db(float(np.max(np.abs(mix))))
    if peak_before > PEAK_CEILING_DB:
        mix = mix * db_to_gain(PEAK_CEILING_DB - peak_before)

    lufs, true_peak = measure_loudness(ffmpeg, mix)
    peak_db = gain_to_db(float(np.max(np.abs(mix))))
    seam = seam_metric(mix)
    bands = band_energy_share(mix)
    rms_db = gain_to_db(float(np.sqrt(np.mean(mix ** 2))))

    mp3_path = out_dir / f"{spec.id}.mp3"
    encode_mp3(ffmpeg, mix, mp3_path)

    # Проверяем ровно то, что уедет в приложение: декодированный mp3.
    decoded = decode_mp3(ffmpeg, mp3_path)
    decoded_peak = gain_to_db(float(np.max(np.abs(decoded))))
    # mp3 может чуть поднять пик на декоде — при необходимости дожимаем и кодируем снова
    for _ in range(2):
        if decoded_peak <= -1.0:
            break
        mix = mix * db_to_gain(-1.2 - decoded_peak)
        encode_mp3(ffmpeg, mix, mp3_path)
        decoded = decode_mp3(ffmpeg, mp3_path)
        decoded_peak = gain_to_db(float(np.max(np.abs(decoded))))
    decoded_lufs, decoded_true_peak = measure_loudness_file(ffmpeg, mp3_path)
    decoded_seam = seam_metric(decoded)
    info = mp3_info(mp3_path)

    return {
        "id": spec.id,
        "title": spec.title,
        "mood": spec.mood,
        "bpm": spec.bpm,
        "bars": spec.bars,
        "duration": round(info["duration"], 2),
        "path": mp3_path,
        "bytes": info["bytes"],
        "lufs": lufs,
        "peak_db": peak_db,
        "true_peak": true_peak,
        "rms_db": rms_db,
        "seam": seam,
        "bands": bands,
        "mp3": info,
        "synth_duration": round(ctx.duration, 3),
        "decoded_peak": decoded_peak,
        "decoded_lufs": decoded_lufs,
        "decoded_true_peak": decoded_true_peak,
        "decoded_seam": decoded_seam,
    }


def log_track(entry: dict) -> None:
    bands = entry["bands"]
    lufs = f"{entry['lufs']:.1f}" if entry["lufs"] is not None else "?"
    tp = f"{entry['true_peak']:.2f}" if entry["true_peak"] is not None else "?"
    dp = f"{entry['decoded_peak']:.2f}" if entry.get("decoded_peak") is not None else "?"
    dl = f"{entry['decoded_lufs']:.1f}" if entry.get("decoded_lufs") is not None else "?"
    print(
        f"  {entry['id']:<16} {entry['duration']:6.2f} с  {entry['bpm']:>3} BPM  "
        f"{entry['bytes'] / 1024:6.0f} КБ  "
        f"LUFS {lufs:>6}  пик {entry['peak_db']:6.2f} ({tp})  "
        f"RMS {entry['rms_db']:6.2f}  шов x{entry['seam']:.2f}  "
        f"декод: пик {dp:>6} LUFS {dl:>6} шов x{entry.get('decoded_seam', 0):.2f}  "
        f"профиль {bands['sub']:.0f}/{bands['low']:.0f}/{bands['mid']:.0f}/"
        f"{bands['high']:.0f}/{bands['air']:.0f}%"
    )


def write_manifest(entries: Sequence[dict], out_dir: Path) -> Path:
    manifest = [
        {
            "id": entry["id"],
            "title": entry["title"],
            "mood": entry["mood"],
            "bpm": entry["bpm"],
            "duration": entry["duration"],
            "license": LICENSE,
            "file": f"{entry['id']}.mp3",
        }
        for entry in entries
    ]
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Аранжировки
# ---------------------------------------------------------------------------


def _build_lofi(ctx: Ctx) -> tuple[Bus, Bus, Bus]:
    """Lofi chill: Fmaj9 – Dm9 – Bbmaj9 – C9, свинг, Rhodes, виннил."""
    beat = ctx.beat_len
    music, drums, fx = Bus(ctx.n), Bus(ctx.n), Bus(ctx.n)
    rng = ctx.rng

    chords = [
        [53, 57, 60, 64, 67],  # Fmaj9
        [50, 53, 57, 60, 64],  # Dm9
        [46, 50, 53, 57, 60],  # Bbmaj9
        [48, 52, 55, 58, 62],  # C9
        [53, 57, 60, 64, 67],
        [50, 53, 57, 60, 64],
        [46, 50, 53, 57, 60],
        [48, 53, 55, 58, 60],  # C7sus4 — тяга в Fmaj9 на стыке лупа
    ]
    roots = [41, 38, 34, 36, 41, 38, 34, 36]
    kicks: list[float] = []

    for bar in range(8):
        base = ctx.at(bar)
        tones = chords[bar]
        for beat_pos, dur_b, vel in ((0.0, 2.2, 0.92), (2.5, 1.25, 0.55)):
            moment = base + beat_pos * beat + ctx.jitter(0.004)
            for index, note in enumerate(tones):
                pan = -0.13 + 0.26 * (index / max(1, len(tones) - 1))
                note_vel = vel * float(rng.uniform(0.9, 1.05))
                music.add(
                    moment,
                    voice_rhodes(midi_hz(note), dur_b * beat, note_vel, seed=bar * 7 + index),
                    gain=0.26,
                    pan=pan,
                )
            # октавное эхо верхней ноты: даёт «воздух» и середину аккорду
            music.add(
                moment,
                voice_rhodes(midi_hz(tones[-1] + 12), dur_b * beat * 0.9, vel * 0.34, seed=bar * 3 + 1),
                gain=0.09,
                pan=0.34,
            )
        root = roots[bar]
        for beat_pos, dur_b, vel in ((0.0, 1.7, 0.95), (2.5, 0.85, 0.68)):
            music.add(
                base + beat_pos * beat,
                voice_bass(midi_hz(root), dur_b * beat, vel, tone=0.45, seed=bar),
                gain=0.14,
            )
        for beat_pos, vel in ((0.0, 1.0), (2.5, 0.82)):
            moment = base + beat_pos * beat + ctx.jitter(0.005)
            drums.add(moment, drum_kick(rng, vel, decay=0.2), gain=0.46)
            kicks.append(moment)
        for beat_pos, vel in ((1.0, 0.8), (3.0, 0.92)):
            drums.add(
                base + beat_pos * beat + ctx.jitter(0.006),
                drum_snare(rng, vel),
                gain=0.5,
                pan=float(rng.uniform(-0.07, 0.07)),
            )
        # восьмые со свингом: нечётные слегка запаздывают
        for step in range(8):
            position = step * 0.5 + (0.11 if step % 2 else 0.0)
            if bar % 2 == 1 and step in (5, 7):
                continue
            velocity = (0.9, 0.42, 0.7, 0.38, 0.8, 0.4, 0.62, 0.35)[step]
            drums.add(
                base + position * beat + ctx.jitter(0.004),
                drum_hat(rng, velocity),
                gain=0.13,
                pan=float(rng.uniform(0.12, 0.3)),
            )

    melody = {
        4: [(0.0, 81, 1.0), (1.0, 84, 0.7), (1.5, 86, 0.9), (2.5, 84, 0.7)],
        5: [(0.0, 81, 1.4), (1.5, 77, 1.0), (2.5, 81, 0.8)],
        6: [(0.0, 84, 1.0), (1.0, 86, 0.7), (2.0, 82, 1.0), (3.0, 84, 0.8)],
        7: [(0.0, 84, 1.4), (1.5, 82, 0.9), (2.5, 79, 1.4)],
    }
    for bar, notes in melody.items():
        for beat_pos, note, dur_b in notes:
            music.add(
                ctx.at(bar, beat_pos) + ctx.jitter(0.005),
                voice_rhodes(midi_hz(note), dur_b * beat * 1.2, 0.5, seed=bar * 3 + note),
                gain=0.15,
                pan=0.2,
            )

    fx.data += vinyl_bed(ctx.n, rng, level=0.011)
    gain = sidechain_gain(ctx.n, kicks, depth=0.3, release=0.24)
    music.data *= gain[:, None]
    return music, drums, fx


def _build_cinematic(ctx: Ctx) -> tuple[Bus, Bus, Bus]:
    """Cinematic pad: Cm(add9) – Abmaj7 – Fm7 – G, струнные, бумы, колокола."""
    beat = ctx.beat_len
    music, drums, fx = Bus(ctx.n), Bus(ctx.n), Bus(ctx.n)
    rng = ctx.rng

    sections = [
        (0, [48, 51, 55, 60, 62], 2.0, 0.55),  # Cm add9, 2 такта
        (2, [44, 48, 51, 55], 2.0, 0.6),        # Abmaj7
        (4, [41, 44, 48, 51], 2.0, 0.85),       # Fm7
        (6, [43, 47, 50, 55], 2.0, 0.95),       # G (доминанта)
    ]
    for bar_start, tones, bars_len, intensity in sections:
        moment = ctx.at(bar_start)
        dur = bars_len * ctx.bar_len + 1.7
        for index, note in enumerate(tones):
            pan = -0.5 + 1.0 * index / max(1, len(tones) - 1)
            music.add(
                moment + 0.02 * index,
                voice_string(midi_hz(note), dur, intensity, seed=bar_start * 5 + index),
                gain=0.36,
                pan=pan * 0.8,
            )

    # низкий дрон C + квинта G: склеивает гармонию и заполняет низ
    for bar_start in (0, 2, 4, 6):
        moment = ctx.at(bar_start)
        music.add(moment, voice_drone(midi_hz(36), ctx.bar_len * 2 + 1.8, 0.8, tone=0.25), gain=0.14)
        music.add(moment, voice_drone(midi_hz(43), ctx.bar_len * 2 + 1.0, 0.32, tone=0.2), gain=0.05)

    bells = [
        (1.0, 72, 0.5), (2.0, 75, 0.42), (3.5, 79, 0.38),
        (5.0, 75, 0.5), (6.0, 79, 0.45), (6.5, 82, 0.4), (7.0, 84, 0.5),
    ]
    for beat_pos, note, vel in bells:
        music.add(
            ctx.at(0, beat_pos) + ctx.jitter(0.01),
            voice_bell(midi_hz(note), 3.2, vel, seed=note),
            gain=0.17,
            pan=float(rng.uniform(-0.35, 0.35)),
        )

    for bar, vel in ((0, 0.85), (2, 0.6), (4, 0.95), (6, 0.75)):
        drums.add(ctx.at(bar), drum_boom(rng, vel), gain=0.3)
    for beat_pos, vel in ((6.5, 0.7), (7.0, 0.8)):
        drums.add(ctx.at(0, beat_pos), drum_taiko(rng, vel), gain=0.24, pan=-0.15)
    drums.add(ctx.at(6), drum_boom(rng, 0.8, length=2.2), gain=0.3)

    for start_beat, length in ((3.0, 1.7), (6.0, 3.6)):
        fx.add(ctx.at(0, start_beat), noise_riser(rng, length * beat), gain=0.16, pan=0.1)
    return music, drums, fx


def _build_podcast(ctx: Ctx) -> tuple[Bus, Bus, Bus]:
    """Podcast intro: Cmaj7 – Am7 – Fmaj7 – G6, нейтрально, без забитой середины."""
    beat = ctx.beat_len
    music, drums, fx = Bus(ctx.n), Bus(ctx.n), Bus(ctx.n)
    rng = ctx.rng

    sections = [
        (0, [48, 55, 59, 64], [60, 64, 67, 71, 72], 36),   # Cmaj7
        (2, [45, 52, 55, 60], [57, 60, 64, 67, 72], 33),   # Am7
        (4, [41, 48, 52, 57], [53, 57, 60, 64, 69], 41),   # Fmaj7
        (6, [43, 50, 55, 59], [55, 59, 62, 64, 67], 31),   # G6
    ]
    pattern = [
        (0.0, 0, 0.8), (0.75, 2, 0.45), (1.5, 1, 0.6), (2.5, 3, 0.7),
        (3.5, 2, 0.42), (4.0, 4, 0.6), (5.0, 3, 0.5), (6.0, 2, 0.45), (7.0, 1, 0.4),
    ]
    for bar_start, pad_tones, arp_tones, root in sections:
        moment = ctx.at(bar_start)
        dur = 2.0 * ctx.bar_len + 1.2
        for index, note in enumerate(pad_tones):
            music.add(
                moment + 0.01 * index,
                voice_pad(midi_hz(note), dur, 0.55, tone=0.35, seed=bar_start + index),
                gain=0.11,
                pan=-0.35 + 0.7 * index / max(1, len(pad_tones) - 1),
            )
        for beat_pos, tone_index, vel in pattern:
            music.add(
                moment + beat_pos * beat + ctx.jitter(0.004),
                voice_pluck(midi_hz(arp_tones[tone_index]), 1.6 * beat, vel, tone=0.3, seed=bar_start * 11 + tone_index),
                gain=0.24,
                pan=float(rng.uniform(-0.3, 0.3)),
            )
        music.add(moment, voice_bass(midi_hz(root), 2.0 * ctx.bar_len, 0.55, tone=0.3), gain=0.12)
        drums.add(moment, drum_kick(rng, 0.42, tone=0.7, decay=0.09, length=0.28), gain=0.2)
        drums.add(ctx.at(bar_start + 1), drum_kick(rng, 0.38, tone=0.7, decay=0.09, length=0.28), gain=0.18)
        for beat_pos, vel in ((1.0, 0.5), (3.0, 0.55)):
            drums.add(moment + beat_pos * beat, drum_rim(rng, vel), gain=0.16, pan=0.25)
        for step in range(8):
            drums.add(
                moment + step * 0.5 * beat + ctx.jitter(0.004),
                drum_shaker(rng, 0.4 if step % 2 else 0.28),
                gain=0.11,
                pan=float(rng.uniform(-0.2, 0.2)),
            )
        music.add(moment, voice_bell(midi_hz(84 if bar_start == 0 else 88), 2.4, 0.32), gain=0.09, pan=0.0)

    return music, drums, fx


def _build_dark(ctx: Ctx) -> tuple[Bus, Bus, Bus]:
    """Dark tension: Em – Cmaj7 – Am – B7, остинато, тики, тревожный кластер."""
    beat = ctx.beat_len
    music, drums, fx = Bus(ctx.n), Bus(ctx.n), Bus(ctx.n)
    rng = ctx.rng

    chords = [
        [40, 47, 52, 55],
        [48, 52, 55, 59],
        [45, 48, 52, 57],
        [47, 51, 54, 57],
        [40, 47, 52, 55, 65],
        [48, 52, 55, 59],
        [45, 48, 52, 57],
        [47, 51, 54, 57],
    ]
    ostinato = [40, 40, 47, 40, 40, 47, 40, 43]
    kicks: list[float] = []

    for bar in range(8):
        base = ctx.at(bar)
        crescendo = 1.0 + 0.22 * (bar // 2)
        for index, note in enumerate(chords[bar]):
            pan = -0.45 + 0.9 * index / max(1, len(chords[bar]) - 1)
            music.add(
                base + 0.01 * index,
                voice_pad(midi_hz(note), 1.9 * ctx.bar_len + 0.7, 0.7 * crescendo, tone=0.5, seed=bar * 13 + index),
                gain=0.14,
                pan=pan * 0.7,
            )
        for step, note in enumerate(ostinato):
            velocity = (0.9, 0.5, 0.66, 0.55, 0.95, 0.5, 0.62, 0.58)[step]
            music.add(
                base + step * 0.5 * beat,
                voice_string(midi_hz(note), 0.55 * beat, velocity * crescendo * 0.6, seed=bar * 7 + step),
                gain=0.16,
                pan=-0.25,
            )
        for beat_pos, vel in ((0.0, 0.8), (2.0, 0.62)):
            moment = base + beat_pos * beat + ctx.jitter(0.004)
            drums.add(moment, drum_kick(rng, vel, tone=0.5, decay=0.2, length=0.55), gain=0.42)
            kicks.append(moment)
        for beat_pos in (1.5, 3.5):
            drums.add(
                base + beat_pos * beat + ctx.jitter(0.005),
                drum_tick(rng, 0.55 if beat_pos == 1.5 else 0.4),
                gain=0.1,
                pan=float(rng.uniform(0.3, 0.5)),
            )
        if bar in (2, 6):
            drums.add(base + 3.5 * beat, drum_rim(rng, 0.45), gain=0.12, pan=-0.3)

    # высокий диссонансный кластер только на Em-тактах — «что-то не так»
    for bar_start in (0, 4):
        for beat_pos, note, vel in ((0.0, 76, 0.32), (0.0, 77, 0.28), (2.0, 83, 0.22)):
            length = 2.0 * ctx.bar_len
            n = n_samples(length)
            trem = 1.0 - 0.18 + 0.18 * np.sin(2.0 * math.pi * (ctx.n / SR) / 0.7 * np.arange(n) / SR)
            music.add(
                ctx.at(bar_start, beat_pos),
                voice_string(midi_hz(note), length, vel, seed=note) * trem,
                gain=0.11,
                pan=float(rng.uniform(-0.4, 0.4)),
            )
    for bar in (0, 4):
        drums.add(ctx.at(bar), drum_boom(rng, 0.9 if bar == 4 else 0.75, length=2.0), gain=0.33)
    for start_bar in (1, 3, 5):
        fx.add(ctx.at(start_bar, 2.0), noise_riser(rng, 1.8 * beat, lo=250.0, hi=5200.0), gain=0.13, pan=-0.2)
    gain = sidechain_gain(ctx.n, kicks, depth=0.22, release=0.3)
    music.data *= gain[:, None]
    return music, drums, fx


def _build_upbeat(ctx: Ctx) -> tuple[Bus, Bus, Bus]:
    """Upbeat pop: C – G – Am – F, ровная бочка, хлопки, цепляющий лид."""
    beat = ctx.beat_len
    music, drums, fx = Bus(ctx.n), Bus(ctx.n), Bus(ctx.n)
    rng = ctx.rng

    chords = [
        [60, 64, 67, 72],
        [55, 59, 62, 67],
        [57, 60, 64, 69],
        [57, 60, 65, 69],
    ]
    roots = [36, 43, 45, 41]
    kicks: list[float] = []

    for bar in range(ctx.bars):
        base = ctx.at(bar)
        chord = chords[bar % 4]
        root = roots[bar % 4]
        for beat_pos, dur_b, vel in ((0.0, 0.9, 0.9), (1.5, 0.4, 0.55), (2.5, 0.8, 0.75), (3.5, 0.35, 0.45)):
            moment = base + beat_pos * beat
            for index, note in enumerate(chord):
                music.add(
                    moment,
                    voice_pluck(midi_hz(note), dur_b * beat, vel, tone=0.7, seed=bar * 3 + index),
                    gain=0.3,
                    pan=-0.3 + 0.6 * index / 3.0,
                )
        for index, note in enumerate(chord[:3]):
            music.add(
                base,
                voice_piano(midi_hz(note), 1.5 * beat, 0.55, hard=0.6, seed=bar + index),
                gain=0.16,
                pan=-0.15 + 0.15 * index,
            )
        bass_pattern = ((0.0, 0, 1.0), (0.75, 0, 0.6), (1.5, 7, 0.7),
                        (2.0, 0, 0.85), (2.5, 12, 0.6), (3.25, 7, 0.55))
        for beat_pos, offset, vel in bass_pattern:
            music.add(
                base + beat_pos * beat,
                voice_bass(midi_hz(root + offset), 0.45 * beat, vel, tone=0.6, seed=bar),
                gain=0.12,
            )
        for beat_pos, vel in ((0.0, 1.0), (1.0, 0.78), (2.0, 0.95), (3.0, 0.78)):
            moment = base + beat_pos * beat + ctx.jitter(0.002)
            drums.add(moment, drum_kick(rng, vel, decay=0.12, length=0.4), gain=0.42)
            kicks.append(moment)
        for beat_pos, vel in ((1.0, 0.85), (3.0, 0.9)):
            drums.add(base + beat_pos * beat, drum_clap(rng, vel), gain=0.3, pan=0.05)
        for step in range(8):
            position = step * 0.5
            drums.add(
                base + position * beat + ctx.jitter(0.002),
                drum_hat(rng, 0.55 if step % 2 == 0 else 0.34),
                gain=0.12,
                pan=0.28,
            )
        for beat_pos in (0.5, 1.5, 2.5, 3.5):
            drums.add(base + beat_pos * beat, drum_hat(rng, 0.4, open_hat=True), gain=0.09, pan=-0.25)
        if bar in (3, 7, 11):
            for k, beat_pos in enumerate((2.5, 2.75, 3.0, 3.25, 3.5, 3.75)):
                drums.add(base + beat_pos * beat, drum_snare(rng, 0.35 + 0.1 * k), gain=0.13, pan=float(rng.uniform(-0.2, 0.2)))

    melody = {
        0: [(0.0, 76, 0.5), (0.75, 79, 0.5), (1.5, 81, 0.5), (2.25, 79, 0.5), (3.0, 76, 1.0)],
        1: [(0.0, 74, 0.5), (0.75, 71, 0.5), (1.5, 74, 0.5), (2.25, 79, 1.0), (3.25, 74, 0.5)],
        2: [(0.0, 72, 0.5), (0.75, 76, 0.5), (1.5, 81, 0.5), (2.25, 79, 0.5), (3.0, 76, 1.0)],
        3: [(0.0, 77, 0.75), (0.75, 76, 0.5), (1.5, 74, 0.5), (2.25, 72, 1.0), (3.25, 69, 0.5)],
        8: [(0.0, 79, 0.5), (0.75, 81, 0.5), (1.5, 79, 0.5), (2.25, 76, 1.0)],
        9: [(0.0, 74, 0.75), (0.75, 71, 0.5), (1.5, 74, 0.5), (2.25, 79, 1.0)],
        10: [(0.0, 72, 0.5), (0.75, 76, 0.5), (1.5, 81, 0.5), (2.25, 79, 0.5), (3.0, 76, 1.0)],
        11: [(0.0, 77, 0.75), (0.75, 76, 0.5), (1.5, 74, 0.5), (2.25, 72, 1.5)],
    }
    for bar in range(ctx.bars):
        source_bar = bar if bar in melody else bar - 4
        if source_bar not in melody:
            continue
        for beat_pos, note, dur_b in melody[source_bar]:
            lead = voice_lead(midi_hz(note), dur_b * beat, 0.62, seed=bar * 5 + note)
            music.add(ctx.at(bar, beat_pos), lead, gain=0.22, pan=0.12)
            if bar >= 4:
                music.add(
                    ctx.at(bar, beat_pos),
                    voice_glock(midi_hz(note + 12), dur_b * beat, 0.35, seed=note),
                    gain=0.08,
                    pan=-0.3,
                )

    for bar in (0, 4, 8):
        drums.add(ctx.at(bar), drum_crash(rng, 0.7), gain=0.16, pan=0.2)
        drums.add(ctx.at(bar), drum_boom(rng, 0.55, length=1.0), gain=0.18)
    fx.add(ctx.at(7, 2.0), noise_riser(rng, 2.0 * beat, lo=500.0, hi=9000.0), gain=0.12)
    gain = sidechain_gain(ctx.n, kicks, depth=0.34, release=0.18)
    music.data *= gain[:, None]
    return music, drums, fx


def _build_minimal(ctx: Ctx) -> tuple[Bus, Bus, Bus]:
    """Minimal tech: Am9 / Fmaj7, катящий бас, ровная бочка, дилей на стабах."""
    beat = ctx.beat_len
    music, drums, fx = Bus(ctx.n), Bus(ctx.n), Bus(ctx.n)
    rng = ctx.rng

    sections = [
        (0, [45, 52, 55, 59, 60], 33, [69, 72, 76, 74, 72, 69]),
        (2, [41, 48, 52, 57], 29, [69, 72, 77, 72, 69, 67]),
        (4, [45, 52, 55, 59, 60], 33, [72, 76, 79, 76, 72, 69]),
        (6, [41, 48, 52, 57], 29, [69, 72, 77, 76, 72, 69]),
        (8, [48, 52, 55, 59], 36, [72, 76, 79, 76, 72, 76]),
        (10, [43, 50, 55, 59], 31, [74, 79, 76, 72, 69, 67]),
    ]
    kicks: list[float] = []
    for bar_start, chord, bass_root, motif in sections:
        for local in range(2):
            base = ctx.at(bar_start + local)
            for index, note in enumerate(chord):
                music.add(
                    base + 0.5 * beat if index % 2 == 0 else base + 2.75 * beat,
                    voice_pad(midi_hz(note), 0.9 * beat, 0.5, tone=0.45, seed=bar_start * 9 + index),
                    gain=0.12,
                    pan=-0.3 + 0.6 * index / max(1, len(chord) - 1),
                )
            for step in range(16):
                if step in (7, 15) and local == 1:
                    continue
                note = bass_root + (12 if step in (6, 14) else 0)
                velocity = 1.0 if step % 4 == 0 else (0.7 if step % 2 == 0 else 0.48)
                music.add(
                    base + step * 0.25 * beat,
                    voice_bass(midi_hz(note), 0.22 * beat, velocity, tone=0.75, seed=bar_start + step),
                    gain=0.09,
                )
            for index, beat_pos in enumerate((0.0, 1.5, 2.5)):
                fx.add(
                    base + beat_pos * beat,
                    voice_pluck(midi_hz(motif[(local * 3 + index) % len(motif)]), 1.2 * beat,
                                0.7 if index == 0 else 0.45, tone=0.75, seed=bar_start * 7 + index),
                    gain=0.16,
                    pan=float(rng.uniform(-0.25, 0.25)),
                )
            for step in range(4):
                moment = base + step * beat + ctx.jitter(0.002)
                drums.add(moment, drum_kick(rng, 1.0 if step % 2 == 0 else 0.82, tone=1.0, decay=0.1, length=0.34), gain=0.3)
                kicks.append(moment)
            for beat_pos in (1.0, 3.0):
                drums.add(base + beat_pos * beat, drum_clap(rng, 0.8), gain=0.26, pan=-0.05)
            for step in range(16):
                if step % 2 == 0:
                    continue
                drums.add(base + step * 0.25 * beat, drum_hat(rng, 0.5), gain=0.1, pan=0.3)
            for beat_pos in (0.5, 2.5):
                drums.add(base + beat_pos * beat, drum_hat(rng, 0.45, open_hat=True), gain=0.1, pan=-0.3)

    top = [72, 76, 79, 76, 72, 69, 67, 69]
    for bar in range(ctx.bars):
        if bar % 4 != 2:
            continue
        music.add(
            ctx.at(bar, 2.0),
            voice_pad(midi_hz(top[bar % len(top)]), 2.0 * beat, 0.4, tone=0.4, seed=bar),
            gain=0.08,
            pan=0.35,
        )

    fx.data = fft_delay(fx.data, ctx.beat_len * 0.75, feedback=0.34, mix=0.28, damp_hz=3200.0)
    gain = sidechain_gain(ctx.n, kicks, depth=0.48, release=0.16)
    music.data *= gain[:, None]
    return music, drums, fx


def _build_piano(ctx: Ctx) -> tuple[Bus, Bus, Bus]:
    """Emotional piano: Am – F – C – G – Am – F – Dm – E, мелодия и арпеджио."""
    beat = ctx.beat_len
    music, drums, fx = Bus(ctx.n), Bus(ctx.n), Bus(ctx.n)
    rng = ctx.rng

    bass_lines = [
        [45, 52, 57, 60, 57, 52, 57, 52],   # Am
        [41, 48, 53, 57, 53, 48, 53, 48],   # F
        [36, 43, 48, 52, 48, 43, 48, 43],   # C
        [43, 50, 55, 59, 55, 50, 55, 50],   # G
        [45, 52, 57, 60, 57, 52, 57, 52],   # Am
        [41, 48, 53, 57, 53, 48, 53, 48],   # F
        [38, 45, 50, 53, 50, 45, 50, 45],   # Dm
        [40, 47, 52, 56, 52, 47, 52, 47],   # E
    ]
    pads = [
        [69, 72, 76], [65, 69, 72], [64, 67, 72], [67, 71, 74],
        [69, 72, 76], [65, 69, 72], [62, 65, 69], [64, 68, 71],
    ]
    melody = [
        [(0.0, 76, 2.0), (2.0, 72, 1.0), (3.0, 69, 1.0)],
        [(0.0, 77, 2.0), (2.0, 76, 1.0), (3.0, 72, 1.0)],
        [(0.0, 79, 2.0), (2.0, 76, 1.0), (3.0, 72, 1.0)],
        [(0.0, 74, 2.0), (2.0, 71, 1.0), (3.0, 67, 1.0)],
        [(0.0, 76, 2.0), (2.0, 72, 1.0), (3.0, 69, 1.0)],
        [(0.0, 77, 2.0), (2.0, 76, 1.0), (3.0, 72, 1.0)],
        [(0.0, 74, 2.0), (2.0, 77, 1.0), (3.0, 81, 1.0)],
        [(0.0, 79, 2.0), (2.0, 76, 2.0)],
    ]
    bars = min(ctx.bars, 8)
    for bar in range(bars):
        base = ctx.at(bar)
        for step, note in enumerate(bass_lines[bar]):
            velocity = (0.62, 0.42, 0.5, 0.56, 0.44, 0.4, 0.46, 0.4)[step]
            music.add(
                base + step * 0.5 * beat + ctx.jitter(0.012),
                voice_piano(midi_hz(note), 1.6 * beat, velocity * float(rng.uniform(0.92, 1.05)), hard=0.55, seed=bar * 5 + step),
                gain=0.16,
                pan=-0.22 + 0.1 * (step % 3),
            )
        for index, note in enumerate(pads[bar]):
            music.add(
                base,
                voice_pad(midi_hz(note), ctx.bar_len + 0.9, 0.38, tone=0.35, seed=bar * 3 + index),
                gain=0.07,
                pan=-0.3 + 0.3 * index,
            )
        music.add(base, voice_bass(midi_hz(bass_lines[bar][0] - 12), ctx.bar_len, 0.5, tone=0.25), gain=0.09)
        for beat_pos, note, dur_b in melody[bar]:
            music.add(
                base + beat_pos * beat + ctx.jitter(0.015),
                voice_piano(midi_hz(note), dur_b * beat * 1.15, 0.72 * float(rng.uniform(0.9, 1.06)), hard=0.75, seed=bar * 11 + note),
                gain=0.2,
                pan=0.12,
            )

    return music, drums, fx


def _build_hype(ctx: Ctx) -> tuple[Bus, Bus, Bus]:
    """Hype trap: Gm – Eb – Bb – F, 808, триольные хэты, тёмный колокол."""
    beat = ctx.beat_len
    music, drums, fx = Bus(ctx.n), Bus(ctx.n), Bus(ctx.n)
    rng = ctx.rng

    chords = [
        [43, 50, 55, 58],
        [39, 46, 51, 55],
        [34, 41, 46, 50],
        [41, 48, 53, 57],
    ]
    bass_patterns = {
        0: [(31, 0.0, 2.5), (31, 2.5, 1.0)],
        1: [(39, 0.0, 2.0), (39, 2.5, 1.5)],
        2: [(34, 0.0, 2.0), (34, 2.5, 1.0)],
        3: [(29, 0.0, 1.5), (29, 2.0, 2.0)],
    }
    kicks: list[float] = []
    for bar in range(ctx.bars):
        base = ctx.at(bar)
        chord = chords[bar % 4]
        for index, note in enumerate(chord):
            music.add(
                base,
                voice_pad(midi_hz(note), 2.0 * beat, 0.75, tone=0.5, seed=bar * 5 + index),
                gain=0.18,
                pan=-0.4 + 0.8 * index / 3.0,
            )
        for note, beat_pos, dur_b in bass_patterns[bar % 4]:
            music.add(
                base + beat_pos * beat,
                voice_808(midi_hz(note), dur_b * beat, 0.95, glide=0.07, seed=bar),
                gain=0.12,
            )
        for beat_pos, vel in ((0.0, 1.0), (2.75, 0.75)):
            moment = base + beat_pos * beat + ctx.jitter(0.002)
            drums.add(moment, drum_kick(rng, vel, tone=1.1, decay=0.18, length=0.45), gain=0.3)
            kicks.append(moment)
        for beat_pos, vel in ((2.0, 0.95),):
            drums.add(base + beat_pos * beat, drum_clap(rng, vel), gain=0.32)
        hat_limit = 16
        if bar % 4 == 3:
            hat_limit = 12
        for step in range(hat_limit):
            velocity = 0.8 if step % 4 == 0 else (0.45 if step % 2 == 0 else 0.32)
            drums.add(
                base + step * 0.25 * beat + ctx.jitter(0.0015),
                drum_hat(rng, velocity),
                gain=0.13,
                pan=float(rng.uniform(0.2, 0.4)),
            )
        if bar % 4 == 3:
            for triple in range(6):
                drums.add(
                    base + (3.0 + triple * (1.0 / 6.0)) * beat,
                    drum_hat(rng, 0.34),
                    gain=0.1,
                    pan=0.3,
                )
        if bar % 4 == 0:
            drums.add(base, drum_crash(rng, 0.55), gain=0.13, pan=-0.2)

    hook = {
        0: [(0.0, 67, 0.75), (1.0, 74, 0.5), (1.75, 70, 0.5), (2.5, 67, 1.0)],
        1: [(0.0, 75, 0.75), (1.0, 74, 0.5), (1.75, 70, 0.5), (2.5, 74, 1.0)],
        2: [(0.0, 70, 0.75), (1.0, 74, 0.5), (1.75, 79, 0.5), (2.5, 70, 1.0)],
        3: [(0.0, 77, 0.5), (0.75, 74, 0.5), (1.5, 70, 0.5), (2.5, 67, 1.0)],
    }
    for bar in range(ctx.bars):
        for beat_pos, note, dur_b in hook[bar % 4]:
            octave = 0 if bar < 8 else 12
            music.add(
                ctx.at(bar, beat_pos),
                voice_bell(midi_hz(note + octave), dur_b * beat * 1.3, 0.55, ratio=3.51, seed=note + bar),
                gain=0.28,
                pan=float(rng.uniform(-0.3, 0.3)),
            )

    for bar in (0, 4, 8):
        drums.add(ctx.at(bar), drum_boom(rng, 0.8, length=1.8), gain=0.24)
        if bar in (0, 8):
            drums.add(ctx.at(bar), drum_crash(rng, 0.5), gain=0.1, pan=0.25)
    fx.add(ctx.at(7, 2.0), noise_riser(rng, 2.0 * beat, lo=400.0, hi=8000.0), gain=0.14)

    gain = sidechain_gain(ctx.n, kicks, depth=0.38, release=0.2)
    music.data *= gain[:, None]
    return music, drums, fx


def _build_drama_guzheng(ctx: Ctx) -> tuple[Bus, Bus, Bus]:
    """Каноничная драма (грустное пианино + струнные): как в клипах китайских историй (Am-F-C-G)."""
    beat = ctx.beat_len
    music, drums, fx = Bus(ctx.n), Bus(ctx.n), Bus(ctx.n)
    rng = ctx.rng

    arp_patterns = [
        [57, 64, 69, 72, 69, 64, 72, 69],  # Am
        [53, 60, 65, 69, 65, 60, 69, 65],  # F
        [48, 55, 60, 64, 60, 55, 64, 60],  # C
        [55, 62, 67, 71, 67, 62, 71, 67],  # G
    ]
    top_notes = [
        [(0.0, 76, 1.5), (1.5, 72, 1.0), (2.5, 74, 1.5)],
        [(0.0, 77, 1.5), (1.5, 76, 1.0), (2.5, 72, 1.5)],
        [(0.0, 79, 2.0), (2.0, 76, 1.0), (3.0, 72, 1.0)],
        [(0.0, 74, 1.5), (1.5, 71, 1.5), (3.0, 69, 1.0)],
    ]
    for bar in range(ctx.bars):
        base = ctx.at(bar)
        arp = arp_patterns[bar % 4]
        for idx, note in enumerate((arp[0], arp[2], arp[3])):
            music.add(
                base,
                voice_string(midi_hz(note), ctx.bar_len + 0.8, 0.55, seed=bar * 7 + idx),
                gain=0.13,
                pan=-0.35 + 0.35 * idx,
            )
        music.add(base, voice_bass(midi_hz(arp[0] - 12), ctx.bar_len, 0.5, tone=0.28), gain=0.11)
        for step, note in enumerate(arp):
            music.add(
                base + step * 0.5 * beat + ctx.jitter(0.01),
                voice_piano(midi_hz(note), 1.4 * beat, 0.65 if step % 2 == 0 else 0.48, hard=0.6, seed=bar * 11 + step),
                gain=0.22,
                pan=-0.2 + 0.12 * (step % 4),
            )
        for beat_pos, note, dur_b in top_notes[bar % 4]:
            music.add(
                base + beat_pos * beat + ctx.jitter(0.01),
                voice_piano(midi_hz(note), dur_b * beat * 1.25, 0.78, hard=0.72, seed=bar * 19 + note),
                gain=0.24,
                pan=0.1,
            )
        if bar % 4 == 0:
            drums.add(base, drum_boom(rng, 0.65, length=1.4), gain=0.18)

    return music, drums, fx


def _build_brainrot_twist(ctx: Ctx) -> tuple[Bus, Bus, Bus]:
    """Каноничный интрига-бит (как в @aiistori): тёмный грув, чёткий хэт/рим, минорный колокол."""
    beat = ctx.beat_len
    music, drums, fx = Bus(ctx.n), Bus(ctx.n), Bus(ctx.n)
    rng = ctx.rng

    chords = [
        [45, 52, 57, 60],  # Am
        [41, 48, 53, 57],  # F
        [43, 50, 55, 59],  # G
        [40, 47, 52, 55],  # Em
    ]
    bells = [
        [(0.0, 69, 0.75), (1.0, 72, 0.75), (2.0, 76, 1.0), (3.0, 74, 0.75)],
        [(0.0, 69, 0.75), (1.0, 72, 0.75), (2.0, 77, 1.0), (3.0, 76, 0.75)],
        [(0.0, 67, 0.75), (1.0, 71, 0.75), (2.0, 74, 1.0), (3.0, 72, 0.75)],
        [(0.0, 64, 0.75), (1.0, 67, 0.75), (2.0, 71, 1.0), (3.0, 69, 0.75)],
    ]
    kicks: list[float] = []
    for bar in range(ctx.bars):
        base = ctx.at(bar)
        chord = chords[bar % 4]
        for idx, note in enumerate(chord):
            music.add(
                base,
                voice_pad(midi_hz(note), ctx.bar_len, 0.58, tone=0.45, seed=bar * 5 + idx),
                gain=0.14,
                pan=-0.3 + 0.2 * idx,
            )
        music.add(base, voice_808(midi_hz(chord[0] - 12), 2.0 * beat, 0.85, glide=0.05, seed=bar), gain=0.13)
        music.add(base + 2.5 * beat, voice_808(midi_hz(chord[0] - 12), 1.5 * beat, 0.75, glide=0.04, seed=bar + 1), gain=0.12)
        for beat_pos, vel in ((0.0, 0.95), (1.5, 0.7), (2.5, 0.88)):
            moment = base + beat_pos * beat
            drums.add(moment, drum_kick(rng, vel, tone=0.9, decay=0.15, length=0.4), gain=0.28)
            kicks.append(moment)
        for beat_pos in (1.0, 3.0):
            drums.add(base + beat_pos * beat, drum_rim(rng, 0.72), gain=0.18, pan=-0.1)
            drums.add(base + beat_pos * beat, drum_snare(rng, 0.65), gain=0.16, pan=0.05)
        for step in range(8):
            drums.add(base + step * 0.5 * beat, drum_hat(rng, 0.55 if step % 2 == 0 else 0.35), gain=0.12, pan=0.28)
        for beat_pos, note, dur_b in bells[bar % 4]:
            music.add(
                base + beat_pos * beat,
                voice_rhodes(midi_hz(note), dur_b * beat * 1.2, 0.7, seed=bar * 13 + note),
                gain=0.22,
                pan=-0.15,
            )
        if bar % 4 == 3:
            fx.add(base + 2.5 * beat, noise_riser(rng, 1.5 * beat, lo=400.0, hi=6800.0), gain=0.11)

    gain = sidechain_gain(ctx.n, kicks, depth=0.32, release=0.16)
    music.data *= gain[:, None]
    return music, drums, fx


def _build_imperial_destiny(ctx: Ctx) -> tuple[Bus, Bus, Bus]:
    """Каноничный мистический саспенс-фон (шкатулка/синт + глубокий саб-пульс для конфликтов)."""
    beat = ctx.beat_len
    music, drums, fx = Bus(ctx.n), Bus(ctx.n), Bus(ctx.n)
    rng = ctx.rng

    roots = [40, 40, 41, 40]  # E - E - F - E (фригийский саспенс)
    motif = [76, 79, 77, 76, 72, 76, 74, 72]
    for bar in range(ctx.bars):
        base = ctx.at(bar)
        root = roots[bar % 4]
        music.add(base, voice_drone(midi_hz(root - 12), ctx.bar_len, 0.78, tone=0.38), gain=0.17)
        music.add(base, voice_pad(midi_hz(root + 12), ctx.bar_len, 0.52, tone=0.32, seed=bar), gain=0.12, pan=-0.2)
        for step in range(8):
            note = motif[(bar * 2 + step) % len(motif)]
            music.add(
                base + step * 0.5 * beat + ctx.jitter(0.005),
                voice_glock(midi_hz(note), 0.85 * beat, 0.58 if step % 2 == 0 else 0.4, seed=bar * 7 + step),
                gain=0.16,
                pan=-0.25 + 0.5 * (step % 2),
            )
        for beat_pos in (0.0, 2.0):
            drums.add(base + beat_pos * beat, drum_taiko(rng, 0.78, length=0.95), gain=0.22)
        if bar % 2 == 0:
            drums.add(base, drum_boom(rng, 0.85, length=1.6), gain=0.24)
        for step in range(8):
            drums.add(base + step * 0.5 * beat, drum_tick(rng, 0.35 if step % 2 == 0 else 0.2), gain=0.1, pan=0.2)
        if bar % 4 == 3:
            fx.add(base + 2.0 * beat, noise_riser(rng, 2.0 * beat, lo=280.0, hi=5400.0), gain=0.12)

    music.data = fft_delay(music.data, ctx.beat_len * 0.75, feedback=0.3, mix=0.22, damp_hz=3000.0)
    return music, drums, fx


TRACKS: list[TrackSpec] = [
    TrackSpec(
        id="lofi-chill", title="Ночной лоу-фай", mood="спокойное", bpm=70, bars=8, seed=101,
        build=_build_lofi, width=1.12,
        eq=(("high_shelf", 7000.0, -2.5, 0.7), ("peak", 2600.0, -1.5, 1.0),
            ("low_shelf", 110.0, 1.0, 0.7), ("peak", 400.0, 1.0, 1.2)),
        reverb_seconds=1.5, reverb_tau=0.5, reverb_wet=0.3,
        music_rms=-20.5, drums_rms=-21.0, fx_rms=-46.0,
    ),
    TrackSpec(
        id="cinematic-pad", title="Кинематографичная подложка", mood="кинематографичное",
        bpm=72, bars=8, seed=202, build=_build_cinematic, width=1.22,
        eq=(("high_shelf", 9000.0, 1.5, 0.7), ("low_shelf", 70.0, -0.5, 0.7),
            ("peak", 400.0, -1.0, 0.9)),
        reverb_seconds=3.4, reverb_tau=1.15, reverb_wet=0.42, reverb_lo=150.0,
        drum_reverb_send=0.55, music_rms=-19.5, drums_rms=-24.5, fx_rms=-37.0,
    ),
    TrackSpec(
        id="podcast-intro", title="Подкаст-интро", mood="нейтральное", bpm=92, bars=8, seed=303,
        build=_build_podcast, width=1.05, lufs=-17.5,
        eq=(("peak", 1800.0, -1.2, 1.2), ("high_shelf", 8000.0, -1.0, 0.7),
            ("low_shelf", 80.0, -2.0, 0.7), ("peak", 250.0, 1.0, 1.0),
            ("peak", 3200.0, 0.8, 1.0), ("peak", 320.0, -1.2, 1.0)),
        reverb_seconds=1.2, reverb_tau=0.4, reverb_wet=0.2,
        music_rms=-24.0, drums_rms=-32.0,
    ),
    TrackSpec(
        id="dark-tension", title="Тёмное напряжение", mood="тревожное", bpm=80, bars=8, seed=404,
        build=_build_dark, width=1.15,
        eq=(("low_shelf", 90.0, 1.2, 0.7), ("high_shelf", 9000.0, -2.0, 0.7),
            ("peak", 420.0, 1.0, 1.1), ("peak", 2400.0, -1.5, 1.0)),
        reverb_seconds=2.6, reverb_tau=0.9, reverb_wet=0.35, reverb_hi=5200.0,
        drum_reverb_send=0.5, music_rms=-20.5, drums_rms=-22.5, fx_rms=-38.0,
    ),
    TrackSpec(
        id="upbeat-pop", title="Бодрый поп", mood="бодрое", bpm=120, bars=12, seed=505,
        build=_build_upbeat, width=1.16, drive=1.15,
        eq=(("high_shelf", 8500.0, 2.0, 0.7), ("low_shelf", 90.0, 0.5, 0.7),
            ("peak", 500.0, -1.5, 1.0), ("peak", 3000.0, 1.0, 1.0)),
        reverb_seconds=1.1, reverb_tau=0.4, reverb_wet=0.24,
        music_rms=-19.5, drums_rms=-19.0, fx_rms=-42.0,
    ),
    TrackSpec(
        id="minimal-tech", title="Минимал-тех", mood="минимализм", bpm=124, bars=12, seed=606,
        build=_build_minimal, width=1.18, drive=1.1,
        eq=(("high_shelf", 9000.0, 1.0, 0.7), ("low_shelf", 80.0, 0.5, 0.7),
            ("peak", 320.0, -2.0, 1.0), ("peak", 4000.0, 1.0, 1.0)),
        reverb_seconds=1.6, reverb_tau=0.55, reverb_wet=0.26, drum_reverb_send=0.25,
        music_rms=-20.0, drums_rms=-18.5, fx_rms=-36.0,
    ),
    TrackSpec(
        id="emotional-piano", title="Эмоциональное пианино", mood="трогательное", bpm=66, bars=8, seed=707,
        build=_build_piano, width=1.2,
        eq=(("low_shelf", 100.0, 1.0, 0.7), ("high_shelf", 8000.0, -2.0, 0.7),
            ("peak", 300.0, -1.5, 0.9), ("peak", 2500.0, -1.0, 1.0)),
        reverb_seconds=2.8, reverb_tau=0.95, reverb_wet=0.36, reverb_hi=5800.0,
        music_rms=-19.0, fx_rms=-50.0,
    ),
    TrackSpec(
        id="hype-trap", title="Хайп-трэп", mood="мотивационное", bpm=140, bars=12, seed=808,
        build=_build_hype, width=1.14, drive=1.25,
        eq=(("low_shelf", 60.0, 1.0, 0.7), ("peak", 260.0, -2.0, 1.0),
            ("high_shelf", 10000.0, 1.5, 0.7), ("peak", 3200.0, 1.0, 1.0)),
        reverb_seconds=1.9, reverb_tau=0.65, reverb_wet=0.3, drum_reverb_send=0.3,
        music_rms=-19.0, drums_rms=-19.5, fx_rms=-38.0,
    ),
    TrackSpec(
        id="drama-guzheng", title="Дорама: грустное пианино", mood="дорама", bpm=84, bars=8, seed=909,
        build=_build_drama_guzheng, width=1.22,
        eq=(("low_shelf", 95.0, 0.8, 0.7), ("peak", 340.0, -1.5, 1.0),
            ("peak", 2900.0, 1.2, 1.0), ("high_shelf", 8500.0, -1.0, 0.7)),
        reverb_seconds=2.4, reverb_tau=0.85, reverb_wet=0.34, drum_reverb_send=0.35,
        music_rms=-19.5, drums_rms=-22.0, fx_rms=-40.0,
    ),
    TrackSpec(
        id="brainrot-twist", title="Дорама: интрига-бит", mood="интрига", bpm=116, bars=8, seed=1010,
        build=_build_brainrot_twist, width=1.16, drive=1.15,
        eq=(("low_shelf", 75.0, 1.2, 0.7), ("peak", 280.0, -1.8, 1.0),
            ("peak", 3100.0, 1.2, 1.0), ("high_shelf", 9000.0, 0.5, 0.7)),
        reverb_seconds=1.8, reverb_tau=0.65, reverb_wet=0.28, drum_reverb_send=0.3,
        music_rms=-19.5, drums_rms=-20.0, fx_rms=-38.0,
    ),
    TrackSpec(
        id="imperial-destiny", title="Дорама: саспенс-мистика", mood="саспенс", bpm=92, bars=8, seed=1111,
        build=_build_imperial_destiny, width=1.24,
        eq=(("low_shelf", 80.0, 1.0, 0.7), ("peak", 380.0, -1.2, 0.9),
            ("high_shelf", 8500.0, 1.0, 0.7)),
        reverb_seconds=3.1, reverb_tau=1.05, reverb_wet=0.38, drum_reverb_send=0.45,
        music_rms=-19.0, drums_rms=-22.5, fx_rms=-38.0,
    ),
]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def collect_entries(out_dir: Path) -> list[dict]:
    """Собирает манифест по факту: длительность берём из самих mp3."""
    entries = []
    for spec in TRACKS:
        path = out_dir / f"{spec.id}.mp3"
        if not path.is_file():
            continue
        info = mp3_info(path)
        entries.append({
            "id": spec.id, "title": spec.title, "mood": spec.mood,
            "bpm": spec.bpm, "duration": round(info["duration"], 2),
            "path": path, "bytes": info["bytes"], "mp3": info,
        })
    return entries


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Синтез оригинального музыкального пака Cutter")
    parser.add_argument("--only", action="append", default=[], help="собрать только эти id")
    parser.add_argument("--skip-existing", action="store_true", help="не пересобирать готовые файлы")
    args = parser.parse_args(argv)

    ffmpeg = find_ffmpeg()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wanted = [spec for spec in TRACKS if not args.only or spec.id in args.only]

    print(f"ffmpeg: {ffmpeg}")
    print(f"треков к сборке: {len(wanted)} -> {OUT_DIR}")
    results: list[dict] = []
    for spec in wanted:
        target = OUT_DIR / f"{spec.id}.mp3"
        if args.skip_existing and target.is_file():
            print(f"  {spec.id:<16} пропущен (файл уже есть)")
            continue
        entry = render_track(ffmpeg, spec)
        log_track(entry)
        results.append(entry)

    entries = collect_entries(OUT_DIR)
    if len(entries) != len(TRACKS):
        missing = [spec.id for spec in TRACKS if not (OUT_DIR / f"{spec.id}.mp3").is_file()]
        print(f"ВНИМАНИЕ: в папке нет {missing}; манифест собран по существующим файлам")
    manifest = write_manifest(entries, OUT_DIR)
    total = sum(entry["bytes"] for entry in entries)
    print(f"манифест: {manifest} ({len(entries)} треков)")
    print(f"итого: {len(entries)} mp3, {total / 1024 / 1024:.2f} МБ")

    # сводка качества: пик и шов проверяем на декодированном mp3
    bad = []
    for entry in results:
        problems = []
        if entry["decoded_peak"] is None or entry["decoded_peak"] > -1.0:
            problems.append(f"пик {entry['decoded_peak']}")
        if entry["decoded_seam"] > 3.0:
            problems.append(f"шов x{entry['decoded_seam']:.2f}")
        if entry["mp3"]["bitrate"] != 192 or entry["mp3"]["sr"] != SR or entry["mp3"]["channels"] != 2:
            problems.append("формат mp3")
        if problems:
            bad.append(f"{entry['id']} ({', '.join(problems)})")
    if bad:
        print("ВНИМАНИЕ: проверь треки: " + "; ".join(bad))
    else:
        print("качество: пик <= -1 dBFS, шов чистый, mp3 192k/44.1k/стерео")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
