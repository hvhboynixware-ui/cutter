# -*- coding: utf-8 -*-
"""HTTP-слой: локальный API для окна приложения. Только 127.0.0.1, без авторизации."""

from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import jobs
from .core import settings as S
from .core import models as M
from .core import pipeline

VERSION = "1.0.0"
WEB_DIR = Path(__file__).resolve().parent / "web"
KINDS = {"clips", "previews"}

app = FastAPI(title="Cutter", version=VERSION, docs_url=None, redoc_url=None)


# ---------------------------------------------------------------- утилиты

def _job_dir(job_id: str) -> Path:
    path = S.outdir(S.load()) / _safe_id(job_id)
    return path


def _safe_id(value: str) -> str:
    safe = "".join(ch for ch in str(value) if ch.isalnum() or ch in "-_")
    if not safe:
        raise HTTPException(400, "плохой идентификатор")
    return safe


def _clip_payload(job_id: str, clips: list[dict]) -> list[dict]:
    result = []
    for clip in clips:
        item = dict(clip)
        item["video_url"] = f"/media/{job_id}/clips/{clip['file']}"
        item["preview_url"] = f"/media/{job_id}/previews/{clip['preview']}"
        result.append(item)
    return result


def _require(job_id: str):
    job = jobs.MANAGER.get(job_id)
    if not job:
        raise HTTPException(404, "задача не найдена")
    return job


# ---------------------------------------------------------------- health / settings

@app.get("/api/health")
def health() -> dict:
    settings = S.load()
    try:
        from .core.upload import browsers
        edge = bool(browsers.detect_browser_channel())
        browser_error = "" if edge else "не нашёл Chrome/Edge"
    except Exception as exc:  # модуль загрузки может быть ещё не собран
        edge = False
        browser_error = str(exc)[:200]

    installed = S.installed_models()
    return {
        "ok": True,
        "version": VERSION,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "python": sys.version.split()[0],
        "browser": edge,
        "browser_error": browser_error,
        "models": installed,
        "whisper_size": settings["stt"]["whisper_size"],
        "jobs": len(jobs.MANAGER.list(10)),
    }


@app.get("/api/settings")
def get_settings() -> dict:
    return S.mask_secrets(S.load())


@app.put("/api/settings")
def put_settings(patch: dict = Body(...)) -> dict:
    merged = S.update(patch)
    return S.mask_secrets(merged)


@app.get("/api/providers")
def providers() -> dict:
    try:
        from .core import llm
        return {"providers": llm.available_providers()}
    except Exception as exc:
        return {"providers": [], "error": str(exc)[:300]}


@app.get("/api/looks")
def looks() -> dict:
    """Пресеты кадра и музыка — для панели «Стиль кадра»."""
    from .core import effects as FX
    return {"effects": FX.effect_ids(), "music": FX.list_music()}


@app.post("/api/providers/test")
def provider_test(body: dict = Body(...)) -> dict:
    try:
        from .core import llm
    except Exception as exc:
        return {"ok": False, "message": f"модуль LLM недоступен: {exc}"}
    settings = S.load()
    provider = body.get("provider") or settings["llm"]["provider"]
    api_key = body.get("api_key") or S.resolve_key(settings, provider)
    cfg = llm.LLMConfig(provider=provider, model=body.get("model") or settings["llm"]["model"], api_key=api_key)
    try:
        ok, message = llm.ping(cfg)
    except Exception as exc:
        ok, message = False, str(exc)
    return {"ok": bool(ok), "message": message}


# ---------------------------------------------------------------- модели whisper

@app.get("/api/models")
def models() -> dict:
    return {"installed": S.installed_models(), "sizes": M.SIZES}


@app.post("/api/models/download")
def models_download(body: dict = Body(...)) -> dict:
    size = str(body.get("size") or "base")

    def run(job: jobs.Job) -> None:
        job.set_progress(1, "скачивание", f"модель {size}")
        target = M.download_model(
            size,
            log=job.log,
            progress=lambda pct, message: job.set_progress(pct * 0.98 + 1, "скачивание", message),
            cancel=job.cancelled,
        )
        S.update({"stt": {"whisper_size": size}})
        job.result = {"size": size, "path": str(target)}
        job.set_progress(100, "готово", f"модель {size} скачана")

    job = jobs.MANAGER.submit("download_model", run, meta={"size": size})
    return {"job_id": job.id}


# ---------------------------------------------------------------- нарезка

