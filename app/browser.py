"""CloakBrowser 页面抓取实现。"""

import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from urllib.parse import urlparse

from app.config import Settings
from app.models import BrowserCookie, FetchPageRequest, FetchPageResponse
from app.security import OutboundUrlGuard


@dataclass(frozen=True)
class _FetchResult:
    """浏览器执行后的内部结果。"""

    status: int | None
    final_url: str
    html: str
    page_title: str | None
    cookies: list[BrowserCookie]


class CloakBrowserFetcher:
    """用 CloakBrowser 以请求级隔离上下文抓取页面。"""

    def __init__(self, settings: Settings):
        """初始化浏览器参数和统一出站地址保护。"""
        self._settings = settings
        self._url_guard = OutboundUrlGuard()

    def fetch(self, request: FetchPageRequest) -> FetchPageResponse:
        """打开受允许页面并返回受大小限制的渲染结果。"""
        started_at = time.monotonic()
        self._url_guard.ensure_allowed(str(request.url))
        timeout_seconds = min(
            request.timeout_seconds or self._settings.default_timeout_seconds,
            self._settings.max_timeout_seconds,
        )
        result = self._fetch_with_browser(request, timeout_seconds)
        html, truncated = self._truncate_html(result.html)
        return FetchPageResponse(
            request_id=request.request_id,
            status=result.status,
            final_url=result.final_url,
            html=html,
            html_truncated=truncated,
            cookies=result.cookies,
            page_title=result.page_title,
            challenge_detected=_is_challenge_page(html),
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )

    def _fetch_with_browser(self, request: FetchPageRequest, timeout_seconds: int) -> _FetchResult:
        """在独立上下文中启动浏览器、导航页面并提取同站 Cookie。"""
        from cloakbrowser import launch_context

        context = launch_context(
            headless=self._settings.headless,
            humanize=self._settings.humanize,
            human_preset=self._settings.human_preset,
        )
        try:
            self._install_request_guard(context)
            self._inject_cookie(context, str(request.url), request.cookie)
            if request.headers:
                context.set_extra_http_headers(request.headers)
            page = context.new_page()
            page.set_default_timeout(timeout_seconds * 1000)
            response = page.goto(str(request.url), wait_until=request.wait_until, timeout=timeout_seconds * 1000)
            return _FetchResult(
                status=response.status if response else None,
                final_url=page.url,
                html=page.content(),
                page_title=page.title(),
                cookies=self._same_site_cookies(context.cookies(), str(request.url)),
            )
        finally:
            context.close()

    def _install_request_guard(self, context: object) -> None:
        """拦截页面及其子资源，阻止它们访问内网或非 HTTP(S) 地址。"""
        def guard_route(route: object) -> None:
            request_url = route.request.url
            try:
                self._url_guard.ensure_allowed(request_url)
            except ValueError:
                route.abort()
                return
            route.continue_()

        context.route("**/*", guard_route)

    @staticmethod
    def _inject_cookie(context: object, url: str, cookie_header: str | None) -> None:
        """将 Elysium 持有的 Cookie 注入到本次隔离浏览器上下文。"""
        if not cookie_header:
            return
        parsed_url = urlparse(url)
        host = parsed_url.hostname
        if not host:
            return
        parsed_cookie = SimpleCookie()
        parsed_cookie.load(cookie_header)
        cookies = [
            {"name": name, "value": morsel.value, "domain": host, "path": "/"}
            for name, morsel in parsed_cookie.items()
        ]
        if cookies:
            context.add_cookies(cookies)

    def _same_site_cookies(self, cookies: list[dict], url: str) -> list[BrowserCookie]:
        """筛出目标站及父域 Cookie，避免回传第三方资源 Cookie。"""
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        result: list[BrowserCookie] = []
        for cookie in cookies:
            domain = str(cookie.get("domain") or "").lower().lstrip(".").rstrip(".")
            if not domain or not (host == domain or host.endswith(f".{domain}")):
                continue
            result.append(
                BrowserCookie(
                    name=str(cookie.get("name", "")),
                    value=str(cookie.get("value", "")),
                    domain=domain,
                    path=str(cookie.get("path") or "/"),
                    expires=_normalize_expiry(cookie.get("expires")),
                    http_only=bool(cookie.get("httpOnly", False)),
                    secure=bool(cookie.get("secure", False)),
                    same_site=cookie.get("sameSite"),
                )
            )
        return result

    def _truncate_html(self, html: str) -> tuple[str, bool]:
        """按 UTF-8 字节数限制响应体，避免大页面耗尽服务内存。"""
        encoded = html.encode("utf-8")
        if len(encoded) <= self._settings.max_html_bytes:
            return html, False
        return encoded[: self._settings.max_html_bytes].decode("utf-8", errors="ignore"), True


def _normalize_expiry(value: object) -> float | None:
    """将 Playwright 的会话 Cookie 过期值统一为 null。"""
    if value is None:
        return None
    numeric_value = float(value)
    return numeric_value if numeric_value > 0 else None


def _is_challenge_page(html: str) -> bool:
    """识别常见验证页面文本，仅用于上报而不执行任何验证操作。"""
    normalized = html.lower()
    markers = ("cf-chl-", "captcha", "verify you are human", "人机验证", "安全验证")
    return any(marker in normalized for marker in markers)
