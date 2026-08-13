"""Shared browser utilities — request guard, cookie injection, HTML truncation.

These are used by both CloakBrowserFetcher and site login adapters to avoid
duplicating the same browser plumbing across multiple call sites.
"""

from __future__ import annotations

import json
import logging
from http.cookies import SimpleCookie
from urllib.parse import urlparse

from app.models import BrowserCookie
from app.security import OutboundUrlGuard

logger = logging.getLogger("elysium.browser_gateway.shared")


def install_outbound_request_guard(context: object, url_guard: OutboundUrlGuard) -> None:
    """拦截页面及其子资源，阻止它们访问内网或非 HTTP(S) 地址。"""

    def guard_route(route: object) -> None:
        request_url = str(route.request.url)
        try:
            url_guard.ensure_allowed(request_url)
        except ValueError:
            route.abort()
            return
        route.continue_()

    context.route("**/*", guard_route)


def inject_cookie_from_header(context: object, url: str, cookie_header: str | None) -> None:
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
        {
            "name": name,
            "value": morsel.value,
            "domain": host,
            "path": morsel.get("path") or "/",
        }
        for name, morsel in parsed_cookie.items()
    ]
    if cookies:
        context.add_cookies(cookies)


def filter_same_site_cookies(cookies: list[dict], url: str) -> list[BrowserCookie]:
    """筛出目标站及父域 Cookie，避免回传第三方资源 Cookie。"""
    host = _normalize_host(urlparse(url).hostname)
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


def truncate_html_bytes(html: str, max_bytes: int) -> tuple[str, bool]:
    """按 UTF-8 字节数限制响应体，避免大页面耗尽服务内存。"""
    encoded = html.encode("utf-8")
    if len(encoded) <= max_bytes:
        return html, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def is_challenge_page(html: str) -> bool:
    """识别常见验证页面文本，仅用于上报而不执行任何验证操作。"""
    normalized = html.lower()
    markers = ("cf-chl-", "captcha", "verify you are human", "人机验证", "安全验证")
    return any(marker in normalized for marker in markers)


def cookie_header_from_context(context: object, site_origin: str) -> str:
    """从浏览器上下文中提取站点 Cookie 并拼接为 HTTP Cookie 头。"""
    host = _normalize_host(urlparse(site_origin).hostname)
    return cookie_header_for_host(context.cookies(site_origin), host)


def cookie_header_for_host(cookies: list[dict], host: str) -> str:
    """只拼接目标主机可见的 Cookie，保持登录适配器原有筛选语义。"""
    normalized_host = _normalize_host(host)
    values: list[str] = []
    for cookie in cookies:
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        domain = _normalize_host(str(cookie.get("domain") or "").lstrip("."))
        if not name or not domain or not (
            normalized_host == domain or normalized_host.endswith(f".{domain}")
        ):
            continue
        parsed = SimpleCookie()
        parsed[name] = value
        values.append(f"{name}={parsed[name].value}")
    return "; ".join(values)


def close_browser_context(
    context: object,
    log: logging.Logger,
    label: str,
    request_id: str,
    *,
    suppress_errors: bool = True,
) -> None:
    """统一关闭请求级上下文；调用方可保留原先的异常传播方式。"""
    try:
        context.close()
    except Exception:
        log.exception("%s Browser上下文关闭失败: request_id=%s", label, request_id)
        if not suppress_errors:
            raise


def sanitize_diagnostic_json(payload: object) -> str:
    """将诊断数据序列化为安全的 JSON 字符串。"""
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        return str(payload)


def _normalize_host(hostname: str | None) -> str:
    if hostname is None:
        return ""
    return hostname.lower().rstrip(".")


def _normalize_expiry(value: object) -> float | None:
    """将 Playwright 的会话 Cookie 过期值统一为 null。"""
    if value is None:
        return None
    try:
        numeric_value = float(value)
        return numeric_value if numeric_value > 0 else None
    except (TypeError, ValueError):
        return None
