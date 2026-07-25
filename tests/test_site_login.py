"""站点登录适配器测试。"""

import json

import pytest

from app.config import Settings
from app.models import SiteLoginRequest
from app.site_login.service import SiteLoginService
from app.site_login.sunnypt import SunnyPtLoginAdapter


class _AllowAllGuard:
    """测试用出站地址保护替身。"""

    def ensure_allowed(self, _: str) -> None:
        """允许测试构造的公网地址。"""


class _FakePage:
    """按顺序返回页面脚本结果。"""

    def __init__(self, results: list[dict]):
        self.results = results
        self.evaluate_calls = 0

    def set_default_timeout(self, _: int) -> None:
        """记录超时不需要额外行为。"""

    def goto(self, *_: object, **__: object) -> None:
        """模拟登录页导航。"""

    def evaluate(self, *_: object) -> dict:
        """返回下一条预设脚本结果。"""
        result = self.results[self.evaluate_calls]
        self.evaluate_calls += 1
        return result


class _FakeContext:
    """提供 SunnyPT 适配器所需的最小浏览器上下文。"""

    def __init__(self, page: _FakePage):
        self.page = page
        self.closed = False

    def route(self, *_: object) -> None:
        """忽略测试中的路由注册。"""

    def new_page(self) -> _FakePage:
        """返回共享测试页面。"""
        return self.page

    def close(self) -> None:
        """记录上下文已关闭。"""
        self.closed = True


def _request(password: str = "secret") -> SiteLoginRequest:
    """构造不包含真实凭据的 SunnyPT 登录请求。"""
    return SiteLoginRequest(
        request_id="request-1",
        site_key="sunnypt",
        account_id=1,
        site_url="https://sunnypt.top",
        credentials={"username": "tester", "password": password},
    )


def test_sunnypt_login_runs_three_stage_flow_once():
    """SunnyPT 登录应只执行一次三阶段浏览器流程并返回会话 Token。"""
    page = _FakePage(
        [
            {"status": 200, "body": json.dumps({"code": 0, "data": {"token": "first-token"}})},
            {"status": 200, "body": json.dumps({"data": True})},
            {"status": 200, "body": json.dumps({"data": {"accessToken": "final-token"}})},
        ],
    )
    context = _FakeContext(page)
    adapter = SunnyPtLoginAdapter(
        Settings(),
        _AllowAllGuard(),
        context_factory=lambda **_: context,
    )

    result = adapter.login(_request())

    assert result.success is True
    assert result.credential is not None
    assert result.credential.bearer_token == "final-token"
    assert page.evaluate_calls == 3
    assert context.closed is True


def test_sunnypt_login_failure_does_not_retry():
    """SunnyPT 登录接口失败后不得重复提交账号密码。"""
    page = _FakePage(
        [{"status": 401, "body": json.dumps({"code": 1, "msg": "invalid credentials"})}],
    )
    context = _FakeContext(page)
    adapter = SunnyPtLoginAdapter(
        Settings(),
        _AllowAllGuard(),
        context_factory=lambda **_: context,
    )

    result = adapter.login(_request())

    assert result.success is False
    assert page.evaluate_calls == 1
    assert context.closed is True


def test_login_service_rejects_unregistered_site():
    """未注册站点不得落入通用登录实现。"""
    service = SiteLoginService(Settings(), adapters=[])

    try:
        service.login(
            SiteLoginRequest(
                request_id="request-2",
                site_key="unknown",
                account_id=1,
                site_url="https://example.com",
                credentials={},
            ),
        )
    except ValueError as error:
        assert "未配置自动登录适配器" in str(error)
    else:
        raise AssertionError("未注册站点应拒绝登录")


def test_sunnypt_adapter_rejects_non_sunnypt_target():
    """SunnyPT 凭据不得被发送到配置错误的第三方域名。"""
    adapter = SunnyPtLoginAdapter(
        Settings(),
        _AllowAllGuard(),
        context_factory=lambda **_: None,
    )

    with pytest.raises(ValueError, match="站点地址无效"):
        adapter.login(
            SiteLoginRequest(
                request_id="request-3",
                site_key="sunnypt",
                account_id=1,
                site_url="https://example.com",
                credentials={"username": "tester", "password": "secret"},
            ),
        )
