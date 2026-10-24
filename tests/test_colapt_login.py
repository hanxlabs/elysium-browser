"""可乐 ReelCore 会话登录流程，使用脱敏 API 响应验证。"""

import json
from unittest.mock import Mock

import pytest

from app.config import Settings
from app.models import SiteLoginRequest
from app.site_login.colapt import ColaptLoginAdapter
from app.site_login.service import SiteLoginService

_CSRF = "a" * 64
_CURRENT_CSRF = "b" * 64
_CHALLENGE = "c" * 64
_CREATED = {"status": "authenticated", "csrf_token": _CSRF, "expires_at": "2099-01-01T00:00:00Z"}
_ME = {
    "id": 42, "username": "fixture-user", "status": "active", "roles": [],
    "permissions": [], "groups": [], "two_factor": {"enabled": False, "required": False},
    "session": {"csrf_token": _CURRENT_CSRF, "expires_at": "2099-01-01T00:00:00Z"},
}
_TWO_FACTOR = {
    "status": "two_factor_required", "challenge_token": _CHALLENGE,
    "expires_at": "2099-01-01T00:00:00Z", "methods": ["totp", "recovery_code"],
}


class _Page:
    def __init__(self, responses, *, final_url="https://www.colapt.com/login"):
        self.url = final_url
        self.responses = list(responses)
        self.calls = []
        self.goto = Mock()
        self.set_default_timeout = Mock()

    def evaluate(self, script, arguments):
        self.calls.append(arguments)
        status, payload = self.responses.pop(0)
        return {
            "status": status, "body": json.dumps(payload),
            "userAgent": "fixture-agent", "language": "zh-CN",
        }


def _request(**credentials):
    return SiteLoginRequest(
        request_id="colapt-fixture", site_key="colapt", account_id=1,
        site_url="https://www.colapt.com/", timeout_seconds=30,
        credentials={"username": "fixture-user", "password": "fixture-password", **credentials},
    )


def _adapter(responses, *, cookies=True, final_url="https://www.colapt.com/login"):
    page = _Page(responses, final_url=final_url)
    context = Mock()
    context.new_page.return_value = page
    context.cookies.return_value = [
        {"name": "reelcore_session", "value": "fixture-session", "domain": ".colapt.com"},
        {"name": "unrelated", "value": "ignored", "domain": "another.example"},
    ] if cookies else []
    guard = Mock()
    adapter = ColaptLoginAdapter(Settings(), guard, context_factory=lambda **_: context)
    return adapter, page, context, guard


def test_login_confirms_session_and_returns_current_csrf_and_same_site_cookie():
    adapter, page, context, guard = _adapter([(201, _CREATED), (200, _ME)])

    result = adapter.login(_request())

    assert result.success is True
    assert result.credential.cookie == "reelcore_session=fixture-session"
    assert result.credential.bearer_token is None
    assert result.credential.headers["X-CSRF-Token"] == _CURRENT_CSRF
    assert result.credential.headers["Origin"] == "https://www.colapt.com"
    assert result.credential.headers["Referer"] == "https://www.colapt.com/"
    assert result.credential.headers["User-Agent"] == "fixture-agent"
    assert page.calls == [
        {"path": "/api/v1/sessions", "method": "POST", "body": {
            "username": "fixture-user", "password": "fixture-password",
        }, "timeoutMs": 30000},
        {"path": "/api/v1/me", "method": "GET", "body": None, "timeoutMs": 30000},
    ]
    page.goto.assert_called_once_with(
        "https://www.colapt.com/login", wait_until="domcontentloaded", timeout=30000,
    )
    context.cookies.assert_called_once_with("https://www.colapt.com/api/v1/me")
    context.close.assert_called_once()


def test_totp_challenge_is_verified_once_in_the_same_browser_context(monkeypatch):
    adapter, page, context, _ = _adapter([(202, _TWO_FACTOR), (201, _CREATED), (200, _ME)])
    totp = Mock(return_value="123456")
    monkeypatch.setattr("app.site_login.colapt.generate_totp", totp)

    result = adapter.login(_request(twoFactorSecret="fixture-secret"))

    assert result.success is True
    totp.assert_called_once_with("fixture-secret", minimum_validity_seconds=5)
    assert page.calls[1]["path"] == "/api/v1/sessions/verify"
    assert page.calls[1]["body"] == {"challenge_token": _CHALLENGE, "code": "123456"}
    context.new_page.assert_called_once()
    context.close.assert_called_once()


def test_totp_is_not_generated_for_accounts_without_a_challenge(monkeypatch):
    adapter, _, _, _ = _adapter([(201, _CREATED), (200, _ME)])
    totp = Mock(side_effect=AssertionError("TOTP should not be generated"))
    monkeypatch.setattr("app.site_login.colapt.generate_totp", totp)

    assert adapter.login(_request(twoFactorSecret="unused-secret")).success is True
    totp.assert_not_called()


@pytest.mark.parametrize("challenge,credentials,message", [
    (_TWO_FACTOR, {}, "配置两步验证密钥"),
    (_TWO_FACTOR, {"twoFactorSecret": "invalid!"}, "2FA密钥格式无效"),
    ({**_TWO_FACTOR, "challenge_token": ""}, {"twoFactorSecret": "secret"}, "有效的两步验证挑战"),
    ({**_TWO_FACTOR, "methods": ["recovery_code"]}, {"twoFactorSecret": "secret"}, "账号恢复"),
    ({**_TWO_FACTOR, "status": "enrollment_required"}, {}, "两步验证设置"),
    ({**_TWO_FACTOR, "status": "recovery_required"}, {}, "账号恢复"),
])
def test_challenges_requiring_user_action_do_not_resubmit_password(challenge, credentials, message):
    adapter, page, context, _ = _adapter([(202, challenge)])

    result = adapter.login(_request(**credentials))

    assert result.success is False
    assert message in result.message
    assert result.credential is None
    assert len(page.calls) == 1
    context.close.assert_called_once()