def _analyze_runner(job: jobs.Job, source: Path, job_dir: Path, settings: dict) -> None:
    result = pipeline.analyze(job=job, source=source, workdir=job_dir, settings=settings)
    job.result = {
        "clips": _clip_payload(job.id, result["clips"]),
        "count": len(result["clips"]),
        "source": source.name,
    }


@app.post("/api/analyze")
async def analyze(
    file: UploadFile | None = File(default=None),
    path: str = Form(default=""),
    clips: int = Form(default=5),
    style: str = Form(default="classic"),
    effect: str = Form(default="none"),
    protection: str = Form(default="off"),
    music: str = Form(default="none"),
    music_volume: float = Form(default=0.18),
    min_dur: float = Form(default=18),
    max_dur: float = Form(default=60),
) -> dict:
    settings = S.load()
    S.update({"render": {
        "clips": clips, "style": style, "min_dur": min_dur, "max_dur": max_dur,
        "effect": effect, "protection": protection, "music": music, "music_volume": music_volume,
    }})
    settings = S.load()

    if file is None and not path.strip():
        raise HTTPException(400, "нужен файл или путь к нему")

    job_id = uuid.uuid4().hex[:12]
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    if file is not None:
        suffix = Path(file.filename or "source.mp4").suffix or ".mp4"
        source = job_dir / f"source{suffix}"
        with open(source, "wb") as handle:
            while chunk := await file.read(1 << 22):
                handle.write(chunk)
    else:
        source = Path(path.strip()).expanduser()
        if not source.is_file():
            raise HTTPException(400, f"файл не найден: {source}")
        target = job_dir / f"source{source.suffix or '.mp4'}"
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        source = target

    def run(job: jobs.Job) -> None:
        _analyze_runner(job, source, job_dir, settings)

    job = jobs.MANAGER.submit("analyze", run, job_id=job_id, meta={"job_dir": str(job_dir), "source": source.name})
    return {"job_id": job.id}


@app.post("/api/youtube")
def youtube(body: dict = Body(...)) -> dict:
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "пустая ссылка")
    settings = S.load()
    S.update({"render": {
        "clips": int(body.get("clips", 5)),
        "style": body.get("style", "classic"),
        "effect": body.get("effect", "none"),
        "protection": body.get("protection", "off"),
        "music": body.get("music", "none"),
        "music_volume": float(body.get("music_volume", 0.18)),
        "min_dur": float(body.get("min_dur", 18)),
        "max_dur": float(body.get("max_dur", 60)),
    }})
    settings = S.load()
    max_height = int(body.get("max_height", 1080))

    job_id = uuid.uuid4().hex[:12]
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    def run(job: jobs.Job) -> None:
        from .core import youtube as YT
        job.set_progress(1, "скачивание", "получаю информацию о видео")
        try:
            info = YT.probe_url(url)
            job.log(f"видео: {info.get('title', '?')} ({int(info.get('duration', 0))} с)")
        except Exception as exc:
            raise pipeline.PipelineError(f"не смог прочитать ссылку: {exc}")

        def on_progress(data: dict) -> None:
            job.set_progress(2 + float(data.get("percent", 0)) * 0.16, "скачивание",
                             f"{data.get('percent', 0):.0f}% · {data.get('speed', '')}")

        source = YT.download(url, job_dir, max_height=max_height,
                             on_progress=on_progress, cancel=job.cancelled)
        job.log(f"скачано: {Path(str(source.path)).name}")
        _analyze_runner(job, Path(str(source.path)), job_dir, settings)

    job = jobs.MANAGER.submit("youtube", run, job_id=job_id, meta={"job_dir": str(job_dir), "url": url})
    return {"job_id": job.id}


# ---------------------------------------------------------------- задачи

@app.get("/api/jobs")
def jobs_list(limit: int = Query(default=30, ge=1, le=200)) -> dict:
    return {"jobs": jobs.MANAGER.list(limit)}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    job = _require(job_id)
    data = job.to_dict()
    if job.result and "clips" in (job.result or {}):
        data["result"]["clips"] = _clip_payload(job.id, job.result["clips"])
    return data


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str) -> dict:
    return {"ok": jobs.MANAGER.cancel(job_id)}


# ---------------------------------------------------------------- загрузка на площадки

@app.get("/api/clips")
def clips(job_id: str = Query(...)) -> dict:
    job_dir = _job_dir(job_id)
    manifest = job_dir / "clips.json"
    if not manifest.is_file():
        return {"clips": [], "source": ""}
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return {"clips": _clip_payload(job_id, data.get("clips", [])), "source": data.get("source", "")}


