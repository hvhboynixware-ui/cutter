# -*- coding: utf-8 -*-
"""Аккаунты с подпиской (ChatGPT Plus/Pro и Google Gemini) вместо API-ключей.

Поддерживает гибридную авторизацию:
  - OpenAI ChatGPT OAuth PKCE (токен подписки Plus/Pro/Team + авто-обновление)
  - Встроенный браузер Playwright (persistent-профили для chatgpt.com и gemini.google.com)
  - Импорт OAuth-учётки Gemini (если есть локальный token/client)

Все секреты (OAuth-токены, куки) лежат только в DATA_DIR/accounts.json и
DATA_DIR/account_profiles; наружу через API отдаются только маскированные статусы.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import shutil
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from . import settings as S

OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_AUTH_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_REDIRECT_PORT = 1455
OPENAI_REDIRECT_URI = f"http://127.0.0.1:{OPENAI_REDIRECT_PORT}/auth/callback"
OPENAI_CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
OPENAI_OAUTH_SCOPE = "openid profile email offline_access"

CHATGPT_WEB_URL = "https://chatgpt.com/"
GEMINI_WEB_URL = "https://gemini.google.com/app"

LOGIN_TIMEOUT_SEC = 300.0
QUERY_TIMEOUT_SEC = 180.0

_LOCK = threading.RLock()


class AccountError(RuntimeError):
    """Человекочитаемая ошибка авторизации или запроса через аккаунт."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _accounts_path() -> Path:
    return S.DATA_DIR / "accounts.json"


def _profiles_dir() -> Path:
    path = S.DATA_DIR / "account_profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_store() -> list[dict]:
    path = _accounts_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, dict) and isinstance(data.get("accounts"), list):
        return [dict(item) for item in data["accounts"] if isinstance(item, dict)]
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, dict)]
    return []


def _write_store(items: list[dict]) -> None:
    S.ensure_dirs()
    path = _accounts_path()
    payload = {"version": 1, "accounts": items}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _mask_account(item: dict) -> dict:
    tokens = item.get("tokens") or {}
    return {
        "id": str(item.get("id") or ""),
        "provider": str(item.get("provider") or ""),
        "auth_method": str(item.get("auth_method") or "browser"),
        "label": str(item.get("label") or item.get("email") or item.get("id") or ""),
        "email": str(item.get("email") or ""),
        "plan": str(item.get("plan") or ""),
        "profile_dir": str(item.get("profile_dir") or ""),
        "status": str(item.get("status") or "ready"),
        "has_token": bool(tokens.get("access_token") or tokens.get("refresh_token")),
        "expires_at": int(tokens.get("expires_at") or 0),
        "last_used": str(item.get("last_used") or ""),
        "created_at": str(item.get("created_at") or ""),
        "error": str(item.get("error") or ""),
    }


def list_accounts(provider: str | None = None, *, mask: bool = True) -> list[dict]:
    with _LOCK:
        items = _read_store()
    if provider:
        wanted = provider.strip().lower()
        items = [item for item in items if str(item.get("provider") or "").lower() == wanted]
    return [_mask_account(item) if mask else item for item in items]


def get_account(account_id: str, *, mask: bool = False) -> dict | None:
    target = (account_id or "").strip()
    if not target:
        return None
    with _LOCK:
        for item in _read_store():
            if str(item.get("id") or "") == target:
                return _mask_account(item) if mask else item
    return None


def active_account(provider: str, preferred_id: str = "") -> dict | None:
    """Возвращает выбранный или первый готовый аккаунт для провайдера."""
    wanted = (provider or "").strip().lower()
    if wanted in {"chatgpt_sub", "chatgpt"}:
        wanted = "chatgpt"
    elif wanted in {"google_sub", "google", "gemini_sub"}:
        wanted = "google"

    items = list_accounts(wanted, mask=False)
    if not items:
        return None
    if preferred_id:
        for item in items:
            if item.get("id") == preferred_id:
                return item
    settings = S.load()
    saved_id = str((settings.get("llm") or {}).get("account_id") or "")
    if saved_id:
        for item in items:
            if item.get("id") == saved_id:
                return item
    ready = [item for item in items if item.get("status") == "ready"]
    return ready[0] if ready else items[0]


