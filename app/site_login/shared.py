"""PT站点自动登录适配器共享执行能力。"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from app.models import SiteLoginCredential, SiteLoginRequest, SiteLoginResponse
from app.site_login.btschool import BtschoolLoginAdapter, CaptchaRecognizer, _CAPTCHA_PATTERN
from app.totp import generate_totp

logger = logging.getLogger("elysium.browser_gateway.site_login.shared")

_CAPTCHA_ERROR = re.compile(r"图片代码无效|圖片代碼無效|图片代码已被清除|圖片代碼已被清除", re.I)
_CREDENTIAL_ERROR = re.compile(
    r"用户名或密码不正确|用戶名或密碼不正確|還沒有通過驗證|还没有通过验证|"
    r"身份凭据与站点记录不符合|账号或密码错误|帳號或密碼錯誤",
    re.I,
)
_TWO_FACTOR_ERROR = re.compile(
    r"两步验证码错误|兩步驗證碼錯誤|两步验证码未输入|兩步驗證碼未輸入|"
    r"两步验证\s*code\s*无效|兩步驗證\s*code\s*無效|"
    r"(?:2FA|二步验证|二步驗證|两步验证|兩步驗證|动态口令).{0,24}(?:错误|錯誤|无效|無效|失败|失敗)",
    re.I,
)
_TURNSTILE_ERROR = re.compile(
    r"Verify not success|Turnstile.{0,24}(?:error|failed|invalid)|"
    r"(?:Cloudflare|安全|人机|人機|机器人|機器人).{0,24}(?:验证|驗證).{0,16}(?:失败|失敗|无效|無效)|"
    r"Are you a bot",
    re.I,
)
_CF_MARKERS = (
    "cf-chl",
    "challenge-platform",
    "cf-turnstile",
    "just a moment",
    "checking your browser",
    "attention required",
    "cloudflare ray id",
)
_SENSITIVE_INPUT = re.compile(
    r"""(<input\b[^>]*\bname=["'](?:password|imagestring|two_step_code|scode|2fa|response|_token|cf-turnstile-response)["'][^>]*\bvalue=["'])[^"']*""",
    re.I,
)
_SENSITIVE_INPUT_REVERSED = re.compile(
    r"""(<input\b[^>]*\bvalue=["'])[^"']*(["'][^>]*\bname=["'](?:password|imagestring|two_step_code|scode|2fa|response|_token|cf-turnstile-response)["'])""",
    re.I,
)
_SENSITIVE_TEXTAREA = re.compile(
    r"""(<textarea\b[^>]*\bname=["'](?:cf-turnstile-response|cf_chl_[^"']*)["'][^>]*>).*?(</textarea>)""",
    re.I | re.S,
)


@dataclass(frozen=True)
class SiteDefinition:
    key: str
    label: str
    hosts: tuple[str, ...]
    default_host: str
    image_captcha: bool = False
    turnstile: bool = False
    challenge: bool = False
    two_factor_field: str | None = None
    login_path: str = "/login.php"
    form_selector: str = 'form[action$="takelogin.php"][method="post"]'
    submit_selector: str = "#submit-btn"
    reveal_selector: str | None = None
    unit3d: bool = False


