# Контракты между модулями Cutter

Никаких «договорились на словах»: сигнатуры ниже — единственный интерфейс между модулями.
Если меняешь сигнатуру — меняешь этот файл в том же коммите.

## app/core/llm.py — выбор моментов через LLM

```python
@dataclass
class LLMConfig:
    provider: str = "deepseek"          # deepseek | openrouter | gemini | groq | ollama
    model: str = "deepseek-flash"
    api_key: str = ""
    base_url: str = ""                  # переопределение, иначе дефолт провайдера
    temperature: float = 1.1

@dataclass
class Moment:
    start: float                        # секунды
    end: float                          # секунды
    hook: str = ""
    title: str = ""
    hashtags: list[str] = field(default_factory=list)
    reason: str = ""
    score: int = 0                      # 0-100, оценка «залетит / не залетит», LLM

class LLMError(RuntimeError): ...

def available_providers() -> list[dict]:
    """[{id, label, models: [{id, label, free}], needs_key, key_url, note}]"""
    ...

def resolve(provider: str, model: str = "") -> tuple[str, str, str]:
    """provider, model, base_url по умолчанию для провайдера."""
    ...

def ping(cfg: LLMConfig, timeout: float = 20.0) -> tuple[bool, str]:
    """(ok, сообщение). Делает минимальный запрос к провайдеру."""

def pick_moments(
    segments: list[tuple[float, float, str]],
    *,
    n: int,
    min_dur: float,
    max_dur: float,
    duration: float,
    cfg: LLMConfig,
    log: Callable[[str], None] = print,
) -> list[Moment]:
    """Отдаёт до n моментов, отсортированных по времени.
    Бросает LLMError с человеческим текстом, если провайдер недоступен.
    Не должен сам рендерить/трогать файлы."""
```

Требования:
- OpenAI-совместимый формат для deepseek/openrouter/groq/ollama; Gemini — свой REST.
- JSON-режим (`response_format={"type":"json_object"}`) там, где поддерживается; для Gemini — `responseMimeType`.
- Если провайдер вернул мусор — одна повторная попытка с упрощённым промптом, потом `LLMError`.
- Таймаут 180 с, HTTP-ошибки → `LLMError` с кодом и первыми 300 символами тела.
- Никаких секретов в логи.

## app/core/youtube.py — скачивание с YouTube

```python
@dataclass
class DownloadResult:
    path: Path
    title: str
    duration: float
    uploader: str
    thumbnail: Path | None = None
    source_id: str = ""

def probe_url(url: str) -> dict: ...
    # {title, duration, uploader, thumbnail_url, source_id, is_live, height}

def download(
    url: str,
    outdir: Path,
    *,
    max_height: int = 1080,
    on_progress: Callable[[dict], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> DownloadResult: ...
```

Требования:
- `on_progress` получает `{"percent": float, "downloaded": int, "total": int, "speed": str, "eta": str}`.
- `cancel()` возвращает True → прервать скачивание (yt-dlp hook), файл не оставлять битым.
- Выбор формата: лучший mp4 ≤ max_height с аудио; если нет — лучший доступный.
- Имя файла: `<source_id>_<slug>.mp4` в outdir.
- Ошибки yt-dlp → `RuntimeError` с понятным текстом (возраст, приватное, гео-блок).

## app/core/upload/base.py — контракт загрузки

```python
@dataclass
class BrowserTarget:
    engine: str = "plain"        # plain | dolphin | adspower
    profile_id: str = ""         # id профиля антидетекта или имя локального профиля
    headless: bool = False
    proxy: str = ""              # необязательно, формат host:port@user:pass

@dataclass
class UploadRequest:
    video_path: Path
    title: str
    description: str
    hashtags: list[str]
    platform: str
    account_label: str = ""      # человекочитаемый ник/профиль
    dry_run: bool = False        # довести до кнопки «опубликовать», но не нажимать

@dataclass
class UploadResult:
    ok: bool
    platform: str
    url: str = ""
    message: str = ""
    screenshot: Path | None = None

class Uploader(Protocol):
    platform: str
    def upload(self, session, req: UploadRequest, log: Callable[[str], None]) -> UploadResult: ...
```

`session` — объект `BrowserSession` из `app/core/upload/browsers.py`
(поля: `.page`, `.context`, `.engine`, `.profile_id`).

## app/core/upload/browsers.py — движок браузера

```python
@dataclass
class BrowserSession:
    engine: str
    profile_id: str
    context: Any
    page: Any
    _playwright: Any = None
    _browser: Any = None
    _stop_url: str = ""

def list_profiles(engine: str, *, token: str = "") -> tuple[bool, list[dict], str]:
    """(сервис запущен, [{id, name, status}], текст ошибки)"""

def open_session(target: BrowserTarget, *, persistent_root: Path, log=print) -> BrowserSession: ...
def close_session(session: BrowserSession, log=print) -> None: ...
```

## app/jobs.py — очередь задач

```python
class JobManager:
    def submit(self, kind: str, fn: Callable[[Job], None], *, meta: dict | None = None) -> Job
    def get(self, job_id: str) -> Job | None
    def list(self) -> list[dict]
    def cancel(self, job_id: str) -> bool

class Job:
    id: str
    kind: str               # analyze | youtube | download_model | upload
    status: str             # queued | running | done | error | cancelled
    progress: float         # 0..100
    stage: str
    message: str
    logs: list[str]
    result: dict | None
    error: str
    def log(self, text: str) -> None
    def set_progress(self, value: float, stage: str = "", message: str = "") -> None
```

## app/server.py — HTTP API

| Метод | Путь | Тело / ответ |
|---|---|---|
| GET | `/api/health` | `{ok, version, ffmpeg, webview2, chrome}` |
| GET | `/api/settings` | настройки без секретов (ключи маскируются) |
| PUT | `/api/settings` | частичное обновление |
| GET | `/api/providers` | `available_providers()` |
| POST | `/api/providers/test` | `{provider, model, api_key}` → `{ok, message}` |
| GET | `/api/models` | `{installed: [...], sizes: [...]}` |
| POST | `/api/models/download` | `{size}` → job |
| POST | `/api/analyze` | multipart `file` + поля `clips, style, min_dur, max_dur` → job |
| POST | `/api/youtube` | `{url, clips, style, min_dur, max_dur}` → job |
| GET | `/api/jobs` | список (без логов) |
| GET | `/api/jobs/{id}` | полный job |
| GET | `/api/jobs/{id}/events` | SSE: `progress`, `log`, `done` |
| POST | `/api/jobs/{id}/cancel` | `{ok}` |
| GET | `/api/clips?job_id=` | список клипов задачи |
| POST | `/api/upload` | `{clip_id, platforms[], browser{engine,profile_id,headless}, dry_run}` → job |
| GET | `/api/history` | последние задачи/загрузки |
| GET | `/api/browsers` | `?engine=dolphin|adspower` → профили |
| GET | `/media/{path}` | статика (mp4/png) |
| GET | `/` | `app/web/index.html` |
