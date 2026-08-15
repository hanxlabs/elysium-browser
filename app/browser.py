"""CloakBrowser 页面抓取实现。"""

import logging
import time
from dataclasses import dataclass

from app.browser_runtime import launch_isolated_context
from app.browser_shared import (
    close_browser_context,
    filter_same_site_cookies,
    inject_cookie_from_header,
    install_outbound_request_guard,
    is_challenge_page,
    truncate_html_bytes,
)
from app.config import Settings
from app.models import (
    BrowserCookie,
    FetchPageRequest,
    FetchPageResponse,
    FetchResourceRequest,
    FetchResourceResponse,
)
from app.security import OutboundUrlGuard

logger = logging.getLogger("elysium.browser_gateway.fetcher")


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
        html, truncated = truncate_html_bytes(result.html, self._settings.max_html_bytes)
        return FetchPageResponse(
            request_id=request.request_id,
            status=result.status,
            final_url=result.final_url,
            html=html,
            html_truncated=truncated,
            cookies=result.cookies,
            page_title=result.page_title,
            challenge_detected=is_challenge_page(html),
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )

    def fetch_resource(self, request: FetchResourceRequest) -> FetchResourceResponse:
        """在 Browser 上下文中请求站点页面或 API，并回传更新后的同站 Cookie。"""
        started_at = time.monotonic()
        self._url_guard.ensure_allowed(str(request.url))
        timeout_seconds = min(
            request.timeout_seconds or self._settings.default_timeout_seconds,
            self._settings.max_timeout_seconds,
        )
        result = self._fetch_resource_with_browser(request, timeout_seconds)
        body, truncated = truncate_html_bytes(result.html, self._settings.max_html_bytes)
        return FetchResourceResponse(
            request_id=request.request_id,
            status=result.status,
            final_url=result.final_url,
            body=body,
            body_truncated=truncated,
            cookies=result.cookies,
            challenge_detected=is_challenge_page(body),
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )

    def _fetch_with_browser(self, request: FetchPageRequest, timeout_seconds: int) -> _FetchResult:
        """在独立上下文中启动浏览器、导航页面并提取同站 Cookie。"""
        context = launch_isolated_context(self._settings)
        try:
            install_outbound_request_guard(context, self._url_guard)
            inject_cookie_from_header(context, str(request.url), request.cookie)
            if request.headers:
                context.set_extra_http_headers(request.headers)
            page = context.new_page()
            page.set_default_timeout(timeout_seconds * 1000)
            response = page.goto(str(request.url), wait_until=request.wait_until, timeout=timeout_seconds * 1000)
            if request.settle_seconds:
                # Challenge pages may finish their initial navigation before replacing
                # themselves with the requested page. Keep this wait bounded and opt-in.
                page.wait_for_timeout(request.settle_seconds * 1000)
            return _FetchResult(
                status=response.status if response else None,
                final_url=page.url,
                html=page.content(),
                page_title=page.title(),
                cookies=filter_same_site_cookies(context.cookies(), str(request.url)),
            )
        finally:
            close_browser_context(
                context,
                logger,
                "通用抓取",
                request.request_id,
                suppress_errors=False,
            )

    def _fetch_resource_with_browser(
        self,
        request: FetchResourceRequest,
        timeout_seconds: int,
    ) -> _FetchResult:
        """页面请求使用 Chromium 渲染，API 请求使用关联请求上下文共享 Cookie。"""
        context = launch_isolated_context(self._settings)
        try:
            install_outbound_request_guard(context, self._url_guard)
            inject_cookie_from_header(context, str(request.url), request.cookie)
            if request.render_page:
                if request.method != "GET":
                    raise ValueError("渲染页面模式只支持 GET")
                if request.headers:
                    context.set_extra_http_headers(request.headers)
                page = context.new_page()
                page.set_default_timeout(timeout_seconds * 1000)
                response = page.goto(
                    str(request.url),
                    wait_until=request.wait_until,
                    timeout=timeout_seconds * 1000,
                )
                if request.settle_seconds:
                    page.wait_for_timeout(request.settle_seconds * 1000)
                return _FetchResult(
                    status=response.status if response else None,
                    final_url=page.url,
                    html=page.content(),
                    page_title=page.title(),
                    cookies=filter_same_site_cookies(context.cookies(), str(request.url)),
                )

            response = context.request.fetch(
                str(request.url),
                method=request.method,
                headers=request.headers or None,
                data=request.body if request.method == "POST" else None,
                timeout=timeout_seconds * 1000,
                max_redirects=0,
            )
            return _FetchResult(
                status=response.status,
                final_url=response.url,
                html=response.text(),
                page_title=None,
                cookies=filter_same_site_cookies(context.cookies(), str(request.url)),
            )
        finally:
            close_browser_context(
                context,
                logger,
                "站点数据请求",
                request.request_id,
                suppress_errors=False,
            )