@app.post("/api/upload")
def upload(body: dict = Body(...)) -> dict:
    job_id = _safe_id(str(body.get("job_id") or ""))
    clip_id = str(body.get("clip_id") or "")
    platforms = [str(p) for p in (body.get("platforms") or [])]
    if not platforms:
        raise HTTPException(400, "не выбрана ни одна платформа")
    dry_run = bool(body.get("dry_run", True))

    job_dir = _job_dir(job_id)
    manifest_path = job_dir / "clips.json"
    if not manifest_path.is_file():
        raise HTTPException(404, "клипы для этой задачи не найдены")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    clip = next((c for c in manifest.get("clips", []) if str(c.get("id")) == clip_id), None)
    if not clip:
        raise HTTPException(404, "клип не найден")

    settings = S.load()
    browser_cfg = body.get("browser") or {}
    engine = browser_cfg.get("engine") or settings["upload"]["engine"]
    profile_id = browser_cfg.get("profile_id") or settings["upload"]["profile_id"]
    visibility = str(body.get("visibility") or settings["upload"]["visibility"])
    # адаптеры читают видимость из настроек — фиксируем выбор пользователя на этот запуск
    S.update({"upload": {"visibility": visibility, "engine": engine}})

    def run(job: jobs.Job) -> None:
        from .core.upload import browsers, get_uploader
        from .core.upload.base import BrowserTarget, UploadRequest

        video = job_dir / "clips" / clip["file"]
        if not video.is_file():
            raise pipeline.PipelineError(f"файл клипа пропал: {video}")
        title = (clip.get("title") or "shorts")[:100]
        description = clip.get("hook") or ""
        tags = list(clip.get("hashtags") or [])

        target = BrowserTarget(engine=engine, profile_id=profile_id, headless=False)
        job.log(f"браузер: {engine}, профиль {profile_id or 'по умолчанию'}")
        session = browsers.open_session(target, persistent_root=S.DATA_DIR / "browser_profiles", log=job.log)
        results = []
        try:
            for index, platform in enumerate(platforms, start=1):
                if job.cancelled():
                    raise pipeline.PipelineError("отменено")
                job.set_progress((index - 1) / len(platforms), "загрузка", f"{platform}")
                job.log(f"— {platform}: {title[:60]}")
                uploader = get_uploader(platform)
                request = UploadRequest(
                    video_path=video,
                    title=title,
                    description=description,
                    hashtags=tags,
                    platform=platform,
                    account_label=profile_id or engine,
                    dry_run=dry_run,
                )
                result = uploader.upload(session, request, job.log)
                results.append({
                    "platform": platform,
                    "ok": bool(result.ok),
                    "url": result.url,
                    "message": result.message,
                    "screenshot": str(result.screenshot) if result.screenshot else "",
                })
        finally:
            browsers.close_session(session, job.log)
        job.result = {"uploads": results, "dry_run": dry_run, "visibility": visibility}
        ok_count = sum(1 for item in results if item["ok"])
        job.set_progress(100, "готово", f"{ok_count} из {len(results)} загружено")

    job = jobs.MANAGER.submit("upload", run, meta={"job_id": job_id, "clip_id": clip_id, "platforms": platforms})
    return {"job_id": job.id}


@app.get("/api/browsers")
def browser_profiles(engine: str = Query(default="plain")) -> dict:
    try:
        from .core.upload import browsers
    except Exception as exc:
        return {"running": False, "profiles": [], "error": str(exc)[:300]}
    token = S.load()["upload"].get("antidetect_token", "")
    running, profiles, error = browsers.list_profiles(engine, token=token)
    return {"running": running, "profiles": profiles, "error": error}


@app.post("/api/open-folder")
def open_folder(body: dict = Body(...)) -> dict:
    job_id = _safe_id(str(body.get("job_id") or ""))
    folder = _job_dir(job_id) / "clips"
    if not folder.is_dir():
        raise HTTPException(404, "папки ещё нет")
    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(folder)])
    return {"ok": True, "path": str(folder)}


# ---------------------------------------------------------------- статика и медиа

@app.get("/media/{job_id}/{kind}/{name}")
def media(job_id: str, kind: str, name: str) -> FileResponse:
    if kind not in KINDS:
        raise HTTPException(404, "не найдено")
    safe_name = Path(name).name
    path = _job_dir(job_id) / kind / safe_name
    if not path.is_file():
        raise HTTPException(404, "файл не найден")
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@app.get("/")
def index() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


if WEB_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets")), name="assets")