def upsert_account(account: dict) -> dict:
    with _LOCK:
        items = _read_store()
        acc_id = str(account.get("id") or "")
        replaced = False
        for index, existing in enumerate(items):
            if existing.get("id") == acc_id:
                items[index] = account
                replaced = True
                break
        if not replaced:
            items.append(account)
        _write_store(items)
    return _mask_account(account)


def remove_account(account_id: str) -> bool:
    target = (account_id or "").strip()
    if not target:
        return False
    removed_profile = ""
    with _LOCK:
        items = _read_store()
        kept: list[dict] = []
        for item in items:
            if item.get("id") == target:
                removed_profile = str(item.get("profile_dir") or "")
                continue
            kept.append(item)
        if len(kept) == len(items):
            return False
        _write_store(kept)

    if removed_profile:
        profile_path = _profiles_dir() / Path(removed_profile).name
        if profile_path.is_dir():
            shutil.rmtree(profile_path, ignore_errors=True)
    return True


def _mark_status(account_id: str, status: str, error: str = "") -> None:
    with _LOCK:
        items = _read_store()
        for item in items:
            if item.get("id") == account_id:
                item["status"] = status
                item["error"] = error
                if status == "ready":
                    item["last_used"] = _now_iso()
                _write_store(items)
                return


# ---------------------------------------------------------------- JWT / PKCE утилиты

def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _decode_jwt_claims(token: str) -> dict:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_openai_claims(id_token: str, access_token: str) -> tuple[str, str, str]:
    """(email, account_id, plan) из JWT OpenAI."""
    claims = _decode_jwt_claims(id_token) or _decode_jwt_claims(access_token)
    email = str(claims.get("email") or "").strip()
    auth = claims.get("https://api.openai.com/auth") or {}
    if not isinstance(auth, dict):
        auth = {}
    acc_id = str(auth.get("chatgpt_account_id") or "").strip()
    plan = str(auth.get("chatgpt_plan_type") or "").strip()
    return email, acc_id, plan


# ---------------------------------------------------------------- OAuth PKCE (ChatGPT Plus/Pro)

class _OAuthCallbackServer:
    """Одноразовый локальный callback-сервер на 127.0.0.1:1455."""

    def __init__(self, expected_state: str, port: int = OPENAI_REDIRECT_PORT) -> None:
        self.expected_state = expected_state
        self.port = port
        self.code: str = ""
        self.error: str = ""
        self.done = threading.Event()
        self._server: http.server.HTTPServer | None = None

    def __enter__(self) -> "_OAuthCallbackServer":
        parent = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path != "/auth/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                query = urllib.parse.parse_qs(parsed.query)
                state = (query.get("state") or [""])[0]
                code = (query.get("code") or [""])[0]
                err = (query.get("error") or [""])[0]
                if err:
                    parent.error = (query.get("error_description") or [err])[0]
                    html = f"<h2>Ошибка входа</h2><p>{parent.error}</p><p>Можно закрыть окно.</p>"
                elif state != parent.expected_state or not code:
                    parent.error = "неверный state или отсутствует code"
                    html = "<h2>Ошибка проверки входа</h2><p>Можно закрыть окно.</p>"
                else:
                    parent.code = code
                    html = "<h2>Вход выполнен</h2><p>Cutter получил доступ. Это окно можно закрыть.</p>"
                body = f"<!doctype html><meta charset='utf-8'><body style='font-family:sans-serif;background:#111;color:#eee;padding:32px'>{html}</body>".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                parent.done.set()

        class _ReuseHTTPServer(http.server.HTTPServer):
            allow_reuse_address = True

        self._server = _ReuseHTTPServer(("127.0.0.1", self.port), Handler)
        self._server.timeout = 0.5
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def _serve(self) -> None:
        while self._server is not None and not self.done.is_set():
            try:
                self._server.handle_request()
            except Exception:
                break

    def __exit__(self, *exc_info: Any) -> bool:
        self.done.set()
        if self._server is not None:
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        return False


def _network_proxies() -> tuple[str, dict | None]:
    try:
        proxy = str((S.load().get("network") or {}).get("proxy") or "").strip()
    except Exception:
        proxy = ""
    if not proxy:
        return "", None
    return proxy, {"http": proxy, "https": proxy}


