"""BTSCHOOL 图片验证码登录适配器。"""

from __future__ import annotations

import json
import logging
import re
import time
from http.cookies import SimpleCookie
from typing import Callable, Protocol
from urllib.parse import urljoin, urlparse

from app.captcha import LocalCaptchaOcr
from app.config import Settings
from app.models import SiteLoginCredential, SiteLoginRequest, SiteLoginResponse
from app.security import OutboundUrlGuard
from app.site_login.base import SiteLoginAdapter

_CAPTCHA_PATTERN = re.compile(r"^[A-Za-z0-9]{3,8}$")
_CAPTCHA_ERROR_PATTERN = re.compile(r"图片代码无效|图片代码已被清除")
_CREDENTIAL_ERROR_PATTERN = re.compile(r"用户名或密码不正确|还没有通过验证")
_LOGOUT_LINK_PATTERN = re.compile(r"""href\s*=\s*["'][^"']*logout\.php(?:[^"']*)["']""", re.I)
_CF_MARKERS = (
    "cf-chl",
    "challenge-platform",
    "cf-turnstile",
    "just a moment",
    "attention required",
    "checking your browser",
    "cloudflare ray id",
)

logger = logging.getLogger("elysium.browser_gateway.site_login.btschool")


class CaptchaRecognizer(Protocol):
    """适配器所需的最小 OCR 协议。"""

    def recognize(self, image_bytes: bytes) -> str:
        """识别验证码图片。"""