class ConfiguredSiteLoginAdapter(BtschoolLoginAdapter):
    """供独立站点 adapter 复用的浏览器登录执行基类。"""

    _TURNSTILE_HOST = "challenges.cloudflare.com"
    _CAPTCHA_IMAGE_SELECTOR = 'img[alt="CAPTCHA"], img[src*="action=regimage"]'
    _CAPTCHA_INPUT_SELECTOR = 'input[name="imagestring"]'
    _TURNSTILE_RESPONSE_SELECTOR = '[name="cf-turnstile-response"], input[name^="cf_chl_"]'
    _MAX_DIAGNOSTIC_HTML_CHARS = 512 * 1024

    def __init__(
        self,
        settings: object,
        url_guard: object,
        definition: SiteDefinition,
        recognizer: CaptchaRecognizer | None = None,
        context_factory: object | None = None,
    ):
        super().__init__(settings, url_guard, recognizer, context_factory)
        self._definition = definition

    def supports(self, site_key: str) -> bool:
        return site_key.strip().lower() == self._definition.key

    def login(self, request: SiteLoginRequest) -> SiteLoginResponse:
        started_at = time.monotonic()
        definition = self._definition
        username = request.credentials.get("username", "").strip()
        password = request.credentials.get("password", "")
        two_factor_secret = request.credentials.get("twoFactorSecret", "").strip()
        if not username or not password:
            return self._failure(request, started_at, f"{definition.label}自动登录需要账号和密码")

        site_origin = self._build_origin(str(request.site_url), definition)
        login_url = f"{site_origin}{definition.login_path}"
        self._url_guard.ensure_allowed(login_url)
        timeout_seconds = min(
            request.timeout_seconds or self._settings.default_timeout_seconds,
            self._settings.max_timeout_seconds,
        )
        attempts = 2 if definition.image_captcha or definition.turnstile else 1
        context = None
        page = None
        response = None
        stage = "launch-context"
        blocked_requests: list[str] = []
        secrets = [username, password, two_factor_secret]
        try:
            context = self._launch_context()
            stage = "install-request-guard"
            self._install_multi_request_guard(context, definition, blocked_requests)
            page = context.new_page()
            page.set_default_timeout(timeout_seconds * 1000)

            for attempt in range(1, attempts + 1):
                stage = "navigate-login-page"
                response = page.goto(login_url, wait_until="load", timeout=timeout_seconds * 1000)
                self._ensure_allowed_page_url(page.url, definition)
                stage = "inspect-login-page"
                html = self._read_stable_page_content(page, timeout_seconds=5)
                outcome = self._classify(page.url, html, definition)
                if outcome == "success":
                    return self._success(request, started_at, page, context, site_origin, definition)
                if (
                    not page.locator(definition.form_selector).count()
                    and self._has_cloudflare_challenge(html)
                ):
                    stage = "wait-cloudflare-page"
                    outcome, html = self._wait_for_page_outcome(
                        page,
                        definition,
                        timeout_seconds=timeout_seconds,
                        accept_login_form=True,
                    )
                    if outcome == "success":
                        return self._success(
                            request, started_at, page, context, site_origin, definition,
                        )

                if definition.reveal_selector:
                    stage = "reveal-login-form"
                    page.locator(definition.reveal_selector).click()

                form = page.locator(definition.form_selector).first
                if form.count() != 1:
                    self._diagnostic(
                        request, definition, stage="login-form-missing", attempt=attempt,
                        page=page, response=response, blocked=blocked_requests, secrets=secrets,
                    )
                    return self._failure(
                        request, started_at, f"{definition.label}登录页未找到登录表单",
                    )
                self._ensure_form_action(
                    form.get_attribute("action"), page.url, site_origin, definition,
                )

                captcha_code = ""
                if definition.image_captcha:
                    stage = "recognize-captcha"
                    try:
                        image = form.locator(self._CAPTCHA_IMAGE_SELECTOR).first
                        image.wait_for(state="visible")
                        captcha_code = self._recognizer.recognize(image.screenshot())
                    except Exception as error:
                        if attempt < attempts:
                            continue
                        self._diagnostic(
                            request, definition, stage="captcha-ocr-error", attempt=attempt,
                            page=page, response=response, blocked=blocked_requests,
                            secrets=secrets, error=error,
                        )
                        return self._failure(
                            request, started_at, f"{definition.label}本地OCR识别失败",
                        )
                    if not _CAPTCHA_PATTERN.fullmatch(captcha_code):
                        if attempt < attempts:
                            continue
                        self._diagnostic(
                            request, definition, stage="captcha-format-invalid", attempt=attempt,
                            page=page, response=response, blocked=blocked_requests,
                            secrets=secrets,
                        )
                        return self._failure(
                            request, started_at, f"{definition.label}本地OCR结果格式无效",
                        )
                    secrets.append(captcha_code)

                stage = "fill-login-form"
                form.locator('input[name="username"]').fill(username)
                form.locator('input[type="password"]').fill(password)
                if captcha_code:
                    form.locator(self._CAPTCHA_INPUT_SELECTOR).fill(captcha_code)

                if definition.turnstile:
                    stage = "wait-cloudflare-turnstile"
                    page.wait_for_function(
                        """selector => {
                            const field = document.querySelector(selector);
                            return !!field && typeof field.value === "string"
                                && field.value.trim().length > 0;
                        }""",
                        self._TURNSTILE_RESPONSE_SELECTOR,
                        timeout=timeout_seconds * 1000,
                    )

                code = ""
                if definition.two_factor_field and two_factor_secret:
                    stage = "generate-two-factor-code"
                    code = generate_totp(two_factor_secret, minimum_validity_seconds=5)
                    secrets.append(code)
                    form.locator(
                        f'input[name="{definition.two_factor_field}"]',
                    ).fill(code)

                stage = "submit-login-form"
                form.locator(definition.submit_selector).first.click()
                stage = "inspect-login-result"
                outcome, html = self._wait_for_page_outcome(
                    page,
                    definition,
                    timeout_seconds=min(timeout_seconds, 30),
                )
                self._ensure_allowed_page_url(page.url, definition)
                if outcome == "success":
                    return self._success(request, started_at, page, context, site_origin, definition)
                if outcome == "captcha_error" and attempt < attempts:
                    continue
                if outcome == "turnstile_error" and attempt < attempts:
                    continue

                self._diagnostic(
                    request, definition, stage=outcome, attempt=attempt, page=page,
                    response=None, blocked=blocked_requests, secrets=secrets,
                )
                messages = {
                    "captcha_error": f"{definition.label}图片验证码错误",
                    "credential_error": f"{definition.label}用户名或密码不正确",
                    "two_factor_error": f"{definition.label}2FA验证码错误，请检查2FA密钥和系统时间",
                    "turnstile_error": f"{definition.label}Cloudflare Turnstile验证未通过",
                }
                return self._failure(
                    request, started_at,
                    messages.get(outcome, f"{definition.label}登录结果无法确认"),
                )
            return self._failure(request, started_at, f"{definition.label}自动登录失败")
        except ValueError as error:
            if "2FA密钥格式无效" in str(error):
                return self._failure(request, started_at, f"{definition.label}2FA密钥格式无效")
            raise
        except Exception as error:
            self._diagnostic(
                request, definition, stage=stage, attempt=None, page=page,
                response=response, blocked=blocked_requests, secrets=secrets, error=error,
            )
            logger.exception(
                "%s Browser自动登录异常: request_id=%s stage=%s error_type=%s",
                definition.label, request.request_id, stage, type(error).__name__,
            )
            return self._failure(
                request, started_at,
                f"{definition.label}自动登录请求异常（阶段：{stage}，类型：{type(error).__name__}）",
            )
        finally:
            secrets.clear()
            two_factor_secret = ""
            if context is not None:
                try:
                    context.close()
                except Exception:
                    logger.exception(
                        "%s Browser上下文关闭失败: request_id=%s",
                        definition.label, request.request_id,
                    )

    def _success(
        self,
        request: SiteLoginRequest,
        started_at: float,
        page: object,
        context: object,
        site_origin: str,
        definition: SiteDefinition,
    ) -> SiteLoginResponse:
        host = (urlparse(page.url).hostname or definition.default_host).lower().rstrip(".")
        cookie = self._cookie_header(context.cookies(site_origin), host)
        if not cookie:
            return self._failure(
                request, started_at, f"{definition.label}登录成功但未返回 Cookie",
            )
        user_agent = str(page.evaluate("() => navigator.userAgent") or "").strip()
        language = str(page.evaluate("() => navigator.language") or "zh-CN").strip()
        return SiteLoginResponse(
            request_id=request.request_id,
            site_key=request.site_key,
            success=True,
            message=f"{definition.label}自动登录成功",
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

    def _install_multi_request_guard(
        self,
        context: object,
        definition: SiteDefinition,
        blocked: list[str],
    ) -> None:
        allowed_hosts = set(definition.hosts) | {self._TURNSTILE_HOST}

        def guard_route(route: object) -> None:
            request_url = str(route.request.url)
            parsed = urlparse(request_url)
            if parsed.scheme in {"data", "blob"}:
                route.continue_()
                return
            host = (parsed.hostname or "").lower().rstrip(".")
            if (
                parsed.scheme != "https"
                or host not in allowed_hosts
                or parsed.port not in {None, 443}
            ):
                if len(blocked) < 50:
                    blocked.append(request_url)
                logger.debug("%s 请求被站点域名白名单拦截: url=%s", definition.label, request_url)
                route.abort()
                return
            try:
                self._url_guard.ensure_allowed(request_url)
            except ValueError as error:
                if len(blocked) < 50:
                    blocked.append(request_url)
                logger.warning(
                    "%s 请求被出站安全策略拦截: url=%s reason=%s",
                    definition.label, request_url, error,
                )
                route.abort()
                return
            route.continue_()

        context.route("**/*", guard_route)

    @staticmethod
    def _build_origin(site_url: str, definition: SiteDefinition) -> str:
        parsed = urlparse(site_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or host not in definition.hosts
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(f"{definition.label}站点地址无效")
        return f"https://{host}"

    @staticmethod
    def _ensure_allowed_page_url(url: str, definition: SiteDefinition) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or host not in definition.hosts:
            raise ValueError(f"{definition.label}登录导航离开允许域名")

    @staticmethod
    def _ensure_form_action(
        action: str | None,
        page_url: str,
        expected_origin: str,
        definition: SiteDefinition,
    ) -> None:
        action_url = urljoin(page_url, action or page_url)
        parsed = urlparse(action_url)
        if f"{parsed.scheme}://{parsed.netloc}".rstrip("/") != expected_origin:
            raise ValueError(f"{definition.label}登录表单提交地址无效")

    @staticmethod
    def _classify(final_url: str, html: str, definition: SiteDefinition) -> str:
        text = re.sub(r"<[^>]+>", " ", html)
        if _CAPTCHA_ERROR.search(text):
            return "captcha_error"
        if _TWO_FACTOR_ERROR.search(text):
            return "two_factor_error"
        if _TURNSTILE_ERROR.search(text):
            return "turnstile_error"
        if _CREDENTIAL_ERROR.search(text):
            return "credential_error"
        lower_html = html.lower()
        if definition.unit3d:
            if re.search(r"""<form[^>]+action=["'][^"']*/logout["']""", html, re.I):
                return "success"
            return "unknown"
        authenticated = bool(
            re.search(r"""(?:href|data-url)\s*=\s*["'][^"']*logout\.php""", html, re.I)
            or ("userdetails.php" in lower_html and ("欢迎回来" in text or "歡迎回來" in text))
        )
        path = urlparse(final_url).path.rstrip("/")
        index_marker = path in {"", "/index.php"} or bool(
            re.search(r"<title[^>]*>[^<]*(?:首页|首頁)", html, re.I)
        )
        return "success" if authenticated and index_marker else "unknown"

    def _wait_for_page_outcome(
        self,
        page: object,
        definition: SiteDefinition,
        *,
        timeout_seconds: int,
        accept_login_form: bool = False,
    ) -> tuple[str, str]:
        """等待异步挑战或CF导航完成，超时后保留最后页面用于诊断。"""
        deadline = time.monotonic() + timeout_seconds
        last_html = ""
        while time.monotonic() < deadline:
            try:
                last_html = self._read_stable_page_content(page, timeout_seconds=2)
                outcome = self._classify(page.url, last_html, definition)
                if outcome != "unknown":
                    return outcome, last_html
                if accept_login_form and page.locator(definition.form_selector).count():
                    return "login", last_html
            except Exception:
                # 页面正在切换 frame 时继续轮询。
                pass
            time.sleep(0.25)
        return self._classify(page.url, last_html, definition), last_html

    @staticmethod
    def _has_cloudflare_challenge(html: str) -> bool:
        lower = html.lower()
        return any(marker in lower for marker in _CF_MARKERS)

    def _diagnostic(
        self,
        request: SiteLoginRequest,
        definition: SiteDefinition,
        *,
        stage: str,
        attempt: int | None,
        page: object | None,
        response: object | None,
        blocked: list[str],
        secrets: list[str],
        error: Exception | None = None,
    ) -> None:
        final_url = ""
        title = ""
        html = ""
        read_error = ""
        if page is not None:
            try:
                final_url = str(page.url or "")
                title = str(page.title() or "")
                html = self._read_stable_page_content(page, timeout_seconds=2)
            except Exception as current_error:
                read_error = f"{type(current_error).__name__}: {current_error}"
        html = self._redact(html, secrets)
        lower_html = html.lower()
        response_headers: dict[str, str] = {}
        response_status = None
        if response is not None:
            try:
                response_status = int(response.status)
                response_headers = {
                    str(name): self._redact(str(value), secrets)
                    for name, value in dict(response.headers).items()
                    if str(name).lower() not in {"set-cookie", "cookie", "authorization"}
                }
            except Exception:
                response_headers = {}
        normalized = {name.lower(): value for name, value in response_headers.items()}
        markers = [marker for marker in _CF_MARKERS if marker in lower_html]
        diagnostic = {
            "requestId": request.request_id,
            "siteKey": definition.key,
            "stage": stage,
            "attempt": attempt,
            "finalUrl": self._redact(final_url, secrets),
            "title": self._redact(title, secrets),
            "responseStatus": response_status,
            "responseHeaders": response_headers,
            "htmlLength": len(html),
            "htmlTruncated": len(html) > self._MAX_DIAGNOSTIC_HTML_CHARS,
            "pageReadError": self._redact(read_error, secrets),
            "cloudflare": {
                "detected": bool(
                    markers
                    or normalized.get("cf-ray")
                    or normalized.get("cf-mitigated")
                    or "cloudflare" in normalized.get("server", "").lower()
                ),
                "ray": normalized.get("cf-ray", ""),
                "mitigated": normalized.get("cf-mitigated", ""),
                "challengeMarkers": markers,
            },
            "blockedRequests": [self._redact(value, secrets) for value in blocked[-50:]],
            "errorType": type(error).__name__ if error else "",
            "error": self._redact(str(error), secrets) if error else "",
        }
        logger.error(
            "%s Browser页面诊断: %s",
            definition.label,
            json.dumps(diagnostic, ensure_ascii=False, default=str),
        )
        logger.error(
            "%s Browser脱敏页面HTML: request_id=%s stage=%s\n%s",
            definition.label,
            request.request_id,
            stage,
            html[: self._MAX_DIAGNOSTIC_HTML_CHARS] or "[页面HTML不可用]",
        )

    @staticmethod
    def _redact(value: str, secrets: list[str]) -> str:
        result = value
        for secret in sorted((item for item in secrets if item), key=len, reverse=True):
            result = result.replace(secret, "[REDACTED]")
        result = _SENSITIVE_INPUT.sub(r"\1[REDACTED]", result)
        result = _SENSITIVE_INPUT_REVERSED.sub(r"\1[REDACTED]\2", result)
        return _SENSITIVE_TEXTAREA.sub(r"\1[REDACTED]\2", result)