def start_oauth_login(
    provider: str,
    *,
    label: str = "",
    account_id: str = "",
    log: Callable[[str], None] = print,
    cancel: Callable[[], bool] | None = None,
) -> dict:
    """Запускает OAuth PKCE для ChatGPT Plus/Pro и сохраняет аккаунт."""
    prov = (provider or "").strip().lower()
    if prov != "chatgpt":
        raise AccountError("OAuth без API-ключа поддерживается для ChatGPT; для Google используй вход через браузер")

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": OPENAI_CLIENT_ID,
        "redirect_uri": OPENAI_REDIRECT_URI,
        "scope": OPENAI_OAUTH_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "state": state,
        "originator": "codex_cli_rs",
    }
    auth_url = f"{OPENAI_AUTH_URL}?{urllib.parse.urlencode(params)}"

    log("ChatGPT OAuth: открываю окно входа")
    log(f"ссылка авторизации: {auth_url}")
    try:
        server_ctx = _OAuthCallbackServer(state)
    except OSError as exc:
        raise AccountError(f"порт {OPENAI_REDIRECT_PORT} занят другим процессом ({exc})") from exc

    with server_ctx as cb:
        import webbrowser
        webbrowser.open(auth_url)
        deadline = time.time() + LOGIN_TIMEOUT_SEC
        while time.time() < deadline:
            if cancel and cancel():
                raise AccountError("вход отменён")
            if cb.done.wait(timeout=0.4):
                break
        if cb.error:
            raise AccountError(f"ChatGPT OAuth: {cb.error}")
        if not cb.code:
            raise AccountError("ChatGPT OAuth: не дождался завершения входа за 5 минут")
        code = cb.code

    log("ChatGPT OAuth: обмениваю code на токен")
    _, proxies = _network_proxies()
    try:
        resp = requests.post(
            OPENAI_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": OPENAI_REDIRECT_URI,
                "client_id": OPENAI_CLIENT_ID,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            proxies=proxies,
            timeout=30.0,
        )
    except requests.RequestException as exc:
        raise AccountError(f"ChatGPT OAuth: ошибка сети при обмене токена — {exc}") from exc

    if resp.status_code >= 400:
        raise AccountError(f"ChatGPT OAuth: отказ сервера ({resp.status_code}): {resp.text[:250]}")

    payload = resp.json()
    access_token = str(payload.get("access_token") or "")
    refresh_token = str(payload.get("refresh_token") or "")
    expires_in = int(payload.get("expires_in") or 3600)
    id_token = str(payload.get("id_token") or "")
    if not access_token:
        raise AccountError("ChatGPT OAuth: сервер не выдал access_token")

    email, chatgpt_acc_id, plan = _extract_openai_claims(id_token, access_token)
    acc_id = account_id or f"acc_{uuid.uuid4().hex[:10]}"
    display = (label or "").strip() or email or f"ChatGPT {plan or 'Sub'} ({acc_id[-4:]})"
    record = {
        "id": acc_id,
        "provider": "chatgpt",
        "auth_method": "oauth",
        "label": display,
        "email": email,
        "plan": plan,
        "profile_dir": "",
        "tokens": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": int(time.time()) + expires_in,
            "account_id": chatgpt_acc_id,
        },
        "status": "ready",
        "last_used": _now_iso(),
        "created_at": _now_iso(),
        "error": "",
    }
    saved = upsert_account(record)
    log(f"ChatGPT OAuth: аккаунт сохранён ({display})")
    return saved


def refresh_oauth_token(account: dict, *, log: Callable[[str], None] = print) -> dict:
    tokens = dict(account.get("tokens") or {})
    expires_at = int(tokens.get("expires_at") or 0)
    if tokens.get("access_token") and expires_at - 90 > time.time():
        return account

    refresh_token = str(tokens.get("refresh_token") or "")
    if not refresh_token:
        _mark_status(account["id"], "expired", "нужен повторный вход")
        raise AccountError("Срок действия токена ChatGPT истёк — нажми «Войти заново»")

    log("ChatGPT OAuth: обновляю access token")
    try:
        resp = requests.post(
            OPENAI_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OPENAI_CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
    except requests.RequestException as exc:
        raise AccountError(f"ChatGPT OAuth: ошибка сети при обновлении токена — {exc}") from exc

    if resp.status_code >= 400:
        _mark_status(account["id"], "expired", f"HTTP {resp.status_code}")
        raise AccountError(f"ChatGPT OAuth: не удалось обновить сессию ({resp.status_code}) — войди заново")

    payload = resp.json()
    tokens["access_token"] = str(payload.get("access_token") or tokens.get("access_token") or "")
    if payload.get("refresh_token"):
        tokens["refresh_token"] = str(payload["refresh_token"])
    tokens["expires_at"] = int(time.time()) + int(payload.get("expires_in") or 3600)
    account["tokens"] = tokens
    account["status"] = "ready"
    account["error"] = ""
    account["last_used"] = _now_iso()
    upsert_account(account)
    return account


# ---------------------------------------------------------------- Встроенный браузер (Playwright)

def _detect_chatgpt_session(session) -> tuple[bool, str, str]:
    """(logged_in, email, plan)."""
    try:
        cookies = session.context.cookies("https://chatgpt.com")
    except Exception:
        cookies = []
    has_cookie = any(
        "session-token" in (c.get("name") or "") or c.get("name") == "oai-did"
        for c in cookies
    )
    if not has_cookie:
        return False, "", ""
    try:
        info = session.page.evaluate(
            """async () => {
              try {
                const r = await fetch('/backend-api/me', {credentials: 'include'});
                if (!r.ok) return {ok: false, status: r.status};
                const d = await r.json();
                return {ok: true, email: d.email || d.name || '', plan: (d.plan_type || '')};
              } catch (e) { return {ok: false}; }
            }"""
        )
        if isinstance(info, dict) and info.get("ok"):
            return True, str(info.get("email") or ""), str(info.get("plan") or "")
    except Exception:
        pass
    has_auth = any("session-token" in (c.get("name") or "") for c in cookies)
    return has_auth, "", ""


def _detect_google_session(session) -> tuple[bool, str]:
    """(logged_in, email)."""
    try:
        url = str(getattr(session.page, "url", "") or "")
    except Exception:
        url = ""
    if "accounts.google.com" in url:
        return False, ""
    try:
        cookies = session.context.cookies("https://gemini.google.com")
    except Exception:
        cookies = []
    names = {c.get("name") for c in cookies}
    if "__Secure-1PSID" not in names and "SID" not in names:
        return False, ""
    email = ""
    try:
        email = session.page.evaluate(
            """() => {
              const w = window.WIZ_global_data || {};
              if (w.oPEP7c) return String(w.oPEP7c);
              const btn = document.querySelector('a[aria-label*="@"], button[aria-label*="@"]');
              if (btn) {
                const m = (btn.getAttribute('aria-label') || '').match(/[\\w.+-]+@[\\w.-]+/);
                if (m) return m[0];
              }
              return '';
            }"""
        ) or ""
    except Exception:
        email = ""
    return True, str(email).strip()


def start_browser_login(
    provider: str,
    *,
    label: str = "",
    account_id: str = "",
    log: Callable[[str], None] = print,
    cancel: Callable[[], bool] | None = None,
) -> dict:
    """Открывает окно встроенного браузера и ждёт входа в Google или ChatGPT."""
    from .upload import browsers
    from .upload.base import BrowserTarget

    prov = (provider or "").strip().lower()
    if prov not in {"chatgpt", "google"}:
        raise AccountError(f"Неизвестный провайдер аккаунта: {provider}")

    acc_id = account_id or f"acc_{uuid.uuid4().hex[:10]}"
    existing = get_account(acc_id)
    slug = (existing or {}).get("profile_dir") or f"{prov}_{acc_id}"
    url = CHATGPT_WEB_URL if prov == "chatgpt" else GEMINI_WEB_URL

    proxy_str, _ = _network_proxies()
    log(f"{prov}: открываю браузер для входа ({slug})")
    session = browsers.open_session(
        BrowserTarget(engine="plain", profile_id=slug, headless=False, proxy=proxy_str),
        persistent_root=_profiles_dir(),
        log=log,
    )
    try:
        session.page.goto(url, wait_until="domcontentloaded")
        deadline = time.time() + LOGIN_TIMEOUT_SEC
        logged_in = False
        email = ""
        plan = ""

        # Даём пользователю завершить авторизацию, 2FA или проверку браузера самостоятельно.
        while time.time() < deadline:
            if cancel and cancel():
                raise AccountError("вход отменён")
            try:
                if session.page.is_closed():
                    raise AccountError("окно браузера закрыто до завершения входа")
            except AccountError:
                raise
            except Exception:
                raise AccountError("окно браузера закрыто")
            if prov == "chatgpt":
                logged_in, email, plan = _detect_chatgpt_session(session)
            else:
                logged_in, email = _detect_google_session(session)
            if logged_in:
                # Короткая пауза, чтобы профиль успел сбросить куки/IndexedDB на диск.
                time.sleep(1.5)
                break
            time.sleep(1.0)

        if not logged_in:
            raise AccountError(f"{prov}: не дождался входа за {int(LOGIN_TIMEOUT_SEC)} с")

        default_name = "ChatGPT" if prov == "chatgpt" else "Google Gemini"
        display = (label or "").strip() or email or (existing or {}).get("label") or f"{default_name} ({acc_id[-4:]})"
        record = {
            "id": acc_id,
            "provider": prov,
            "auth_method": "browser",
            "label": display,
            "email": email,
            "plan": plan,
            "profile_dir": slug,
            "tokens": (existing or {}).get("tokens") or {},
            "status": "ready",
            "last_used": _now_iso(),
            "created_at": (existing or {}).get("created_at") or _now_iso(),
            "error": "",
        }
        saved = upsert_account(record)
        log(f"{prov}: вход выполнен ({display})")
        return saved
    finally:
        browsers.close_session(session, log)


# ---------------------------------------------------------------- Запросы к модели через аккаунт

def query_account(
    account: dict,
    prompt: str,
    *,
    system: str = "",
    model: str = "",
    timeout: float = QUERY_TIMEOUT_SEC,
    log: Callable[[str], None] = print,
) -> str:
    """Отправляет промпт через выбранный аккаунт и возвращает текст ответа."""
    prov = str(account.get("provider") or "").lower()
    method = str(account.get("auth_method") or "browser").lower()
    full_prompt = f"{system.strip()}\n\n{prompt}" if system.strip() else prompt

    if prov == "chatgpt" and method == "oauth":
        return _query_chatgpt_oauth(account, prompt, system=system, model=model, timeout=timeout, log=log)
    if prov == "chatgpt":
        return _query_chatgpt_browser(account, full_prompt, model=model, timeout=timeout, log=log)
    if prov == "google":
        return _query_google_browser(account, full_prompt, timeout=timeout, log=log)
    raise AccountError(f"Неподдерживаемый тип аккаунта: {prov}/{method}")


def _query_chatgpt_oauth(
    account: dict,
    prompt: str,
    *,
    system: str = "",
    model: str = "",
    timeout: float = QUERY_TIMEOUT_SEC,
    log: Callable[[str], None] = print,
) -> str:
    account = refresh_oauth_token(account, log=log)
    tokens = account.get("tokens") or {}
    access_token = str(tokens.get("access_token") or "")
    if not access_token:
        raise AccountError("ChatGPT OAuth: нет access_token — войди заново")

    chosen_model = (model or "").strip() or "gpt-5.1-codex-mini"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "originator": "codex_cli_rs",
    }
    if tokens.get("account_id"):
        headers["ChatGPT-Account-ID"] = str(tokens["account_id"])

    payload: dict[str, Any] = {
        "model": chosen_model,
        "instructions": system.strip() or "Отвечай строго по делу.",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "store": False,
        "stream": True,
    }
    try:
        resp = requests.post(
            OPENAI_CODEX_RESPONSES_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
            stream=True,
        )
    except requests.RequestException as exc:
        raise AccountError(f"ChatGPT подписка: ошибка сети — {exc}") from exc

    if resp.status_code == 401:
        _mark_status(account["id"], "expired", "401")
        raise AccountError("ChatGPT подписка: сессия отклонена (401) — войди заново")
    if resp.status_code >= 400:
        body = (resp.text or "")[:300]
        raise AccountError(f"ChatGPT подписка: ошибка {resp.status_code} — {body}")

    chunks: list[str] = []
    final_text = ""
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "application/json" in ctype:
        data = resp.json()
        final_text = _extract_responses_text(data)
    else:
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                evt = json.loads(data_str)
            except ValueError:
                continue
            etype = evt.get("type") or ""
            if etype == "response.output_text.delta":
                delta = evt.get("delta")
                if isinstance(delta, str):
                    chunks.append(delta)
            elif etype == "response.completed":
                extracted = _extract_responses_text(evt.get("response") or {})
                if extracted:
                    final_text = extracted

    text = (final_text or "".join(chunks)).strip()
    if not text:
        raise AccountError("ChatGPT подписка: пустой ответ модели")
    _mark_status(account["id"], "ready", "")
    return text


