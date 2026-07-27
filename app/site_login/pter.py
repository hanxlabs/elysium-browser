"""猫站 Cloudflare Turnstile 与可选 2FA 登录适配器。"""

from __future__ import annotations

import json
import logging
import re
import time
from urllib.parse import urlparse

from app.models import SiteLoginCredential, SiteLoginRequest, SiteLoginResponse
from app.site_login.btschool import BtschoolLoginAdapter
from app.totp import generate_totp

logger = logging.getLogger("elysium.browser_gateway.site_login.pter")

_CREDENTIAL_ERROR_PATTERN = re.compile(r"用户名或密码不正确|还没有通过验证")
_TWO_FACTOR_ERROR_PATTERN = re.compile(
    r"(?:2FA|二步验证|两步验证|动态口令).{0,20}(?:错误|无效|失败)|"
    r"(?:错误|无效|失败).{0,20}(?:2FA|二步验证|两步验证|动态口令)",
    re.I,
)
_TURNSTILE_ERROR_PATTERN = re.compile(
    r"Verify not success|验证未通过|Are you a bot\?",
    re.I,
)


class PterLoginAdapter(BtschoolLoginAdapter):
    """在真实浏览器页面中完成猫站 CF 与 2FA 登录。"""

    _SITE_KEY = "pter"
    _SITE_NAME = "猫站"
    _HOST = "pterclub.net"
    _TURNSTILE_HOST = "challenges.cloudflare.com"
    _FORM_SELECTOR = 'form[action$="takelogin.php"][method="post"]'
    _TWO_FACTOR_SELECTOR = 'input[name="2fa_secret"]'
    _SUBMIT_SELECTOR = 'input[type="submit"][value="登录"]'
    _TURNSTILE_RESPONSE_SELECTOR = (
        '[name="cf-turnstile-response"], input[name^="cf_chl_"]'
    )

    def login(self, request: SiteLoginRequest) -> SiteLoginResponse:
        started_at = time.monotonic()
        username = request.credentials.get("username", "").strip()
        password = request.credentials.get("password", "")
        two_factor_secret = request.credentials.get("twoFactorSecret", "").strip()
        if not username or not password:
            return self._failure(request, started_at, "猫站自动登录需要账号和密码")

        site_origin = self._build_site_origin(str(request.site_url))
        login_url = f"{site_origin}/login.php"
        self._url_guard.ensure_allowed(login_url)
        timeout_seconds = min(
            request.timeout_seconds or self._settings.default_timeout_seconds,
            self._settings.max_timeout_seconds,
        )

        context = None
        page = None
        code = ""
        stage = "launch-context"
        blocked_requests: list[str] = []
        try:
            context = self._launch_context()
            stage = "install-request-guard"
            self._install_request_guard(context, blocked_requests)
            stage = "open-page"
            page = context.new_page()
            page.set_default_timeout(timeout_seconds * 1000)

            stage = "navigate-login-page"
            page.goto(login_url, wait_until="load", timeout=timeout_seconds * 1000)
            self._ensure_same_origin(page.url, site_origin)
            stage = "inspect-login-page"
            login_html = self._read_stable_page_content(page, timeout_seconds=5)
            if self._classify_result(page.url, login_html) == "success":
                return self._success_response(
                    request,
                    started_at,
                    page,
                    context,
                    site_origin,
                )

            form = page.locator(self._FORM_SELECTOR)
            if form.count() != 1:
                self._log_page_state(
                    request.request_id,
                    "login-form-missing",
                    page,
                    username,
                    password,
                    two_factor_secret,
                    code,
                    blocked_requests,
                )
                return self._failure(request, started_at, "猫站登录页未找到登录表单")
            self._ensure_form_action(form.get_attribute("action"), login_url, site_origin)

            stage = "fill-login-form"
            page.locator(self._USERNAME_SELECTOR).fill(username)
            page.locator(self._PASSWORD_SELECTOR).fill(password)

            stage = "wait-cloudflare-turnstile"
            page.wait_for_function(
                """selector => {
                    const field = document.querySelector(selector);
                    return !!field && typeof field.value === "string" && field.value.trim().length > 0;
                }""",
                self._TURNSTILE_RESPONSE_SELECTOR,
                timeout=timeout_seconds * 1000,
            )

            if two_factor_secret:
                stage = "generate-two-factor-code"
                code = generate_totp(two_factor_secret, minimum_validity_seconds=5)
                page.locator(self._TWO_FACTOR_SELECTOR).fill(code)

            stage = "submit-login-form"
            with page.expect_navigation(
                wait_until="domcontentloaded",
                timeout=timeout_seconds * 1000,
            ):
                page.locator(self._SUBMIT_SELECTOR).click()
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
                )

            self._log_page_state(
                request.request_id,
                outcome,
                page,
                username,
                password,
                two_factor_secret,
                code,
                blocked_requests,
            )
            messages = {
                "credential_error": "猫站用户名或密码不正确，或者账号尚未通过验证",
                "two_factor_error": "猫站2FA验证码错误，请检查2FA密钥和系统时间",
                "turnstile_error": "猫站Cloudflare Turnstile验证未通过",
            }
            return self._failure(
                request,
                started_at,
                messages.get(outcome, "猫站登录结果无法确认"),
            )
        except ValueError as error:
            if "2FA密钥格式无效" in str(error):
                return self._failure(request, started_at, "猫站2FA密钥格式无效")
            raise
        except Exception as error:
            self._log_page_state(
                request.request_id,
                stage,
                page,
                username,
                password,
                two_factor_secret,
                code,
                blocked_requests,
                error,
            )
            logger.exception(
                "猫站Browser自动登录异常: request_id=%s stage=%s error_type=%s",
                request.request_id,
                stage,
                type(error).__name__,
            )
            return self._failure(
                request,
                started_at,
                f"猫站自动登录请求异常（阶段：{stage}，类型：{type(error).__name__}）",
            )
        finally:
            code = ""
            two_factor_secret = ""
            if context is not None:
                try:
                    context.close()
                except Exception:
                    logger.exception(
                        "猫站Browser上下文关闭失败: request_id=%s",
                        request.request_id,
                    )

    def _success_response(
        self,
        request: SiteLoginRequest,
        started_at: float,
        page: object,
        context: object,
        site_origin: str,
    ) -> SiteLoginResponse:
        cookie = self._cookie_header(context.cookies(site_origin), self._HOST)
        if not cookie:
            return self._failure(request, started_at, "猫站登录成功但未返回 Cookie")
        user_agent = str(page.evaluate("() => navigator.userAgent") or "").strip()
        language = str(page.evaluate("() => navigator.language") or "zh-CN").strip()
        return SiteLoginResponse(
            request_id=request.request_id,
            site_key=request.site_key,
            success=True,
            message="猫站自动登录成功",
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

    def _install_request_guard(
        self,
        context: object,
        blocked_requests: list[str],
    ) -> None:
        allowed_hosts = {self._HOST}

        def guard_route(route: object) -> None:
            request_url = str(route.request.url)
            parsed = urlparse(request_url)
            if parsed.scheme in {"data", "blob"}:
                route.continue_()
                return
            host = (parsed.hostname or "").lower().rstrip(".")
            challenge_host = (
                host == self._TURNSTILE_HOST
                or host.endswith(f".{self._TURNSTILE_HOST}")
            )
            if (
                parsed.scheme != "https"
                or (host not in allowed_hosts and not challenge_host)
                or parsed.port not in {None, 443}
            ):
                if len(blocked_requests) < 50:
                    blocked_requests.append(request_url)
                logger.debug("猫站请求被站点域名白名单拦截: url=%s", request_url)
                route.abort()
                return
            try:
                self._url_guard.ensure_allowed(request_url)
            except ValueError as error:
                if len(blocked_requests) < 50:
                    blocked_requests.append(request_url)
                logger.warning(
                    "猫站请求被出站安全策略拦截: url=%s reason=%s",
                    request_url,
                    error,
                )
                route.abort()
                return
            route.continue_()

        context.route("**/*", guard_route)

    @staticmethod
    def _classify_result(final_url: str, html: str) -> str:
        text = re.sub(r"<[^>]+>", " ", html)
        if _TURNSTILE_ERROR_PATTERN.search(text):
            return "turnstile_error"
        if _CREDENTIAL_ERROR_PATTERN.search(text):
            return "credential_error"
        if _TWO_FACTOR_ERROR_PATTERN.search(text):
            return "two_factor_error"
        path = urlparse(final_url).path.rstrip("/")
        index_title = bool(re.search(r"<title[^>]*>[^<]*首页", html, re.I))
        authenticated_marker = bool(
            re.search(r"""href\s*=\s*["'][^"']*logout\.php[^"']*["']""", html, re.I)
            or ("欢迎回来" in text and "userdetails.php" in html.lower())
        )
        if (path in {"", "/index.php"} or index_title) and authenticated_marker:
            return "success"
        return "unknown"

    @staticmethod
    def _log_page_state(
        request_id: str,
        stage: str,
        page: object | None,
        username: str,
        password: str,
        two_factor_secret: str,
        code: str,
        blocked_requests: list[str],
        error: Exception | None = None,
    ) -> None:
        final_url = ""
        title = ""
        body_text = ""
        error_text = str(error) if error else ""
        if page is not None:
            try:
                final_url = str(page.url or "")
                title = str(page.title() or "")
                body_text = str(page.locator("body").inner_text(timeout=2000) or "")
            except Exception:
                pass
        for secret in (password, username, two_factor_secret, code):
            if secret:
                body_text = body_text.replace(secret, "[REDACTED]")
                error_text = error_text.replace(secret, "[REDACTED]")
        logger.error(
            "猫站Browser页面诊断: %s",
            json.dumps(
                {
                    "requestId": request_id,
                    "stage": stage,
                    "finalUrl": final_url,
                    "title": title,
                    "bodyPreview": body_text[:4000],
                    "blockedRequests": blocked_requests[-50:],
                    "errorType": type(error).__name__ if error else "",
                    "error": error_text,
                },
                ensure_ascii=False,
            ),
        )
