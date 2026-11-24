# -*- coding: utf-8 -*-
"""Рекламные интеграции: библиотека файлов, видео-вставки (preroll/midroll/postroll) и баннер-оверлеи."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import settings as S

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ALLOWED_EXTS = VIDEO_EXTS | IMAGE_EXTS


class IntegrationError(RuntimeError):
    pass


@dataclass
class IntegrationSpec:
    file_path: Path
    mode: str = "insert"        # insert (видео-вставка) | overlay (баннер/картинка поверх)
    position: str = "after_hook"  # insert: start | after_hook | end ; overlay: bottom | top | center
    hook_sec: float = 3.0       # для after_hook: через сколько секунд от начала клипа вставить ролик
    start_sec: float = 0.0      # для overlay: с какой секунды показывать
    duration_sec: float = 0.0   # для overlay: длительность (0 = весь клип)
    scale_pct: int = 75         # для overlay: ширина в % от кадра (20..100)


def _integrations_dir() -> Path:
    path = S.DATA_DIR / "integrations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manifest_path() -> Path:
    return _integrations_dir() / "manifest.json"


def _read_manifest() -> list[dict]:
    path = _manifest_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [dict(item) for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_manifest(items: list[dict]) -> None:
    _integrations_dir().mkdir(parents=True, exist_ok=True)
    _manifest_path().write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def list_integrations() -> list[dict]:
    folder = _integrations_dir()
    items = _read_manifest()
    alive: list[dict] = []
    for item in items:
        file_name = str(item.get("file") or "")
        path = folder / Path(file_name).name
        if file_name and path.is_file():
            entry = dict(item)
            entry["bytes"] = path.stat().st_size
            entry["url"] = f"/api/integrations/{entry['id']}/file"
            alive.append(entry)
    return alive


def get_integration(item_id: str) -> dict | None:
    target = (item_id or "").strip()
    if not target:
        return None
    for item in list_integrations():
        if item.get("id") == target:
            return item
    return None


def get_integration_file(item_id: str) -> Path | None:
    item = get_integration(item_id)
    if not item:
        return None
    path = _integrations_dir() / Path(item["file"]).name
    return path if path.is_file() else None


def save_integration(
    src_path: Path,
    *,
    name: str = "",
    mode: str = "",
    position: str = "after_hook",
    hook_sec: float = 3.0,
    start_sec: float = 0.0,
    duration_sec: float = 0.0,
    scale_pct: int = 75,
) -> dict:
    src_path = Path(src_path)
    if not src_path.is_file():
        raise IntegrationError(f"файл интеграции не найден: {src_path}")
    suffix = src_path.suffix.lower()
    if suffix not in ALLOWED_EXTS:
        raise IntegrationError(f"неподдерживаемый формат интеграции: {suffix}")

    item_id = f"ad_{uuid.uuid4().hex[:10]}"
    safe_name = f"{item_id}{suffix}"
    dest = _integrations_dir() / safe_name
    shutil.copy2(src_path, dest)

    kind = "image" if suffix in IMAGE_EXTS else "video"
    resolved_mode = mode.strip().lower() if mode else ("overlay" if kind == "image" else "insert")
    if resolved_mode not in {"insert", "overlay"}:
        resolved_mode = "overlay" if kind == "image" else "insert"

    entry = {
        "id": item_id,
        "name": (name or src_path.stem or "Интеграция").strip()[:80],
        "file": safe_name,
        "kind": kind,
        "mode": resolved_mode,
        "position": position or ("bottom" if resolved_mode == "overlay" else "after_hook"),
        "hook_sec": float(hook_sec or 3.0),
        "start_sec": max(0.0, float(start_sec or 0.0)),
        "duration_sec": max(0.0, float(duration_sec or 0.0)),
        "scale_pct": max(15, min(100, int(scale_pct or 75))),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    items = _read_manifest()
    items.insert(0, entry)
    _write_manifest(items)
    return get_integration(item_id) or entry


def delete_integration(item_id: str) -> bool:
    items = _read_manifest()
    kept: list[dict] = []
    removed_file = ""
    for item in items:
        if item.get("id") == item_id:
            removed_file = str(item.get("file") or "")
            continue
        kept.append(item)
    if len(kept) == len(items):
        return False
    _write_manifest(kept)
    if removed_file:
        (_integrations_dir() / Path(removed_file).name).unlink(missing_ok=True)
    return True


def _probe_stream(path: Path) -> dict:
    res = subprocess.run(
        [
            S.ffprobe_path(), "-v", "error",
            "-show_entries", "stream=codec_type,width,height:format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if res.returncode != 0:
        raise IntegrationError(f"ffprobe не прочитал файл: {path.name}")
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    return {
        "width": int((video or {}).get("width") or 1080),
        "height": int((video or {}).get("height") or 1920),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
    }


def _run_ffmpeg(args: list[str]) -> None:
    res = subprocess.run(
        [S.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if res.returncode != 0:
        tail = (res.stderr or res.stdout or "").strip()[-900:]
        raise IntegrationError(f"ffmpeg интеграция упала: {tail}")


def apply_integration(
    clip_path: Path,
    spec: IntegrationSpec,
    out_path: Path,
    *,
    crf: int = 20,
    fps: int = 30,
    video_encoder_args: list[str] | None = None,
    log: Callable[[str], None] = print,
) -> Path:
    """Применяет видео-вставку или баннер-оверлей к готовому клипу."""
    clip_path = Path(clip_path)
    ad_path = Path(spec.file_path)
    if not clip_path.is_file():
        raise IntegrationError(f"клип не найден: {clip_path}")
    if not ad_path.is_file():
        raise IntegrationError(f"файл интеграции не найден: {ad_path}")

    clip_info = _probe_stream(clip_path)
    w = clip_info["width"] or 1080
    h = clip_info["height"] or 1920
    enc_args = video_encoder_args or ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf)]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Режим оверлея (PNG / GIF / WebP / MP4 поверх клипа).
    if spec.mode == "overlay" or ad_path.suffix.lower() in IMAGE_EXTS:
        ad_w = max(80, int(round(w * max(15, min(100, spec.scale_pct)) / 100.0)))
        if ad_w % 2:
            ad_w += 1
        pos = (spec.position or "bottom").lower()
        if pos == "top":
            y_expr = "88"
        elif pos == "center":
            y_expr = "(H-h)/2"
        else:
            y_expr = "H-h-140"
        x_expr = "(W-w)/2"

        enable_expr = ""
        if spec.duration_sec > 0.1:
            t0 = max(0.0, float(spec.start_sec))
            t1 = t0 + float(spec.duration_sec)
            enable_expr = f":enable='between(t,{t0:.2f},{t1:.2f})'"
        elif spec.start_sec > 0.1:
            enable_expr = f":enable='gte(t,{spec.start_sec:.2f})'"

        is_gif = ad_path.suffix.lower() == ".gif"
        is_img = ad_path.suffix.lower() in IMAGE_EXTS and not is_gif
        in_args = ["-loop", "1", "-i", str(ad_path)] if is_img else (
            ["-ignore_loop", "0", "-i", str(ad_path)] if is_gif else ["-i", str(ad_path)]
        )
        graph = (
            f"[1:v]scale={ad_w}:-2:flags=lanczos,format=rgba[ad];"
            f"[0:v][ad]overlay={x_expr}:{y_expr}:shortest=1{enable_expr}[v]"
        )
        cmd = [
            "-i", str(clip_path), *in_args,
            "-filter_complex", graph, "-map", "[v]",
        ]
        if clip_info["has_audio"]:
            cmd += ["-map", "0:a", "-c:a", "copy"]
        cmd += [*enc_args, "-pix_fmt", "yuv420p", "-r", str(fps), "-movflags", "+faststart", str(out_path)]
        _run_ffmpeg(cmd)
        log(f"интеграция-баннер ({pos}) наложена на {clip_path.name}")
        return out_path

    # 2. Режим видео-вставки (preroll / after_hook / postroll).
    ad_info = _probe_stream(ad_path)
    ad_dur = max(0.2, ad_info["duration"])
    clip_dur = max(0.5, clip_info["duration"])
    pos = (spec.position or "after_hook").lower()

    def norm_video(idx: int, label: str, trim: str = "") -> str:
        t_part = f"{trim}," if trim else ""
        return (
            f"[{idx}:v]{t_part}fps={fps},"
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x0a0a0f,setsar=1,format=yuv420p[{label}]"
        )

    def norm_audio(idx: int, has_a: bool, dur: float, label: str, trim: str = "") -> tuple[str, bool]:
        if has_a:
            t_part = f"{trim}," if trim else ""
            return f"[{idx}:a]{t_part}aresample=48000,aformat=channel_layouts=stereo[{label}]", False
        return f"anullsrc=r=48000:cl=stereo:d={dur:.3f}[{label}]", True

    if pos == "after_hook" and clip_dur > max(2.5, spec.hook_sec + 1.5):
        split_t = max(1.0, min(clip_dur - 1.0, float(spec.hook_sec or 3.0)))
        parts = [
            norm_video(0, "v0", f"trim=0:{split_t:.3f},setpts=PTS-STARTPTS"),
            norm_video(1, "v1"),
            norm_video(0, "v2", f"trim={split_t:.3f},setpts=PTS-STARTPTS"),
        ]
        a0, _ = norm_audio(0, clip_info["has_audio"], split_t, "a0", f"atrim=0:{split_t:.3f},asetpts=PTS-STARTPTS")
        a1, _ = norm_audio(1, ad_info["has_audio"], ad_dur, "a1")
        a2, _ = norm_audio(0, clip_info["has_audio"], clip_dur - split_t, "a2", f"atrim={split_t:.3f},asetpts=PTS-STARTPTS")
        parts += [a0, a1, a2, "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]"]
    elif pos == "start":
        parts = [norm_video(1, "v0"), norm_video(0, "v1")]
        a0, _ = norm_audio(1, ad_info["has_audio"], ad_dur, "a0")
        a1, _ = norm_audio(0, clip_info["has_audio"], clip_dur, "a1")
        parts += [a0, a1, "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"]
    else:  # end
        parts = [norm_video(0, "v0"), norm_video(1, "v1")]
        a0, _ = norm_audio(0, clip_info["has_audio"], clip_dur, "a0")
        a1, _ = norm_audio(1, ad_info["has_audio"], ad_dur, "a1")
        parts += [a0, a1, "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"]

    cmd = [
        "-i", str(clip_path), "-i", str(ad_path),
        "-filter_complex", ";".join(parts),
        "-map", "[v]", "-map", "[a]",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        *enc_args, "-pix_fmt", "yuv420p", "-r", str(fps), "-movflags", "+faststart",
        str(out_path),
    ]
    _run_ffmpeg(cmd)
    log(f"интеграция-ролик ({pos}) вшита в {clip_path.name}")
    return out_path
