"""朱雀同源 JSON API 登录适配器。"""

from __future__ import annotations

import json
import logging
import time
from typing import Callable
from urllib.parse import urlparse

from app.browser_runtime import launch_isolated_context
from app.browser_shared import close_browser_context, cookie_header_for_host
from app.config import Settings
from app.models import SiteLoginCredential, SiteLoginRequest, SiteLoginResponse
from app.security import OutboundUrlGuard
from app.site_login.base import SiteLoginAdapter
from app.totp import generate_totp

logger = logging.getLogger("elysium.browser_gateway.site_login.zhuque")

_ZHUQUE_LOGIN_SCRIPT = r"""
async ({ apiPath, username, password, code }) => {
  const readCsrfToken = () => {
    const meta = document.querySelector(
      'meta[name="x-csrf-token"], meta[property="x-csrf-token"], meta[name="csrf-token"], meta[name="csrf_token"]'
    )?.content?.trim() || "";
    if (meta) return meta;
    const htmlToken = document.documentElement.innerHTML.match(
      /(?:csrfToken|csrf_token)\s*[:=]\s*["']([^"']+)/i
    )?.[1] || "";
    if (htmlToken) return htmlToken;
    const cookie = document.cookie.split(";").map(item => item.trim())
      .find(item => /^(?:xsrf|csrf)[-_]?token=/i.test(item));
    const value = cookie?.slice(cookie.indexOf("=") + 1) || "";
    try { return decodeURIComponent(value); } catch { return value; }
  };
  const csrfToken = readCsrfToken();
  if (!csrfToken) {
    return { status: 0, body: "", csrfToken: "", userAgent: navigator.userAgent, language: navigator.language };
  }
  const response = await fetch(apiPath, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: {
      "Accept": "application/json, text/plain, */*",
      "Content-Type": "application/json",
      "x-csrf-token": csrfToken
    },
    body: JSON.stringify({ username, password, _2fa: code })
  });
  return {
    status: response.status,
    body: (await response.text()).slice(0, 16384),
    csrfToken: response.headers.get("x-csrf-token")?.trim() || readCsrfToken() || csrfToken,
    userAgent: navigator.userAgent,
    language: navigator.language
  };
}
"""


class ZhuqueLoginAdapter(SiteLoginAdapter):
    """在朱雀登录页内提交同源 API，并返回会话 Cookie 与 CSRF Token。"""

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
        return site_key.strip().lower() == "zhuque"

    def login(self, request: SiteLoginRequest) -> SiteLoginResponse:
        started_at = time.monotonic()
        username = request.credentials.get("username", "").strip()
        password = request.credentials.get("password", "")
        two_factor_secret = request.credentials.get("twoFactorSecret", "").strip()
        if not username or not password:
            return self._failure(request, started_at, "朱雀自动登录需要账号和密码")

        site_origin = self._build_origin(str(request.site_url))
        login_url = f"{site_origin}/entry/login"
        api_url = f"{site_origin}/api/user/login"
        self._url_guard.ensure_allowed(login_url)
        self._url_guard.ensure_allowed(api_url)
        timeout_seconds = min(
            request.timeout_seconds or self._settings.default_timeout_seconds,
            self._settings.max_timeout_seconds,
        )
        context = self._launch_context()
        try:
            self._install_request_guard(context)
            page = context.new_page()
            page.set_default_timeout(timeout_seconds * 1000)
            page.goto(login_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            self._ensure_allowed_url(page.url)
            code = (
                generate_totp(two_factor_secret, minimum_validity_seconds=8)
                if two_factor_secret
                else request.credentials.get("code", "").strip()
            )
            result = page.evaluate(
                _ZHUQUE_LOGIN_SCRIPT,
                {
                    "apiPath": "/api/user/login",
                    "username": username,
                    "password": password,
                    "code": code,
                },
            )
            status, payload = self._parse_result(result)
            csrf_token = str(result.get("csrfToken") or "").strip() if isinstance(result, dict) else ""
            if not csrf_token:
                return self._failure(request, started_at, "朱雀登录页未提供 CSRF Token")
            if not self._is_success(status, payload):
                reason = str(
                    payload.get("message") or payload.get("msg") or payload.get("code") or f"HTTP {status}"
                )[:200]
                return self._failure(request, started_at, f"朱雀自动登录失败: {reason}")

            cookie = cookie_header_for_host(context.cookies(site_origin), "zhuque.in")
            if not cookie:
                return self._failure(request, started_at, "朱雀登录成功但未返回 Cookie")
            user_agent = str(result.get("userAgent") or "").strip()
            language = str(result.get("language") or "zh-CN").strip()
            return SiteLoginResponse(
                request_id=request.request_id,
                site_key=request.site_key,
                success=True,
                message="朱雀自动登录成功",
                credential=SiteLoginCredential(
                    cookie=cookie,
                    headers={
                        "User-Agent": user_agent,
                        "Accept": "application/json, text/plain, */*",
                        "Accept-Language": f"{language},zh-CN;q=0.9,zh;q=0.8,en;q=0.7",
                        "Referer": f"{site_origin}/",
                        "x-csrf-token": csrf_token,
                    },
                ),
                duration_ms=self._duration_ms(started_at),
            )
        except ValueError as error:
            if "2FA密钥格式无效" in str(error):
                return self._failure(request, started_at, "朱雀2FA密钥格式无效")
            raise
        except Exception as error:
            logger.exception(
                "朱雀Browser自动登录异常: request_id=%s error_type=%s",
                request.request_id,
                type(error).__name__,
            )
            return self._failure(request, started_at, "朱雀自动登录请求异常")
        finally:
            two_factor_secret = ""
            close_browser_context(context, logger, "朱雀", request.request_id)

    def _launch_context(self) -> object:
        return launch_isolated_context(self._settings, self._context_factory)

    def _install_request_guard(self, context: object) -> None:
        def guard_route(route: object) -> None:
            request_url = str(route.request.url)
            parsed = urlparse(request_url)
            if parsed.scheme in {"data", "blob"}:
                route.continue_()
                return
            try:
                self._ensure_allowed_url(request_url)
                self._url_guard.ensure_allowed(request_url)
            except ValueError:
                route.abort()
                return
            route.continue_()

        context.route("**/*", guard_route)

    @staticmethod
    def _build_origin(site_url: str) -> str:
        parsed = urlparse(site_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or host != "zhuque.in"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("朱雀站点地址无效")
        return "https://zhuque.in"

    @staticmethod
    def _ensure_allowed_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower().rstrip(".") != "zhuque.in" or parsed.port not in {None, 443}:
            raise ValueError("朱雀登录导航离开允许域名")

    @staticmethod
    def _parse_result(result: object) -> tuple[int, dict]:
        if not isinstance(result, dict):
            return 0, {}
        try:
            status = int(result.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        try:
            payload = json.loads(result.get("body")) if isinstance(result.get("body"), str) else {}
        except json.JSONDecodeError:
            payload = {}
        return status, payload if isinstance(payload, dict) else {}

    @staticmethod
    def _is_success(status: int, payload: dict) -> bool:
        return 200 <= status < 300 and payload.get("status") == 200 and payload.get("code") == "LOGIN_SUCCESS"

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        return int((time.monotonic() - started_at) * 1000)

    def _failure(self, request: SiteLoginRequest, started_at: float, message: str) -> SiteLoginResponse:
        return SiteLoginResponse(
            request_id=request.request_id,
            site_key=request.site_key,
            success=False,
            message=message,
            duration_ms=self._duration_ms(started_at),
        )
