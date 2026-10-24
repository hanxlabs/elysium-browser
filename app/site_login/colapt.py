"""可乐 ReelCore 的同源 Cookie 会话登录。"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from typing import Callable
from urllib.parse import urlparse

from app.browser_runtime import launch_isolated_context
from app.browser_shared import close_browser_context, cookie_header_for_host
from app.config import Settings
from app.models import SiteLoginCredential, SiteLoginRequest, SiteLoginResponse
from app.security import OutboundUrlGuard
from app.site_login.base import SiteLoginAdapter
from app.totp import generate_totp

logger = logging.getLogger("elysium.browser_gateway.site_login.colapt")
_HOSTS = {"www.colapt.com", "colapt.com"}
_TOKEN = re.compile(r"[0-9a-f]{64}")

_API_REQUEST = r"""
async ({ path, method, body, timeoutMs }) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const headers = { Accept: "application/json" };
    if (body !== null) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {
      method, credentials: "same-origin", cache: "no-store", redirect: "error",
      headers, signal: controller.signal,
      ...(body === null ? {} : { body: JSON.stringify(body) })
    });
    return {
      status: response.status, body: (await response.text()).slice(0, 65536),
      userAgent: navigator.userAgent, language: navigator.language
    };
  } finally {
    clearTimeout(timer);
  }
}
"""


class ColaptLoginAdapter(SiteLoginAdapter):
    """通过 ReelCore API 登录、按需验证 TOTP，再确认当前会话。"""

    def __init__(
        self,
        settings: Settings,
        url_guard: OutboundUrlGuard,
        context_factory: Callable[..., object] | None = None,
    ):
        self._settings = settings
        self._url_guard = url_guard
        self._context_factory = context_factory

    def supports(self, site_key: str) -> bool:
        return site_key.strip().lower() == "colapt"

    def login(self, request: SiteLoginRequest) -> SiteLoginResponse:
        started_at = time.monotonic()
        username = request.credentials.get("username", "").strip()
        password = request.credentials.get("password", "")
        if not username or not password:
            return self._failure(request, started_at, "可乐自动登录需要账号和密码")

        origin = self._build_origin(str(request.site_url))
        login_url = f"{origin}/login"
        self._url_guard.ensure_allowed(login_url)
        timeout = min(
            request.timeout_seconds or self._settings.default_timeout_seconds,
            self._settings.max_timeout_seconds,
        )
        context = None
        try:
            context = launch_isolated_context(self._settings, self._context_factory)
            self._install_request_guard(context)
            page = context.new_page()
            page.set_default_timeout(timeout * 1000)
            page.goto(login_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            # The bare domain can redirect to www before the API call.
            origin = self._build_origin(page.url)
            status, payload, _ = self._request(
                page, "/api/v1/sessions", "POST",
                {"username": username, "password": password}, timeout,
            )
            if status == 202:
                challenge_status = payload.get("status")
                if challenge_status == "enrollment_required":
                    return self._failure(request, started_at, "可乐需要先在站点完成两步验证设置")
                if challenge_status == "recovery_required":
                    return self._failure(request, started_at, "可乐需要先在站点完成账号恢复")
                if challenge_status != "two_factor_required":
                    return self._failure(request, started_at, "可乐登录返回无法识别的验证状态")
                secret = request.credentials.get("twoFactorSecret", "").strip()
                if not secret:
                    return self._failure(request, started_at, "可乐登录需要配置两步验证密钥")
                methods = payload.get("methods")
                if isinstance(methods, list) and methods and "totp" not in methods:
                    return self._failure(request, started_at, "可乐需要先在站点完成账号恢复")
                challenge = payload.get("challenge_token")
                if not self._is_token(challenge):
                    return self._failure(request, started_at, "可乐登录未返回有效的两步验证挑战")
                try:
                    code = generate_totp(secret, minimum_validity_seconds=5)
                except ValueError:
                    return self._failure(request, started_at, "可乐2FA密钥格式无效")
                status, payload, _ = self._request(
                    page, "/api/v1/sessions/verify", "POST",
                    {"challenge_token": challenge, "code": code}, timeout,
                )

            if status != 201 or payload.get("status") != "authenticated":
                return self._failure(request, started_at, self._error_message(status, payload))
            if payload.get("password_change_required") is True:
                return self._failure(request, started_at, "可乐需要先在站点修改密码")
            if not self._is_token(payload.get("csrf_token")):
                return self._failure(request, started_at, "可乐登录未返回有效的 CSRF Token")

            status, me, metadata = self._request(page, "/api/v1/me", "GET", None, timeout)
            if status != 200:
                return self._failure(request, started_at, self._error_message(status, me))
            session = me.get("session")
            csrf_token = session.get("csrf_token") if isinstance(session, dict) else None
            user_id = me.get("id")
            if (
                type(user_id) is not int or user_id < 1
                or not isinstance(me.get("username"), str) or not me["username"].strip()
                or unicodedata.normalize("NFKC", me["username"]).casefold()
                != unicodedata.normalize("NFKC", username).casefold()
                or me.get("status") != "active" or not self._is_token(csrf_token)
            ):
                return self._failure(request, started_at, "可乐登录后未能确认有效会话")
            if me.get("password_change_required") is True:
                return self._failure(request, started_at, "可乐需要先在站点修改密码")
            host = urlparse(origin).hostname
            cookies = context.cookies(f"{origin}/api/v1/me")
            cookie = cookie_header_for_host(cookies, host)
            session_cookie = cookie_header_for_host([
                item for item in cookies
                if item.get("name") == "reelcore_session" and item.get("value")
            ], host)
            if not session_cookie:
                return self._failure(request, started_at, "可乐登录成功但未返回 Cookie")
            language = str(metadata.get("language") or "zh-CN").strip()
            return SiteLoginResponse(
                request_id=request.request_id,
                site_key=request.site_key,
                success=True,
                message="可乐自动登录成功",
                credential=SiteLoginCredential(
                    cookie=cookie,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": str(metadata.get("userAgent") or "").strip(),
                        "Accept-Language": f"{language},zh-CN;q=0.9,zh;q=0.8,en;q=0.7",
                        "Referer": f"{origin}/",
                        "Origin": origin,
                        "X-CSRF-Token": csrf_token,
                    },
                ),
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
        except Exception as error:
            # Exceptions and API messages may contain submitted credentials.
            logger.error(
                "可乐Browser自动登录异常: request_id=%s error_type=%s",
                request.request_id, type(error).__name__,
            )
            return self._failure(request, started_at, "可乐自动登录请求异常")
        finally:
            if context is not None:
                close_browser_context(context, logger, "可乐", request.request_id)

    def _request(self, page, path, method, body, timeout) -> tuple[int, dict, dict]:
        origin = self._build_origin(page.url)
        self._url_guard.ensure_allowed(f"{origin}{path}")
        result = page.evaluate(_API_REQUEST, {
            "path": path, "method": method, "body": body, "timeoutMs": timeout * 1000,
        })
        if not isinstance(result, dict):
            return 0, {}, {}
        try:
            status = int(result.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        try:
            payload = json.loads(result.get("body")) if isinstance(result.get("body"), str) else {}
        except json.JSONDecodeError:
            payload = {}
        return status, payload if isinstance(payload, dict) else {}, result

    def _install_request_guard(self, context: object) -> None:
        def guard(route: object) -> None:
            url = str(route.request.url)
            if urlparse(url).scheme in {"data", "blob"}:
                route.continue_()
                return
            try:
                self._build_origin(url)
                self._url_guard.ensure_allowed(url)
            except ValueError:
                route.abort()
                return
            route.continue_()

        context.route("**/*", guard)

    @staticmethod
    def _build_origin(url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https" or host not in _HOSTS
            or parsed.port not in {None, 443}
            or parsed.username is not None or parsed.password is not None
        ):
            raise ValueError("可乐站点地址无效")
        return f"https://{host}"

    @staticmethod
    def _is_token(value: object) -> bool:
        return isinstance(value, str) and _TOKEN.fullmatch(value) is not None

    @staticmethod
    def _error_message(status: int, payload: dict) -> str:
        error = payload.get("error")
        error = error if isinstance(error, dict) else {}
        code = error.get("code")
        fields = error.get("fields")
        if (isinstance(code, str) and code.startswith("captcha_")) or (
            isinstance(fields, dict) and "captcha_token" in fields
        ):
            return "可乐要求人机验证，请先在站点完成验证后重试"
        messages = {
            "invalid_credentials": "可乐账号、密码或两步验证码不正确",
            "invalid_totp": "可乐两步验证码不正确，请检查密钥和系统时间",
            "password_change_required": "可乐需要先在站点修改密码",
            "two_factor_required": "可乐登录需要配置两步验证密钥",
            "two_factor_mandatory": "可乐需要先在站点完成两步验证设置",
        }
        if isinstance(code, str) and code in messages:
            return messages[code]
        if status == 429:
            return "可乐登录请求过于频繁，请稍后重试"
        # Return only a bounded machine code, never server messages or field values.
        reason = code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", code) else "响应无效"
        return f"可乐自动登录失败: {reason}（HTTP {status}）"

    @staticmethod
    def _failure(request: SiteLoginRequest, started_at: float, message: str) -> SiteLoginResponse:
        return SiteLoginResponse(
            request_id=request.request_id, site_key=request.site_key,
            success=False, message=message,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )
