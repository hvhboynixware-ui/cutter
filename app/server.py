# -*- coding: utf-8 -*-
"""HTTP-слой: локальный API для окна приложения. Только 127.0.0.1, без авторизации."""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import jobs
from .core import settings as S
from .core import models as M
from .core import pipeline

VERSION = "1.1.0"
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

    installed = M.installed_models()
    try:
        from .core import accounts
        acc_count = len(accounts.list_accounts())
    except Exception:
        acc_count = 0
    return {
        "ok": True,
        "version": VERSION,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "python": sys.version.split()[0],
        "browser": edge,
        "browser_error": browser_error,
        "models": installed,
        "whisper_size": settings["stt"]["whisper_size"],
        "encoder": pipeline.detect_video_encoder("auto"),
        "accounts": acc_count,
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
        return {
            "providers": llm.available_providers(),
            "account_providers": llm.account_providers(),
        }
    except Exception as exc:
        return {"providers": [], "account_providers": [], "error": str(exc)[:300]}


@app.get("/api/looks")
def looks() -> dict:
    """Пресеты кадра, музыка, голоса озвучки и аппаратный энкодер."""
    from .core import effects as FX
    from .core import tts
    return {
        "effects": FX.effect_ids(),
        "music": FX.list_music(),
        "voices": tts.available_voices(),
        "encoder": pipeline.detect_video_encoder("auto"),
    }


@app.post("/api/providers/test")
def provider_test(body: dict = Body(...)) -> dict:
    try:
        from .core import llm
    except Exception as exc:
        return {"ok": False, "message": f"модуль LLM недоступен: {exc}"}
    settings = S.load()
    provider = body.get("provider") or settings["llm"]["provider"]
    api_key = body.get("api_key") or S.resolve_key(settings, provider)
    cfg = llm.LLMConfig(
        provider=provider,
        model=body.get("model") or settings["llm"]["model"],
        api_key=api_key,
        base_url=body.get("base_url") or settings["llm"].get("base_url", ""),
        account_id=str(body.get("account_id") or settings["llm"].get("account_id") or ""),
    )
    try:
        ok, message = llm.ping(cfg)
    except Exception as exc:
        ok, message = False, str(exc)
    return {"ok": bool(ok), "message": message}


# ---------------------------------------------------------------- аккаунты Google / ChatGPT

@app.get("/api/accounts")
def accounts_list() -> dict:
    from .core import accounts
    return {"accounts": accounts.list_accounts(mask=True)}


@app.post("/api/accounts/login")
def accounts_login(body: dict = Body(...)) -> dict:
    from .core import accounts

    provider = str(body.get("provider") or "").strip().lower()
    auth_method = str(body.get("auth_method") or ("oauth" if provider == "chatgpt" else "browser")).strip().lower()
    label = str(body.get("label") or "").strip()
    account_id = str(body.get("account_id") or "").strip()
    if provider not in {"chatgpt", "google"}:
        raise HTTPException(400, "поддерживаются только chatgpt и google")

    def run(job: jobs.Job) -> None:
        job.set_progress(10, "вход", f"авторизация {provider} ({auth_method})")
        if provider == "chatgpt" and auth_method == "oauth":
            saved = accounts.start_oauth_login(
                provider, label=label, account_id=account_id, log=job.log, cancel=job.cancelled,
            )
        else:
            saved = accounts.start_browser_login(
                provider, label=label, account_id=account_id, log=job.log, cancel=job.cancelled,
            )
        llm_prov = "chatgpt_sub" if provider == "chatgpt" else "google_sub"
        S.update({"llm": {"provider": llm_prov, "account_id": saved["id"]}})
        job.result = {"account": saved}
        job.set_progress(100, "готово", f"аккаунт {saved['label']} подключён")

    job = jobs.MANAGER.submit("login", run, meta={"provider": provider, "auth_method": auth_method})
    return {"job_id": job.id}


@app.post("/api/accounts/select")
def accounts_select(body: dict = Body(...)) -> dict:
    from .core import accounts

    account_id = str(body.get("account_id") or "").strip()
    acc = accounts.get_account(account_id, mask=True)
    if not acc:
        raise HTTPException(404, "аккаунт не найден")
    llm_prov = "chatgpt_sub" if acc["provider"] == "chatgpt" else "google_sub"
    patch: dict = {"llm": {"account_id": acc["id"]}}
    if body.get("activate_provider", True):
        patch["llm"]["provider"] = llm_prov
        patch["llm"]["model"] = "gpt-5.1-codex-mini" if llm_prov == "chatgpt_sub" else "gemini-web"
    merged = S.update(patch)
    return {"ok": True, "account": acc, "settings": S.mask_secrets(merged)}


@app.post("/api/accounts/test")
def accounts_test(body: dict = Body(...)) -> dict:
    from .core import accounts
    account_id = str(body.get("account_id") or "").strip()
    model = str(body.get("model") or "").strip()
    ok, message = accounts.test_account(account_id, model=model)
    return {"ok": bool(ok), "message": message}


@app.delete("/api/accounts/{account_id}")
def accounts_delete(account_id: str) -> dict:
    from .core import accounts
    removed = accounts.remove_account(account_id)
    if not removed:
        raise HTTPException(404, "аккаунт не найден")
    settings = S.load()
    if (settings.get("llm") or {}).get("account_id") == account_id:
        S.update({"llm": {"account_id": ""}})
    return {"ok": True, "id": account_id}


# ---------------------------------------------------------------- модели whisper

@app.get("/api/models")
def models_list() -> dict:
    return {"installed": M.installed_models(), "sizes": M.SIZES}


@app.post("/api/models/delete")
def models_delete(body: dict = Body(...)) -> dict:
    size = str(body.get("size") or "")
    removed = M.delete_model(size)
    if removed:
        S.update({"stt": {"whisper_size": "base"}})
    return {"ok": removed, "size": size}


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

def _analyze_runner(job: jobs.Job, source: Path, job_dir: Path, settings: dict,
                    progress_offset: float = 0.0) -> None:
    result = pipeline.analyze(job=job, source=source, workdir=job_dir, settings=settings,
                              progress_offset=progress_offset)
    job.result = {
        "clips": _clip_payload(job.id, result["clips"]),
        "count": len(result["clips"]),
        "source": source.name,
    }


@app.post("/api/analyze")
async def analyze(
    file: UploadFile | None = File(default=None),
    path: str = Form(default=""),
    mode: str = Form(default="moments"),
    clips: int = Form(default=5),
    style: str = Form(default="classic"),
    framing: str = Form(default="auto"),
    subtitles: bool = Form(default=True),
    encoder: str = Form(default="auto"),
    effect: str = Form(default="none"),
    protection: str = Form(default="off"),
    music: str = Form(default="none"),
    music_volume: float = Form(default=0.18),
    voice: str = Form(default="dmitry_drama"),
    orig_audio_vol: float = Form(default=0.08),
    brainrot_rewrite: bool = Form(default=True),
    integration_id: str = Form(default=""),
    min_dur: float = Form(default=18),
    max_dur: float = Form(default=60),
) -> dict:
    S.update({"render": {
        "mode": mode, "clips": clips, "style": style, "framing": framing,
        "subtitles": subtitles, "encoder": encoder,
        "min_dur": min_dur, "max_dur": max_dur,
        "effect": effect, "protection": protection, "music": music, "music_volume": music_volume,
        "voice": voice, "orig_audio_vol": orig_audio_vol, "brainrot_rewrite": brainrot_rewrite,
        "integration_id": integration_id,
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
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
        source = target

    def run(job: jobs.Job) -> None:
        _analyze_runner(job, source, job_dir, settings)

    job = jobs.MANAGER.submit("analyze", run, job_id=job_id, meta={"job_dir": str(job_dir), "source": source.name, "mode": mode})
    return {"job_id": job.id}


@app.post("/api/youtube")
def youtube(body: dict = Body(...)) -> dict:
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "пустая ссылка")
    S.update({"render": {
        "mode": str(body.get("mode", "moments")),
        "clips": int(body.get("clips", 5)),
        "style": body.get("style", "classic"),
        "framing": str(body.get("framing", "auto")),
        "subtitles": bool(body.get("subtitles", True)),
        "encoder": str(body.get("encoder", "auto")),
        "effect": body.get("effect", "none"),
        "protection": body.get("protection", "off"),
        "music": body.get("music", "none"),
        "music_volume": float(body.get("music_volume", 0.18)),
        "voice": str(body.get("voice", "dmitry_drama")),
        "orig_audio_vol": float(body.get("orig_audio_vol", 0.08)),
        "brainrot_rewrite": bool(body.get("brainrot_rewrite", True)),
        "integration_id": str(body.get("integration_id", "")),
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
        # скачивание занимает первые 18% общей шкалы, дальше нарезка
        _analyze_runner(job, Path(str(source.path)), job_dir, settings, progress_offset=18.0)

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


@app.post("/api/upload/batch")
def upload_batch(body: dict = Body(...)) -> dict:
    """Пакетная загрузка нескольких клипов в одной открытой сессии браузера."""
    job_id = _safe_id(str(body.get("job_id") or ""))
    clip_ids = [str(c) for c in (body.get("clip_ids") or [])]
    platforms = [str(p) for p in (body.get("platforms") or [])]
    if not clip_ids:
        raise HTTPException(400, "не выбрано ни одного клипа")
    if not platforms:
        raise HTTPException(400, "не выбрана ни одна платформа")
    dry_run = bool(body.get("dry_run", True))

    job_dir = _job_dir(job_id)
    manifest_path = job_dir / "clips.json"
    if not manifest_path.is_file():
        raise HTTPException(404, "клипы для этой задачи не найдены")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_id = {str(c.get("id")): c for c in manifest.get("clips", [])}
    picked = [by_id[cid] for cid in clip_ids if cid in by_id]
    if not picked:
        raise HTTPException(404, "выбранные клипы не найдены")

    settings = S.load()
    browser_cfg = body.get("browser") or {}
    engine = browser_cfg.get("engine") or settings["upload"]["engine"]
    profile_id = browser_cfg.get("profile_id") or settings["upload"]["profile_id"]
    visibility = str(body.get("visibility") or settings["upload"]["visibility"])
    S.update({"upload": {"visibility": visibility, "engine": engine}})

    def run(job: jobs.Job) -> None:
        from .core.upload import browsers, get_uploader
        from .core.upload.base import BrowserTarget, UploadRequest

        target = BrowserTarget(engine=engine, profile_id=profile_id, headless=False)
        job.log(f"пакетная загрузка: {len(picked)} клипов × {len(platforms)} платформ ({engine})")
        session = browsers.open_session(target, persistent_root=S.DATA_DIR / "browser_profiles", log=job.log)
        results = []
        total_steps = len(picked) * len(platforms)
        step = 0
        try:
            for clip in picked:
                video = job_dir / "clips" / clip["file"]
                if not video.is_file():
                    results.append({"clip_id": clip.get("id"), "platform": "-", "ok": False, "url": "", "message": f"файл пропал: {clip['file']}"})
                    step += len(platforms)
                    continue
                title = (clip.get("title") or "shorts")[:100]
                description = clip.get("hook") or ""
                tags = list(clip.get("hashtags") or [])
                for platform in platforms:
                    if job.cancelled():
                        raise pipeline.PipelineError("отменено")
                    job.set_progress(step / max(1, total_steps) * 100.0, "загрузка", f"{clip.get('title', clip.get('id'))[:24]} → {platform}")
                    job.log(f"— клип {clip.get('id')} → {platform}: {title[:50]}")
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
                    res = uploader.upload(session, request, job.log)
                    results.append({
                        "clip_id": clip.get("id"),
                        "platform": platform,
                        "ok": bool(res.ok),
                        "url": res.url,
                        "message": res.message,
                        "screenshot": str(res.screenshot) if res.screenshot else "",
                    })
                    step += 1
        finally:
            browsers.close_session(session, job.log)

        job.result = {"uploads": results, "dry_run": dry_run, "visibility": visibility}
        ok_count = sum(1 for item in results if item["ok"])
        job.set_progress(100, "готово", f"{ok_count} из {len(results)} загружено")

    job = jobs.MANAGER.submit("upload", run, meta={"job_id": job_id, "clip_ids": clip_ids, "platforms": platforms})
    return {"job_id": job.id}


@app.patch("/api/clips/{job_id}/{clip_id}")
def update_clip_meta(job_id: str, clip_id: str, body: dict = Body(...)) -> dict:
    job_dir = _job_dir(job_id)
    manifest_path = job_dir / "clips.json"
    if not manifest_path.is_file():
        raise HTTPException(404, "клипы не найдены")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = next((c for c in manifest.get("clips", []) if str(c.get("id")) == str(clip_id)), None)
    if not target:
        raise HTTPException(404, "клип не найден")

    if "title" in body:
        target["title"] = str(body["title"] or "")[:120]
    if "hook" in body:
        target["hook"] = str(body["hook"] or "")[:200]
    if "hashtags" in body:
        raw_tags = body["hashtags"]
        if isinstance(raw_tags, str):
            raw_tags = [t.strip().lstrip("#") for t in raw_tags.replace(",", " ").split() if t.strip()]
        if isinstance(raw_tags, list):
            target["hashtags"] = [str(t).strip().lstrip("#") for t in raw_tags if str(t).strip()][:15]

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "clip": _clip_payload(job_id, [target])[0]}


@app.post("/api/clips/{job_id}/{clip_id}/recut")
def recut_clip(job_id: str, clip_id: str, body: dict = Body(...)) -> dict:
    """Перерезает один клип по новым границам start/end без повторного прогона Whisper/LLM."""
    job_dir = _job_dir(job_id)
    manifest_path = job_dir / "clips.json"
    if not manifest_path.is_file():
        raise HTTPException(404, "клипы не найдены")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    clip = next((c for c in manifest.get("clips", []) if str(c.get("id")) == str(clip_id)), None)
    if not clip:
        raise HTTPException(404, "клип не найден")

    source_candidates = [
        Path(str(manifest.get("source_path") or "")),
        *list(job_dir.glob("source.*")),
        *[p for p in job_dir.glob("*.mp4") if p.is_file()],
    ]
    source = next((p for p in source_candidates if str(p) and p.is_file()), None)
    if source is None:
        raise HTTPException(404, "исходное видео для перерезки не найдено")

    start = max(0.0, float(body.get("start", clip["start"])))
    end = max(start + 1.0, float(body.get("end", clip["end"])))
    info = pipeline.probe(source)
    if info["duration"] > 0:
        end = min(float(info["duration"]), end)
    if end - start < 1.0:
        raise HTTPException(400, "слишком короткий интервал")

    settings = S.load()
    render_cfg = settings.get("render") or {}
    style_key = manifest.get("style") or render_cfg.get("style", "classic")
    style = pipeline.STYLES.get(style_key, pipeline.STYLES["classic"])
    framing = str(render_cfg.get("framing") or "auto").lower()
    bg_mode = style.get("background", "blur") if framing == "auto" else framing
    ratio = (info["width"] / info["height"]) if info["height"] else 16 / 9
    encoder = pipeline.detect_video_encoder(str(render_cfg.get("encoder") or "auto"))

    work = job_dir / ".recut"
    work.mkdir(exist_ok=True)
    out_mp4 = job_dir / "clips" / clip["file"]
    prev_jpg = job_dir / "previews" / clip["preview"]

    ass_path: Path | None = None
    if bool(render_cfg.get("subtitles", True)):
        key = pipeline._transcript_cache_key(source, settings["stt"]["whisper_size"], settings["stt"]["language"])
        cached = pipeline._load_cached_transcript(key)
        if cached:
            _, words = cached
            cues = pipeline.build_cues(words, start, end)
            if cues:
                ass_path = work / f"recut_{clip_id}.ass"
                pipeline.write_ass(ass_path, cues, style, "Arial Black", bool(style["uppercase"]))

    from . import effects as FX
    music_file = FX.music_path(clip.get("music") or render_cfg.get("music", "none"))
    pipeline.render_clip(
        source, start, end, ass_path, out_mp4,
        workdir=work, ratio=ratio, has_audio=info["has_audio"],
        crf=int(render_cfg.get("crf", 20)), fps=int(render_cfg.get("fps", 30)),
        background=bg_mode,
        effect=clip.get("effect") or render_cfg.get("effect", "none"),
        protection=clip.get("protection") or render_cfg.get("protection", "off"),
        music=music_file, music_volume=float(render_cfg.get("music_volume", 0.18)),
        encoder=encoder,
    )
    pipeline.make_preview(out_mp4, prev_jpg)
    shutil.rmtree(work, ignore_errors=True)

    clip["start"] = round(start, 2)
    clip["end"] = round(end, 2)
    clip["duration"] = round(end - start, 2)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "clip": _clip_payload(job_id, [clip])[0]}


# ---------------------------------------------------------------- рекламные интеграции

@app.get("/api/integrations")
def integrations_list() -> dict:
    from .core import integrator
    return {"integrations": integrator.list_integrations()}


@app.post("/api/integrations")
async def integrations_upload(
    file: UploadFile = File(...),
    name: str = Form(default=""),
    mode: str = Form(default=""),
    position: str = Form(default="after_hook"),
    hook_sec: float = Form(default=3.0),
    start_sec: float = Form(default=0.0),
    duration_sec: float = Form(default=0.0),
    scale_pct: int = Form(default=75),
) -> dict:
    from .core import integrator

    suffix = Path(file.filename or "ad.mp4").suffix.lower() or ".mp4"
    tmp = S.DATA_DIR / f"_tmp_ad_{uuid.uuid4().hex[:8]}{suffix}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(tmp, "wb") as handle:
            while chunk := await file.read(1 << 22):
                handle.write(chunk)
        item = integrator.save_integration(
            tmp,
            name=name or Path(file.filename or "Интеграция").stem,
            mode=mode, position=position, hook_sec=hook_sec,
            start_sec=start_sec, duration_sec=duration_sec, scale_pct=scale_pct,
        )
        return {"ok": True, "integration": item}
    except integrator.IntegrationError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        tmp.unlink(missing_ok=True)


@app.get("/api/integrations/{item_id}/file")
def integration_file(item_id: str) -> FileResponse:
    from .core import integrator
    path = integrator.get_integration_file(_safe_id(item_id))
    if not path:
        raise HTTPException(404, "файл интеграции не найден")
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@app.delete("/api/integrations/{item_id}")
def integration_delete(item_id: str) -> dict:
    from .core import integrator
    if not integrator.delete_integration(_safe_id(item_id)):
        raise HTTPException(404, "интеграция не найдена")
    settings = S.load()
    if (settings.get("render") or {}).get("integration_id") == item_id:
        S.update({"render": {"integration_id": ""}})
    return {"ok": True}


@app.post("/api/integrations/apply")
def integration_apply(body: dict = Body(...)) -> dict:
    """Накладывает/вшивает интеграцию в выбранные готовые клипы."""
    from .core import integrator

    job_id = _safe_id(str(body.get("job_id") or ""))
    integration_id = _safe_id(str(body.get("integration_id") or ""))
    clip_ids = [str(c) for c in (body.get("clip_ids") or [])]

    item = integrator.get_integration(integration_id)
    ad_path = integrator.get_integration_file(integration_id)
    if not item or not ad_path:
        raise HTTPException(404, "интеграция не найдена")

    job_dir = _job_dir(job_id)
    manifest_path = job_dir / "clips.json"
    if not manifest_path.is_file():
        raise HTTPException(404, "клипы задачи не найдены")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    all_clips = manifest.get("clips") or []
    targets = [c for c in all_clips if not clip_ids or str(c.get("id")) in clip_ids]
    if not targets:
        raise HTTPException(404, "подходящие клипы не найдены")

    spec = integrator.IntegrationSpec(
        file_path=ad_path,
        mode=str(body.get("mode") or item.get("mode") or "insert"),
        position=str(body.get("position") or item.get("position") or "after_hook"),
        hook_sec=float(body.get("hook_sec", item.get("hook_sec", 3.0))),
        start_sec=float(body.get("start_sec", item.get("start_sec", 0.0))),
        duration_sec=float(body.get("duration_sec", item.get("duration_sec", 0.0))),
        scale_pct=int(body.get("scale_pct", item.get("scale_pct", 75))),
    )
    settings = S.load()
    render_cfg = settings.get("render") or {}
    crf = int(render_cfg.get("crf", 20))
    fps = int(render_cfg.get("fps", 30))
    encoder = pipeline.detect_video_encoder(str(render_cfg.get("encoder") or "auto"))
    enc_args = pipeline.encoder_args(encoder, crf)

    def run(job: jobs.Job) -> None:
        job.log(f"интеграция «{item['name']}» → {len(targets)} клипов")
        for idx, clip in enumerate(targets, start=1):
            if job.cancelled():
                raise pipeline.PipelineError("отменено")
            job.set_progress((idx - 1) / len(targets) * 100, "интеграция", f"клип {idx} из {len(targets)}")
            clip_mp4 = job_dir / "clips" / clip["file"]
            if not clip_mp4.is_file():
                continue
            tmp_out = job_dir / "clips" / f".ad_{clip['file']}"
            integrator.apply_integration(
                clip_mp4, spec, tmp_out, crf=crf, fps=fps, video_encoder_args=enc_args, log=job.log,
            )
            tmp_out.replace(clip_mp4)
            prev_jpg = job_dir / "previews" / clip["preview"]
            pipeline.make_preview(clip_mp4, prev_jpg)
            try:
                probe_info = pipeline.probe(clip_mp4)
                if probe_info.get("duration"):
                    clip["duration"] = round(float(probe_info["duration"]), 2)
            except Exception:
                pass
            clip["integration"] = item["name"]

        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        job.result = {"clips": _clip_payload(job_id, all_clips), "source": manifest.get("source", "")}
        job.set_progress(100, "готово", f"интеграция добавлена в {len(targets)} клипов")

    job = jobs.MANAGER.submit("integration", run, meta={"job_id": job_id, "integration": item["name"]})
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
