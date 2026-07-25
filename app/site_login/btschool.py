"""BTSCHOOL 图片验证码登录适配器。"""

from __future__ import annotations

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

        context = self._launch_context()
        try:
            self._install_request_guard(context)
            page = context.new_page()
            page.set_default_timeout(timeout_seconds * 1000)
            last_message = "BTSCHOOL 验证码识别失败"
            for attempt in range(self._MAX_ATTEMPTS):
                page.goto(login_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
                self._ensure_same_origin(page.url, site_origin)
                form = page.locator(self._FORM_SELECTOR)
                if form.count() != 1:
                    return self._failure(request, started_at, "BTSCHOOL 登录页未找到登录表单")
                self._ensure_form_action(form.get_attribute("action"), login_url, site_origin)

                image = page.locator(self._CAPTCHA_IMAGE_SELECTOR).first
                image.wait_for(state="visible")
                try:
                    code = self._recognizer.recognize(image.screenshot())
                except Exception:
                    last_message = "BTSCHOOL 本地OCR识别失败"
                    if attempt + 1 < self._MAX_ATTEMPTS:
                        continue
                    return self._failure(request, started_at, last_message)
                if not _CAPTCHA_PATTERN.fullmatch(code):
                    last_message = "BTSCHOOL 本地OCR结果格式无效"
                    if attempt + 1 < self._MAX_ATTEMPTS:
                        continue
                    return self._failure(request, started_at, last_message)

                page.locator(self._USERNAME_SELECTOR).fill(username)
                page.locator(self._PASSWORD_SELECTOR).fill(password)
                page.locator(self._CAPTCHA_INPUT_SELECTOR).fill(code)
                page.locator(self._SUBMIT_SELECTOR).click()
                page.wait_for_load_state("domcontentloaded")
                self._ensure_same_origin(page.url, site_origin)
                html = page.content()
                outcome = self._classify_result(page.url, html)
                if outcome == "success":
                    user_agent = str(page.evaluate("() => navigator.userAgent") or "").strip()
                    language = str(page.evaluate("() => navigator.language") or "zh-CN").strip()
                    cookie = self._cookie_header(context.cookies(site_origin), self._HOST)
                    if not cookie:
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
                if outcome == "credential_error":
                    return self._failure(
                        request,
                        started_at,
                        "BTSCHOOL 用户名或密码不正确，或者账号尚未通过验证",
                    )
                if outcome == "captcha_error":
                    last_message = "BTSCHOOL 图片验证码错误"
                    if attempt + 1 < self._MAX_ATTEMPTS:
                        continue
                    return self._failure(request, started_at, last_message)
                return self._failure(request, started_at, "BTSCHOOL 登录结果无法确认")
            return self._failure(request, started_at, last_message)
        except ValueError:
            raise
        except Exception:
            return self._failure(request, started_at, "BTSCHOOL 自动登录请求异常")
        finally:
            context.close()

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

    def _install_request_guard(self, context: object) -> None:
        """只允许本适配器访问 BTSCHOOL 的 HTTPS 站点域名。"""
        def guard_route(route: object) -> None:
            parsed = urlparse(route.request.url)
            if (
                parsed.scheme != "https"
                or (parsed.hostname or "").lower().rstrip(".") != self._HOST
                or parsed.port not in {None, 443}
            ):
                route.abort()
                return
            try:
                self._url_guard.ensure_allowed(route.request.url)
            except ValueError:
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
