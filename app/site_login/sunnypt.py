"""SunnyPT 登录适配器。"""

import json
import time
from typing import Callable
from urllib.parse import urlparse

from app.config import Settings
from app.models import SiteLoginCredential, SiteLoginRequest, SiteLoginResponse
from app.security import OutboundUrlGuard
from app.site_login.base import SiteLoginAdapter

_SUNNY_PT_LOGIN_SCRIPT = """
async ({ apiUrl, username, password, code }) => {
  const response = await fetch(apiUrl, {
    method: "POST",
    credentials: "include",
    headers: { "Accept": "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, code })
  });
  return { status: response.status, body: await response.text() };
}
"""

_SUNNY_PT_CREATE_SESSION_SCRIPT = """
async ({ accessToken }) => {
  const response = await fetch("/api/auth/session", {
    method: "POST",
    credentials: "include",
    headers: { "Accept": "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ accessToken, rememberMe: true })
  });
  return { status: response.status, body: await response.text() };
}
"""

_SUNNY_PT_READ_SESSION_SCRIPT = """
async () => {
  const response = await fetch("/api/auth/session", {
    method: "GET",
    credentials: "include",
    headers: { "Accept": "application/json" }
  });
  return { status: response.status, body: await response.text() };
}
"""


class SunnyPtLoginAdapter(SiteLoginAdapter):
    """通过 SunnyPT 登录页的真实浏览器上下文刷新 Bearer Token。"""

    def __init__(
        self,
        settings: Settings,
        url_guard: OutboundUrlGuard,
        context_factory: Callable[..., object] | None = None,
    ):
        """初始化浏览器配置、出站保护和可替换的上下文工厂。"""
        self._settings = settings
        self._url_guard = url_guard
        self._context_factory = context_factory

    def supports(self, site_key: str) -> bool:
        """匹配 SunnyPT 站点键。"""
        return site_key.strip().lower() == "sunnypt"

    def login(self, request: SiteLoginRequest) -> SiteLoginResponse:
        """依次执行账号登录、创建站点会话和读取新 Token，整个流程只尝试一次。"""
        started_at = time.monotonic()
        username = request.credentials.get("username", "").strip()
        password = request.credentials.get("password", "")
        code = request.credentials.get("code", "").strip()
        if not username or not password:
            return self._failure(request, started_at, "SunnyPT 自动登录需要账号和密码")

        site_origin = self._build_site_origin(str(request.site_url))
        login_page_url = f"{site_origin}/auth/sign-in"
        api_login_url = self._build_api_login_url(site_origin)
        self._url_guard.ensure_allowed(login_page_url)
        self._url_guard.ensure_allowed(api_login_url)
        timeout_seconds = min(
            request.timeout_seconds or self._settings.default_timeout_seconds,
            self._settings.max_timeout_seconds,
        )

        context = self._launch_context()
        try:
            self._install_request_guard(context)
            page = context.new_page()
            page.set_default_timeout(timeout_seconds * 1000)
            page.goto(login_page_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)

            login_result = page.evaluate(
                _SUNNY_PT_LOGIN_SCRIPT,
                {"apiUrl": api_login_url, "username": username, "password": password, "code": code},
            )
            access_token, error = self._extract_login_token(login_result)
            if not access_token:
                return self._failure(request, started_at, error or "SunnyPT 登录失败")

            create_session_result = page.evaluate(
                _SUNNY_PT_CREATE_SESSION_SCRIPT,
                {"accessToken": access_token},
            )
            if not self._session_created(create_session_result):
                return self._failure(request, started_at, "SunnyPT 登录成功，但创建站点会话失败")

            session_result = page.evaluate(_SUNNY_PT_READ_SESSION_SCRIPT)
            session_token = self._extract_session_token(session_result)
            if not session_token:
                return self._failure(request, started_at, "SunnyPT 站点会话未返回 Bearer Token")
            return SiteLoginResponse(
                request_id=request.request_id,
                site_key=request.site_key,
                success=True,
                message="SunnyPT 自动登录成功",
                credential=SiteLoginCredential(bearer_token=session_token),
                duration_ms=self._duration_ms(started_at),
            )
        except Exception:
            return self._failure(request, started_at, "SunnyPT 自动登录请求异常")
        finally:
            context.close()

    def _launch_context(self) -> object:
        """创建仅供本次登录使用的隔离 CloakBrowser 上下文。"""
        if self._context_factory is not None:
            return self._context_factory(
                headless=self._settings.headless,
                humanize=self._settings.humanize,
                human_preset=self._settings.human_preset,
            )
        from cloakbrowser import launch_context

        return launch_context(
            headless=self._settings.headless,
            humanize=self._settings.humanize,
            human_preset=self._settings.human_preset,
        )

    def _install_request_guard(self, context: object) -> None:
        """保护登录页、接口和子资源均不能访问内网地址。"""
        def guard_route(route: object) -> None:
            try:
                self._url_guard.ensure_allowed(route.request.url)
            except ValueError:
                route.abort()
                return
            route.continue_()

        context.route("**/*", guard_route)

    @staticmethod
    def _build_site_origin(site_url: str) -> str:
        """把配置中的站点地址规范化为 SunnyPT 页面源。"""
        parsed = urlparse(site_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not (
            host == "sunnypt.top" or host.endswith(".sunnypt.top")
        ):
            raise ValueError("SunnyPT 站点地址无效")
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    @staticmethod
    def _build_api_login_url(site_origin: str) -> str:
        """根据页面域名构造 SunnyPT 登录 API 地址。"""
        parsed = urlparse(site_origin)
        host = parsed.hostname or "sunnypt.top"
        api_host = host if host.startswith("api.") else f"api.{host}"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{api_host}{port}/login"

    @staticmethod
    def _extract_login_token(result: object) -> tuple[str | None, str | None]:
        """校验登录接口响应并提取第一阶段 Token。"""
        status, payload = SunnyPtLoginAdapter._parse_browser_result(result)
        if status < 200 or status >= 300 or payload.get("code") != 0:
            return None, str(payload.get("msg") or "SunnyPT 用户名或密码错误")
        data = payload.get("data")
        token = data.get("token") if isinstance(data, dict) else None
        return (str(token).strip(), None) if token else (None, "SunnyPT 登录响应未返回 Token")

    @staticmethod
    def _session_created(result: object) -> bool:
        """校验前端会话创建接口响应。"""
        status, payload = SunnyPtLoginAdapter._parse_browser_result(result)
        return 200 <= status < 300 and payload.get("data") is True

    @staticmethod
    def _extract_session_token(result: object) -> str | None:
        """从前端会话读取接口提取最终 Bearer Token。"""
        status, payload = SunnyPtLoginAdapter._parse_browser_result(result)
        if status < 200 or status >= 300:
            return None
        data = payload.get("data")
        token = data.get("accessToken") if isinstance(data, dict) else None
        return str(token).strip() if token else None

    @staticmethod
    def _parse_browser_result(result: object) -> tuple[int, dict]:
        """解析页面脚本返回的 HTTP 状态和 JSON 正文。"""
        if not isinstance(result, dict):
            return 0, {}
        status = int(result.get("status") or 0)
        body = result.get("body")
        try:
            payload = json.loads(body) if isinstance(body, str) else {}
        except json.JSONDecodeError:
            payload = {}
        return status, payload if isinstance(payload, dict) else {}

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        """计算登录适配器耗时。"""
        return int((time.monotonic() - started_at) * 1000)

    def _failure(self, request: SiteLoginRequest, started_at: float, message: str) -> SiteLoginResponse:
        """构造不包含账号、密码或 Token 的失败响应。"""
        return SiteLoginResponse(
            request_id=request.request_id,
            site_key=request.site_key,
            success=False,
            message=message,
            duration_ms=self._duration_ms(started_at),
        )
