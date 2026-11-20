# -*- coding: utf-8 -*-
"""Очередь задач: одна задача = один поток + прогресс + логи + результат.

UI опрашивает GET /api/jobs/{id} раз в секунду, поэтому SSE не нужен.
Отмена — через threading.Event, который проверяет пайплайн.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

MAX_LOG_LINES = 400


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    kind: str
    meta: dict = field(default_factory=dict)
    status: str = "queued"          # queued | running | done | error | cancelled
    progress: float = 0.0
    stage: str = ""
    message: str = ""
    logs: list = field(default_factory=list)
    result: dict | None = None
    error: str = ""
    created_at: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def log(self, text: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
        self.logs.append(line)
        if len(self.logs) > MAX_LOG_LINES:
            del self.logs[: len(self.logs) - MAX_LOG_LINES]

    def set_progress(self, value: float, stage: str = "", message: str = "") -> None:
        self.progress = max(0.0, min(100.0, float(value)))
        if stage:
            self.stage = stage
        if message:
            self.message = message

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def to_dict(self, *, with_logs: bool = True) -> dict:
        data = {
            "id": self.id,
            "kind": self.kind,
            "meta": self.meta,
            "status": self.status,
            "progress": round(self.progress, 1),
            "stage": self.stage,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        if with_logs:
            data["logs"] = list(self.logs)
        return data


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[Job], None], *, meta: dict | None = None,
               job_id: str | None = None) -> Job:
        job = Job(id=job_id or uuid.uuid4().hex[:12], kind=kind, meta=meta or {})
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > 200:
                old = self._order.pop(0)
                self._jobs.pop(old, None)

        def runner() -> None:
            job.status = "running"
            job.started_at = _now()
            job.set_progress(0.5, "старт", "")
            try:
                fn(job)
                if job.cancelled() and job.status == "running":
                    job.status = "cancelled"
                    job.message = "отменено"
                elif job.status == "running":
                    job.status = "done"
                    job.progress = 100.0
                    job.message = job.message or "готово"
            except Exception as exc:  # задача не должна ронять поток и приложение
                job.status = "error"
                job.error = str(exc) or exc.__class__.__name__
                job.message = job.error
                job.log(f"ОШИБКА: {job.error}")
                job.log(traceback.format_exc(limit=3).strip().splitlines()[-1])
            finally:
                job.finished_at = _now()

        threading.Thread(target=runner, name=f"job-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            ids = list(reversed(self._order))[:limit]
            return [self._jobs[i].to_dict(with_logs=False) for i in ids if i in self._jobs]

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job:
            return False
        if job.status in {"done", "error", "cancelled"}:
            return False
        job.cancel_event.set()
        job.log("запрошена отмена")
        return True

    def drop_finished(self) -> None:
        kept: list[str] = []
        with self._lock:
            for job_id in self._order:
                job = self._jobs.get(job_id)
                if job and job.status in {"done", "error", "cancelled"}:
                    continue
                kept.append(job_id)
            self._order = kept
            for job_id in list(self._jobs):
                if job_id not in kept:
                    self._jobs.pop(job_id, None)


MANAGER = JobManager()