@pytest.mark.parametrize("status,code,message", [
    (401, "invalid_credentials", "账号、密码或两步验证码不正确"),
    (403, "captcha_required", "人机验证"),
    (403, "captcha_invalid", "人机验证"),
    (403, "captcha_expired", "人机验证"),
    (403, "captcha_replayed", "人机验证"),
    (429, "rate_limited", "过于频繁"),
])
def test_api_refusals_are_reported_without_retrying_or_echoing_secrets(status, code, message):
    error = {"error": {"code": code, "message": "fixture-password", "fields": {}, "request_id": "request"}}
    adapter, page, context, _ = _adapter([(status, error)])

    result = adapter.login(_request())

    assert result.success is False
    assert message in result.message
    assert "fixture-password" not in result.message
    assert len(page.calls) == 1
    context.close.assert_called_once()


def test_wrong_totp_is_not_retried(monkeypatch):
    error = {"error": {"code": "invalid_credentials", "message": "invalid", "fields": {}}}
    adapter, page, _, _ = _adapter([(202, _TWO_FACTOR), (401, error)])
    monkeypatch.setattr("app.site_login.colapt.generate_totp", lambda *_args, **_kwargs: "123456")

    result = adapter.login(_request(twoFactorSecret="fixture-secret"))

    assert result.success is False
    assert "两步验证码不正确" in result.message
    assert [call["path"] for call in page.calls] == ["/api/v1/sessions", "/api/v1/sessions/verify"]


@pytest.mark.parametrize("me", [
    {"message": "not logged in"},
    {**_ME, "id": True},
    {**_ME, "status": "disabled"},
    {**_ME, "username": "another-member"},
    {**_ME, "session": {"csrf_token": ""}},
])
def test_success_requires_a_confirmed_current_user_and_csrf(me):
    adapter, _, _, _ = _adapter([(201, _CREATED), (200, me)])
    result = adapter.login(_request())
    assert result.success is False
    assert "有效会话" in result.message
    assert result.credential is None


def test_login_requires_a_session_cookie():
    adapter, _, _, _ = _adapter([(201, _CREATED), (200, _ME)], cookies=False)
    result = adapter.login(_request())
    assert result.success is False
    assert "未返回 Cookie" in result.message


def test_marker_cookie_alone_does_not_authenticate_the_session():
    adapter, _, context, _ = _adapter([(201, _CREATED), (200, _ME)])
    context.cookies.return_value = [
        {"name": "reelcore_session_marker", "value": "1", "domain": ".colapt.com"},
    ]
    assert adapter.login(_request()).success is False


def test_current_user_matching_accepts_normalized_and_case_insensitive_usernames():
    adapter, _, _, _ = _adapter([(201, _CREATED), (200, _ME)])
    assert adapter.login(_request(username="ＦＩＸＴＵＲＥ-user")).success is True


def test_password_change_requirement_is_reported_before_business_requests():
    adapter, page, _, _ = _adapter([(201, {**_CREATED, "password_change_required": True})])
    result = adapter.login(_request())
    assert result.success is False
    assert "修改密码" in result.message
    assert len(page.calls) == 1


@pytest.mark.parametrize("url", [
    "http://www.colapt.com/", "https://www.colapt.com.evil.example/",
    "https://api.colapt.com/", "https://colapt.com:8443/",
    "https://user:password@www.colapt.com/",
])
def test_rejects_origins_outside_the_observed_site_hosts(url):
    with pytest.raises(ValueError, match="站点地址无效"):
        ColaptLoginAdapter._build_origin(url)


def test_site_is_registered_and_alias_redirect_uses_the_final_host():
    assert any(adapter.supports("colapt") for adapter in SiteLoginService(Settings())._adapters)
    assert ColaptLoginAdapter._build_origin("https://colapt.com/login") == "https://colapt.com"
    adapter, _, context, _ = _adapter([(201, _CREATED), (200, _ME)])
    request = _request().model_copy(update={"site_url": "https://colapt.com/"})
    assert adapter.login(request).success is True
    context.cookies.assert_called_once_with("https://www.colapt.com/api/v1/me")


def test_request_guard_rejects_external_destinations_and_private_address_failures():
    adapter, _, context, guard = _adapter([])
    adapter._install_request_guard(context)
    handler = context.route.call_args.args[1]
    allowed = Mock()
    allowed.request.url = "https://www.colapt.com/api/v1/sessions"
    handler(allowed)
    allowed.continue_.assert_called_once()
    forbidden = Mock()
    forbidden.request.url = "https://www.colapt.com.evil.example/api/v1/sessions"
    handler(forbidden)
    forbidden.abort.assert_called_once()
    guard.ensure_allowed.side_effect = ValueError("private IP")
    private = Mock()
    private.request.url = "https://colapt.com/api/v1/sessions"
    handler(private)
    private.abort.assert_called_once()


def test_failed_browser_operation_closes_context_and_redacts_exception(caplog):
    adapter, page, context, _ = _adapter([])
    page.goto.side_effect = RuntimeError("fixture-password")

    result = adapter.login(_request())

    assert result.success is False
    assert "fixture-password" not in result.message
    assert "fixture-password" not in caplog.text
    context.close.assert_called_once()