class BtschoolLoginAdapter(SiteLoginAdapter):
    """在同一 CloakBrowser 上下文中完成 BTSCHOOL 登录。"""

    _HOST = "pt.btschool.club"
    _FORM_SELECTOR = 'form[action$="takelogin.php"][method="post"]'
    _USERNAME_SELECTOR = 'input[name="username"]'
    _PASSWORD_SELECTOR = 'input[name="password"]'
    _CAPTCHA_IMAGE_SELECTOR = 'img[alt="CAPTCHA"], img[src*="action=regimage"]'
    _CAPTCHA_INPUT_SELECTOR = 'input[name="imagestring"]'
    _SUBMIT_SELECTOR = 'input[type="submit"][value="登录"]'
    _MAX_ATTEMPTS = 2
    _MAX_DIAGNOSTIC_HTML_CHARS = 512 * 1024

    def __init__(
        self,
        settings: Settings,
        url_guard: OutboundUrlGuard,
        recognizer: CaptchaRecognizer | None = None,
        context_factory: Callable[..., object] | None = None,
    ):
        self._settings = settings
        self._url_guard = url_guard
        self._recognizer = recognizer or LocalCaptchaOcr()
        self._context_factory = context_factory

    def supports(self, site_key: str) -> bool:
        return site_key.strip().lower() == "btschool"

    def login(self, request: SiteLoginRequest) -> SiteLoginResponse:
        started_at = time.monotonic()
        username = request.credentials.get("username", "").strip()
        password = request.credentials.get("password", "")
        if not username or not password:
            return self._failure(request, started_at, "BTSCHOOL 自动登录需要账号和密码")

        site_origin = self._build_site_origin(str(request.site_url))
        login_url = f"{site_origin}/login.php"
        self._url_guard.ensure_allowed(login_url)
        timeout_seconds = min(
            request.timeout_seconds or self._settings.default_timeout_seconds,
            self._settings.max_timeout_seconds,
        )

        context = None
        page = None
        navigation_response = None
        stage = "launch-context"
        blocked_requests: list[str] = []
        try:
            context = self._launch_context()
            stage = "install-request-guard"
            self._install_request_guard(context, blocked_requests)
            stage = "open-page"
            page = context.new_page()
            self._install_page_diagnostics(page, request.request_id, username, password)
            page.set_default_timeout(timeout_seconds * 1000)
            last_message = "BTSCHOOL 验证码识别失败"
            for attempt in range(self._MAX_ATTEMPTS):
                stage = "navigate-login-page"
                navigation_response = page.goto(
                    login_url,
                    wait_until="domcontentloaded",
                    timeout=timeout_seconds * 1000,
                )
                self._ensure_same_origin(page.url, site_origin)
                stage = "inspect-login-page"
                login_html = page.content()
                if self._classify_result(page.url, login_html) == "success":
                    logger.info(
                        "BTSCHOOL Browser访问登录页时已处于登录状态: request_id=%s url=%s",
                        request.request_id,
                        page.url,
                    )
                    return self._success_response(
                        request,
                        started_at,
                        page,
                        context,
                        site_origin,
                        username,
                        password,
                        blocked_requests,
                        navigation_response,
                        attempt + 1,
                    )
                form = page.locator(self._FORM_SELECTOR)
                if form.count() != 1:
                    self._log_page_diagnostic(
                        request_id=request.request_id,
                        stage="login-form-missing",
                        attempt=attempt + 1,
                        page=page,
                        response=navigation_response,
                        username=username,
                        password=password,
                        blocked_requests=blocked_requests,
                    )
                    return self._failure(request, started_at, "BTSCHOOL 登录页未找到登录表单")
                self._ensure_form_action(form.get_attribute("action"), login_url, site_origin)

                stage = "wait-captcha-image"
                image = page.locator(self._CAPTCHA_IMAGE_SELECTOR).first
                image.wait_for(state="visible")
                try:
                    stage = "recognize-captcha"
                    code = self._recognizer.recognize(image.screenshot())
                except Exception as error:
                    last_message = "BTSCHOOL 本地OCR识别失败"
                    self._log_page_diagnostic(
                        request_id=request.request_id,
                        stage="captcha-ocr-error",
                        attempt=attempt + 1,
                        page=page,
                        response=navigation_response,
                        username=username,
                        password=password,
                        blocked_requests=blocked_requests,
                        error=error,
                    )
                    if attempt + 1 < self._MAX_ATTEMPTS:
                        continue
                    return self._failure(request, started_at, last_message)
                if not _CAPTCHA_PATTERN.fullmatch(code):
                    last_message = "BTSCHOOL 本地OCR结果格式无效"
                    self._log_page_diagnostic(
                        request_id=request.request_id,
                        stage="captcha-format-invalid",
                        attempt=attempt + 1,
                        page=page,
                        response=navigation_response,
                        username=username,
                        password=password,
                        blocked_requests=blocked_requests,
                    )
                    if attempt + 1 < self._MAX_ATTEMPTS:
                        continue
                    return self._failure(request, started_at, last_message)

                stage = "fill-login-form"
                page.locator(self._USERNAME_SELECTOR).fill(username)
                page.locator(self._PASSWORD_SELECTOR).fill(password)
                page.locator(self._CAPTCHA_INPUT_SELECTOR).fill(code)
                stage = "submit-login-form"
                with page.expect_navigation(
                    wait_until="domcontentloaded",
                    timeout=timeout_seconds * 1000,
                ) as navigation_info:
                    page.locator(self._SUBMIT_SELECTOR).click()
                navigation_response = navigation_info.value
                stage = "wait-login-result"
                page.wait_for_load_state("load", timeout=timeout_seconds * 1000)
                self._ensure_same_origin(page.url, site_origin)
                stage = "inspect-login-result"
                html = self._read_stable_page_content(page, timeout_seconds=5)
                outcome = self._classify_result(page.url, html)
                if outcome == "success":
                    return self._success_response(
                        request,
                        started_at,
                        page,
                        context,
                        site_origin,
                        username,
                        password,
                        blocked_requests,
                        None,
                        attempt + 1,
                    )
                if outcome == "credential_error":
                    self._log_page_diagnostic(
                        request_id=request.request_id,
                        stage="credential-error",
                        attempt=attempt + 1,
                        page=page,
                        response=None,
                        username=username,
                        password=password,
                        blocked_requests=blocked_requests,
                    )
                    return self._failure(
                        request,
                        started_at,
                        "BTSCHOOL 用户名或密码不正确，或者账号尚未通过验证",
                    )
                if outcome == "captcha_error":
                    last_message = "BTSCHOOL 图片验证码错误"
                    if attempt + 1 >= self._MAX_ATTEMPTS:
                        self._log_page_diagnostic(
                            request_id=request.request_id,
                            stage="captcha-error",
                            attempt=attempt + 1,
                            page=page,
                            response=None,
                            username=username,
                            password=password,
                            blocked_requests=blocked_requests,
                        )
                    if attempt + 1 < self._MAX_ATTEMPTS:
                        continue
                    return self._failure(request, started_at, last_message)
                self._log_page_diagnostic(
                    request_id=request.request_id,
                    stage="login-result-unknown",
                    attempt=attempt + 1,
                    page=page,
                    response=None,
                    username=username,
                    password=password,
                    blocked_requests=blocked_requests,
                )
                return self._failure(request, started_at, "BTSCHOOL 登录结果无法确认")
            return self._failure(request, started_at, last_message)
        except ValueError:
            raise
        except Exception as error:
            self._log_page_diagnostic(
                request_id=request.request_id,
                stage=stage,
                attempt=None,
                page=page,
                response=navigation_response,
                username=username,
                password=password,
                blocked_requests=blocked_requests,
                error=error,
            )
            logger.exception(
                "BTSCHOOL Browser自动登录异常: request_id=%s stage=%s error_type=%s",
                request.request_id,
                stage,
                type(error).__name__,
            )
            return self._failure(
                request,
                started_at,
                f"BTSCHOOL 自动登录请求异常（阶段：{stage}，类型：{type(error).__name__}）",
            )
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    logger.exception(
                        "BTSCHOOL Browser上下文关闭失败: request_id=%s",
                        request.request_id,
                    )

    @staticmethod
    def _read_stable_page_content(page: object, timeout_seconds: int) -> str:
        """导航切换期间短暂重试，避免 Playwright 在 frame 更新时读取 DOM。"""
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while True:
            try:
                return str(page.content() or "")
            except Exception as error:
                message = str(error).lower()
                if "page is navigating" not in message and "changing the content" not in message:
                    raise
                last_error = error
                if time.monotonic() >= deadline:
                    raise last_error
                time.sleep(0.1)

    def _success_response(
        self,
        request: SiteLoginRequest,
        started_at: float,
        page: object,
        context: object,
        site_origin: str,
        username: str,
        password: str,
        blocked_requests: list[str],
        response: object | None,
        attempt: int,
    ) -> SiteLoginResponse:
        user_agent = str(page.evaluate("() => navigator.userAgent") or "").strip()
        language = str(page.evaluate("() => navigator.language") or "zh-CN").strip()
        cookie = self._cookie_header(context.cookies(site_origin), self._HOST)
        if not cookie:
            self._log_page_diagnostic(
                request_id=request.request_id,
                stage="success-without-cookie",
                attempt=attempt,
                page=page,
                response=response,
                username=username,
                password=password,
                blocked_requests=blocked_requests,
            )
            return self._failure(request, started_at, "BTSCHOOL 登录成功但未返回 Cookie")
        return SiteLoginResponse(
            request_id=request.request_id,
            site_key=request.site_key,
            success=True,
            message="BTSCHOOL 自动登录成功",
            credential=SiteLoginCredential(
                cookie=cookie,
                headers={
                    "User-Agent": user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Accept-Language": f"{language},zh-CN;q=0.9,zh;q=0.8,en;q=0.7",
                    "Referer": f"{site_origin}/",
                    "Upgrade-Insecure-Requests": "1",
                },
            ),
            duration_ms=self._duration_ms(started_at),
        )

    def _install_page_diagnostics(
        self,
        page: object,
        request_id: str,
        username: str,
        password: str,
    ) -> None:
        def log_console(message: object) -> None:
            text = self._redact_text(str(getattr(message, "text", "")), username, password)
            logger.info(
                "BTSCHOOL 页面控制台: request_id=%s type=%s text=%s",
                request_id,
                getattr(message, "type", ""),
                text,
            )

        def log_page_error(error: object) -> None:
            logger.warning(
                "BTSCHOOL 页面脚本异常: request_id=%s error=%s",
                request_id,
                self._redact_text(str(error), username, password),
            )

        def log_request_failed(failed_request: object) -> None:
            logger.warning(
                "BTSCHOOL 页面请求失败: request_id=%s url=%s failure=%s",
                request_id,
                self._redact_text(str(getattr(failed_request, "url", "")), username, password),
                self._redact_text(str(getattr(failed_request, "failure", "")), username, password),
            )

        def log_response(response: object) -> None:
            try:
                response_request = response.request
                resource_type = str(getattr(response_request, "resource_type", ""))
                response_url = str(getattr(response, "url", ""))
                if resource_type != "document" and "action=regimage" not in response_url:
                    return
                raw_headers = dict(getattr(response, "headers", {}) or {})
                headers = {
                    str(name): self._redact_text(str(value), username, password)
                    for name, value in raw_headers.items()
                    if str(name).lower() not in {"set-cookie", "cookie", "authorization"}
                }
                logger.info(
                    "BTSCHOOL 页面关键响应: %s",
                    json.dumps(
                        {
                            "requestId": request_id,
                            "resourceType": resource_type,
                            "status": getattr(response, "status", None),
                            "url": self._redact_text(response_url, username, password),
                            "headers": headers,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                )
            except Exception:
                logger.exception(
                    "BTSCHOOL 页面响应诊断失败: request_id=%s",
                    request_id,
                )

        page.on("console", log_console)
        page.on("pageerror", log_page_error)
        page.on("requestfailed", log_request_failed)
        page.on("response", log_response)

    def _log_page_diagnostic(
        self,
        *,
        request_id: str,
        stage: str,
        attempt: int | None,
        page: object | None,
        response: object | None,
        username: str,
        password: str,
        blocked_requests: list[str],
        error: Exception | None = None,
    ) -> None:
        final_url = ""
        title = ""
        html = ""
        page_read_error = ""
        if page is not None:
            try:
                final_url = str(page.url or "")
            except Exception as read_error:
                page_read_error = f"url:{type(read_error).__name__}:{read_error}"
            try:
                title = str(page.title() or "")
            except Exception as read_error:
                page_read_error += f" title:{type(read_error).__name__}:{read_error}"
            try:
                html = self._read_stable_page_content(page, timeout_seconds=2)
            except Exception as read_error:
                page_read_error += f" html:{type(read_error).__name__}:{read_error}"

        sanitized_html = self._redact_text(html, username, password)
        html_truncated = len(sanitized_html) > self._MAX_DIAGNOSTIC_HTML_CHARS
        logged_html = sanitized_html[: self._MAX_DIAGNOSTIC_HTML_CHARS]
        lower_html = sanitized_html.lower()
        response_headers: dict[str, str] = {}
        response_status = None
        response_url = ""
        if response is not None:
            try:
                response_status = int(response.status)
            except Exception:
                response_status = None
            try:
                response_url = str(response.url or "")
            except Exception:
                response_url = ""
            try:
                raw_headers = dict(response.headers)
                response_headers = {
                    str(name): self._redact_text(str(value), username, password)
                    for name, value in raw_headers.items()
                    if str(name).lower() not in {"set-cookie", "cookie", "authorization"}
                }
            except Exception:
                response_headers = {}

        normalized_headers = {name.lower(): value for name, value in response_headers.items()}
        challenge_markers = [marker for marker in _CF_MARKERS if marker in lower_html]
        server_header = normalized_headers.get("server", "")
        cf_ray = normalized_headers.get("cf-ray", "")
        cf_mitigated = normalized_headers.get("cf-mitigated", "")
        diagnostic = {
            "requestId": request_id,
            "stage": stage,
            "attempt": attempt,
            "finalUrl": self._redact_text(final_url, username, password),
            "title": self._redact_text(title, username, password),
            "responseStatus": response_status,
            "responseUrl": self._redact_text(response_url, username, password),
            "responseHeaders": response_headers,
            "htmlLength": len(sanitized_html),
            "htmlTruncated": html_truncated,
            "pageReadError": self._redact_text(page_read_error, username, password),
            "cloudflare": {
                "detected": bool(
                    cf_ray
                    or cf_mitigated
                    or "cloudflare" in server_header.lower()
                    or challenge_markers
                ),
                "server": server_header,
                "ray": cf_ray,
                "mitigated": cf_mitigated,
                "challengeMarkers": challenge_markers,
            },
            "selectors": self._selector_counts(page),
            "blockedRequests": [
                self._redact_text(url, username, password)
                for url in blocked_requests[-50:]
            ],
            "errorType": type(error).__name__ if error is not None else "",
            "error": self._redact_text(str(error), username, password) if error is not None else "",
        }
        logger.error(
            "BTSCHOOL Browser页面诊断: %s",
            json.dumps(diagnostic, ensure_ascii=False, default=str),
        )
        logger.error(
            "BTSCHOOL Browser脱敏页面HTML: request_id=%s stage=%s\n%s",
            request_id,
            stage,
            logged_html or "[页面HTML不可用]",
        )

    def _selector_counts(self, page: object | None) -> dict[str, int | None]:
        counts: dict[str, int | None] = {}
        if page is None:
            return counts
        selectors = {
            "loginForm": self._FORM_SELECTOR,
            "username": self._USERNAME_SELECTOR,
            "password": self._PASSWORD_SELECTOR,
            "captchaImage": self._CAPTCHA_IMAGE_SELECTOR,
            "captchaInput": self._CAPTCHA_INPUT_SELECTOR,
            "submit": self._SUBMIT_SELECTOR,
            "logoutLink": 'a[href*="logout.php"]',
        }
        for name, selector in selectors.items():
            try:
                counts[name] = int(page.locator(selector).count())
            except Exception:
                counts[name] = None
        return counts

    @staticmethod
    def _redact_text(value: str, username: str, password: str) -> str:
        result = value
        for secret, replacement in (
            (password, "[REDACTED_PASSWORD]"),
            (username, "[REDACTED_USERNAME]"),
        ):
            if secret:
                result = result.replace(secret, replacement)
        result = re.sub(
            r"""(<input\b[^>]*\bname=["']password["'][^>]*\bvalue=["'])[^"']*""",
            r"\1[REDACTED_PASSWORD]",
            result,
            flags=re.I,
        )
        return re.sub(
            r"""(<input\b[^>]*\bvalue=["'])[^"']*(["'][^>]*\bname=["']password["'])""",
            r"\1[REDACTED_PASSWORD]\2",
            result,
            flags=re.I,
        )

    def _launch_context(self) -> object:
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

    def _install_request_guard(self, context: object, blocked_requests: list[str]) -> None:
        """只允许本适配器访问 BTSCHOOL 的 HTTPS 站点域名。"""
        def guard_route(route: object) -> None:
            parsed = urlparse(route.request.url)
            if (
                parsed.scheme != "https"
                or (parsed.hostname or "").lower().rstrip(".") != self._HOST
                or parsed.port not in {None, 443}
            ):
                if len(blocked_requests) < 50:
                    blocked_requests.append(str(route.request.url))
                logger.warning(
                    "BTSCHOOL 请求被站点域名白名单拦截: url=%s",
                    route.request.url,
                )
                route.abort()
                return
            try:
                self._url_guard.ensure_allowed(route.request.url)
            except ValueError as error:
                if len(blocked_requests) < 50:
                    blocked_requests.append(str(route.request.url))
                logger.warning(
                    "BTSCHOOL 请求被出站安全策略拦截: url=%s reason=%s",
                    route.request.url,
                    error,
                )
                route.abort()
                return
            route.continue_()

        context.route("**/*", guard_route)

    @classmethod
    def _build_site_origin(cls, site_url: str) -> str:
        parsed = urlparse(site_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or host != cls._HOST
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("BTSCHOOL 站点地址无效")
        return f"https://{cls._HOST}"

    @staticmethod
    def _ensure_same_origin(url: str, expected_origin: str) -> None:
        parsed = urlparse(url)
        actual_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if actual_origin != expected_origin:
            raise ValueError("BTSCHOOL 登录导航离开允许域名")

    @staticmethod
    def _ensure_form_action(action: str | None, page_url: str, expected_origin: str) -> None:
        action_url = urljoin(page_url, action or page_url)
        parsed = urlparse(action_url)
        if f"{parsed.scheme}://{parsed.netloc}".rstrip("/") != expected_origin:
            raise ValueError("BTSCHOOL 登录表单提交地址无效")

    @staticmethod
    def _classify_result(final_url: str, html: str) -> str:
        text = re.sub(r"<[^>]+>", " ", html)
        if _CAPTCHA_ERROR_PATTERN.search(text):
            return "captcha_error"
        if _CREDENTIAL_ERROR_PATTERN.search(text):
            return "credential_error"
        path = urlparse(final_url).path.rstrip("/")
        if path in {"", "/index.php"} and _LOGOUT_LINK_PATTERN.search(html):
            return "success"
        return "unknown"

    @staticmethod
    def _cookie_header(cookies: list[dict], host: str) -> str:
        values: list[str] = []
        for cookie in cookies:
            name = str(cookie.get("name") or "").strip()
            value = str(cookie.get("value") or "")
            domain = str(cookie.get("domain") or "").lower().lstrip(".").rstrip(".")
            if not name or not domain or not (host == domain or host.endswith(f".{domain}")):
                continue
            parsed = SimpleCookie()
            parsed[name] = value
            values.append(f"{name}={parsed[name].value}")
        return "; ".join(values)

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