def _extract_responses_text(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"].strip()
    chunks: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "".join(chunks).strip()


def _query_chatgpt_browser(
    account: dict,
    prompt: str,
    *,
    model: str = "",
    timeout: float = QUERY_TIMEOUT_SEC,
    log: Callable[[str], None] = print,
) -> str:
    from .upload import browsers
    from .upload.base import BrowserTarget

    slug = str(account.get("profile_dir") or "")
    if not slug:
        raise AccountError("ChatGPT браузер: нет профиля — войди заново")

    headless = S.env_flag("CUTTER_ACCOUNT_HEADLESS")
    session = browsers.open_session(
        BrowserTarget(engine="plain", profile_id=slug, headless=headless),
        persistent_root=_profiles_dir(),
        log=log,
    )
    try:
        page = session.page
        page.goto(CHATGPT_WEB_URL, wait_until="domcontentloaded")
        ok, _, _ = _detect_chatgpt_session(session)
        if not ok:
            _mark_status(account["id"], "needs_login", "сессия истекла")
            raise AccountError("ChatGPT: сессия не активна — нажми «Войти заново» в Настройках")

        box = browsers.wait_for_any(
            page,
            ["#prompt-textarea", "div[contenteditable='true'][id='prompt-textarea']", "textarea[name='prompt-textarea']"],
            timeout=25_000,
        )
        if box is None:
            raise AccountError("ChatGPT: не нашёл поле ввода (возможно, страница просит проверку в окне)")

        before_count = page.locator("[data-message-author-role='assistant']").count()
        box.click()
        # Вставляем одним действием, чтобы длинный транскрипт не набирался по букве.
        page.evaluate(
            """([text]) => {
              const el = document.querySelector('#prompt-textarea') || document.activeElement;
              if (!el) return;
              if (el.tagName === 'TEXTAREA') {
                el.value = text;
                el.dispatchEvent(new Event('input', {bubbles: true}));
              } else {
                el.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText', false, text);
              }
            }""",
            [prompt],
        )
        time.sleep(0.3)
        send_btn = browsers.wait_for_any(
            page,
            ["button[data-testid='send-button']", "button[aria-label*='Send']", "button[aria-label*='Отправить']"],
            timeout=4_000,
        )
        if send_btn is not None:
            send_btn.click()
        else:
            box.press("Enter")

        deadline = time.time() + timeout
        last_text = ""
        stable_ticks = 0
        while time.time() < deadline:
            count = page.locator("[data-message-author-role='assistant']").count()
            if count > before_count:
                text = page.locator("[data-message-author-role='assistant']").nth(count - 1).inner_text().strip()
                stop_btn = page.locator("button[data-testid='stop-button']").count()
                if text and stop_btn == 0:
                    if text == last_text:
                        stable_ticks += 1
                        if stable_ticks >= 2:
                            _mark_status(account["id"], "ready", "")
                            return text
                    else:
                        stable_ticks = 0
                last_text = text
            time.sleep(0.7)

        if last_text:
            _mark_status(account["id"], "ready", "")
            return last_text
        raise AccountError("ChatGPT браузер: модель не успела ответить")
    finally:
        browsers.close_session(session, log)


def _query_google_browser(
    account: dict,
    prompt: str,
    *,
    timeout: float = QUERY_TIMEOUT_SEC,
    log: Callable[[str], None] = print,
) -> str:
    from .upload import browsers
    from .upload.base import BrowserTarget

    slug = str(account.get("profile_dir") or "")
    if not slug:
        raise AccountError("Google Gemini: нет профиля — войди заново")

    headless = S.env_flag("CUTTER_ACCOUNT_HEADLESS")
    session = browsers.open_session(
        BrowserTarget(engine="plain", profile_id=slug, headless=headless),
        persistent_root=_profiles_dir(),
        log=log,
    )
    try:
        page = session.page
        page.goto(GEMINI_WEB_URL, wait_until="domcontentloaded")
        ok, _ = _detect_google_session(session)
        if not ok:
            _mark_status(account["id"], "needs_login", "сессия истекла")
            raise AccountError("Google Gemini: сессия не активна — нажми «Войти заново» в Настройках")

        box = browsers.wait_for_any(
            page,
            [
                "rich-textarea div[contenteditable='true']",
                "div.ql-editor[contenteditable='true']",
                "div[role='textbox'][contenteditable='true']",
                "textarea",
            ],
            timeout=25_000,
        )
        if box is None:
            raise AccountError("Google Gemini: не нашёл поле ввода на gemini.google.com")

        resp_sel = "model-response message-content, .model-response-text, message-content"
        before_count = page.locator(resp_sel).count()

        box.click()
        page.evaluate(
            """([text]) => {
              const el = document.querySelector("rich-textarea div[contenteditable='true'], div.ql-editor[contenteditable='true'], div[role='textbox']") || document.activeElement;
              if (!el) return;
              if (el.tagName === 'TEXTAREA') {
                el.value = text;
                el.dispatchEvent(new Event('input', {bubbles: true}));
              } else {
                el.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText', false, text);
              }
            }""",
            [prompt],
        )
        time.sleep(0.35)
        send_btn = browsers.wait_for_any(
            page,
            ["button.send-button", "button[aria-label*='Send']", "button[aria-label*='Отправить']"],
            timeout=4_000,
        )
        if send_btn is not None:
            send_btn.click()
        else:
            box.press("Enter")

        deadline = time.time() + timeout
        last_text = ""
        stable_ticks = 0
        while time.time() < deadline:
            count = page.locator(resp_sel).count()
            if count > before_count:
                text = page.locator(resp_sel).nth(count - 1).inner_text().strip()
                stop_vis = page.locator("button[aria-label*='Stop'], button[aria-label*='Остановить']").count()
                if text and stop_vis == 0:
                    if text == last_text:
                        stable_ticks += 1
                        if stable_ticks >= 2:
                            _mark_status(account["id"], "ready", "")
                            return text
                    else:
                        stable_ticks = 0
                last_text = text
            time.sleep(0.75)

        if last_text:
            _mark_status(account["id"], "ready", "")
            return last_text
        raise AccountError("Google Gemini: модель не успела ответить")
    finally:
        browsers.close_session(session, log)


def test_account(account_id: str, *, model: str = "") -> tuple[bool, str]:
    account = get_account(account_id)
    if not account:
        return False, "Аккаунт не найден"
    try:
        reply = query_account(account, "Reply with exactly one word: ok", model=model, timeout=45.0)
        return True, f"{account['label']}: связь есть ({reply[:40]})"
    except Exception as exc:
        return False, str(exc)[:240]


def export_youtube_cookies_txt(account_id: str = "") -> Path | None:
    """Экспортирует куки Google-профиля в Netscape cookies.txt для yt-dlp."""
    account = active_account("google", account_id)
    if not account or not account.get("profile_dir"):
        return None
    slug = str(account["profile_dir"])
    profile_path = _profiles_dir() / Path(slug).name
    if not profile_path.is_dir():
        return None

    try:
        from .upload import browsers
        from .upload.base import BrowserTarget
        session = browsers.open_session(
            BrowserTarget(engine="plain", profile_id=slug, headless=True),
            persistent_root=_profiles_dir(),
            log=lambda _: None,
        )
    except Exception:
        return None

    try:
        cookies = session.context.cookies("https://www.youtube.com", "https://accounts.google.com")
        if not cookies:
            return None
        lines = ["# Netscape HTTP Cookie File", ""]
        for c in cookies:
            domain = str(c.get("domain") or "")
            if not domain:
                continue
            include_sub = "TRUE" if domain.startswith(".") else "FALSE"
            path = str(c.get("path") or "/")
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = int(c.get("expires") or 0)
            if expires < 0:
                expires = 0
            name = str(c.get("name") or "")
            value = str(c.get("value") or "")
            if name:
                lines.append(f"{domain}\t{include_sub}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
        out = S.DATA_DIR / "youtube_cookies.txt"
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out
    except Exception:
        return None
    finally:
        try:
            browsers.close_session(session, lambda _: None)
        except Exception:
            pass
