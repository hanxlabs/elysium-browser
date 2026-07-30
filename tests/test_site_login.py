"""站点登录适配器测试。"""

import json

import pytest

from app.config import Settings
from app.models import SiteLoginRequest
from app.site_login.btschool import BtschoolLoginAdapter
from app.site_login.crabpt import CrabptLoginAdapter
from app.site_login.ptcafe import PtCafeLoginAdapter
from app.site_login.pter import PterLoginAdapter
from app.site_login.pttime import DEFINITION as PTTIME_DEFINITION
from app.site_login.pttime import PttimeLoginAdapter
from app.site_login.service import SiteLoginService
from app.site_login.sunnypt import SunnyPtLoginAdapter
from app.site_login.vclib import DEFINITION as VCLIB_DEFINITION
from app.site_login.vclib import VclibLoginAdapter
from app.totp import generate_totp


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


def test_vclib_adapter_is_registered_with_expected_login_protocol():
    """VC-Lib 应注册 OCR、2FA 和 challenge 登录所需的准确页面协议。"""
    service = SiteLoginService(Settings())

    assert any(adapter.supports("vclib") for adapter in service._adapters)
    assert VCLIB_DEFINITION.hosts == ("pt.vclib.online",)
    assert VCLIB_DEFINITION.form_selector == "#login-form"
    assert VCLIB_DEFINITION.submit_selector == "#submit-btn"
    assert VCLIB_DEFINITION.image_captcha is True
    assert VCLIB_DEFINITION.challenge is True
    assert VCLIB_DEFINITION.two_factor_field == "two_step_code"


def test_vclib_adapter_rejects_non_vclib_target():
    """VC-Lib 账号、密码及2FA密钥不得发送到第三方域名。"""
    adapter = VclibLoginAdapter(
        Settings(),
        _AllowAllGuard(),
        recognizer=lambda _: "abcd",
        context_factory=lambda **_: None,
    )

    with pytest.raises(ValueError, match="站点地址无效"):
        adapter.login(
            SiteLoginRequest(
                request_id="request-vclib",
                site_key="vclib",
                account_id=1,
                site_url="https://example.com",
                credentials={
                    "username": "tester",
                    "password": "secret",
                    "twoFactorSecret": "JBSWY3DPEHPK3PXP",
                },
            ),
        )


def test_browser_gateway_rejects_depiler_only_sites():
    """仅支持 Depiler 的站点不得落入 Browser 登录实现。"""
    service = SiteLoginService(Settings())
    site_keys = (
        "audiences",
        "pter",
        "zmpt",
        "qingwa",
        "ubits",
        "piggo",
        "52movie",
        "luckpt",
        "hitpt",
    )

    for site_key in site_keys:
        with pytest.raises(ValueError, match="未配置自动登录适配器"):
            service.login(
                SiteLoginRequest(
                    request_id=f"request-{site_key}",
                    site_key=site_key,
                    account_id=1,
                    site_url="https://example.com",
                    credentials={},
                ),
            )


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


def test_btschool_classifies_login_results():
    """BTSCHOOL 应区分验证码、凭据错误和已登录首页。"""
    assert BtschoolLoginAdapter._classify_result(
        "https://pt.btschool.club/takelogin.php",
        "<h2>失败</h2><td>图片代码无效！图片代码已被清除！</td>",
    ) == "captcha_error"
    assert BtschoolLoginAdapter._classify_result(
        "https://pt.btschool.club/takelogin.php",
        "<h2>登录失败！</h2><td>用户名或密码不正确！或者你还没有通过验证</td>",
    ) == "credential_error"
    assert BtschoolLoginAdapter._classify_result(
        "https://pt.btschool.club/index.php",
        '<div>欢迎回来</div><a href="logout.php">退出</a>',
    ) == "success"
    assert BtschoolLoginAdapter._classify_result(
        "https://pt.btschool.club/login.php",
        "<title>BTSCHOOL :: 首页</title>"
        '<div>欢迎回来 <a href="userdetails.php?id=1">tester</a></div>'
        '<a href="logout.php">退出</a>',
    ) == "success"


def test_btschool_adapter_rejects_non_btschool_target():
    """BTSCHOOL 凭据不得发送到第三方域名。"""
    adapter = BtschoolLoginAdapter(
        Settings(),
        _AllowAllGuard(),
        recognizer=lambda _: "abcd",
        context_factory=lambda **_: None,
    )

    with pytest.raises(ValueError, match="站点地址无效"):
        adapter.login(
            SiteLoginRequest(
                request_id="request-4",
                site_key="btschool",
                account_id=0,
                site_url="https://example.com",
                credentials={"username": "tester", "password": "secret"},
            ),
        )


def test_crabpt_classifies_login_results():
    """蟹黄堡应复用 NexusPHP 验证码错误、凭据错误和首页成功判定。"""
    assert CrabptLoginAdapter._classify_result(
        "https://crabpt.vip/takelogin.php",
        "<h2>失败</h2><td>图片代码无效！图片代码已被清除！</td>",
    ) == "captcha_error"
    assert CrabptLoginAdapter._classify_result(
        "https://crabpt.vip/takelogin.php",
        "<h2>登录失败！</h2><td>用户名或密码不正确！或者你还没有通过验证</td>",
    ) == "credential_error"
    assert CrabptLoginAdapter._classify_result(
        "https://crabpt.vip/login.php",
        "<title>蟹黄堡 :: 首页 - Powered by NexusPHP</title>"
        '<div>欢迎回来 <a href="userdetails.php?id=1">tester</a></div>'
        '<a href="logout.php">退出</a>',
    ) == "success"


def test_crabpt_adapter_rejects_non_crabpt_target():
    """蟹黄堡账号凭据不得发送到第三方域名。"""
    adapter = CrabptLoginAdapter(
        Settings(),
        _AllowAllGuard(),
        recognizer=lambda _: "abcd",
        context_factory=lambda **_: None,
    )

    with pytest.raises(ValueError, match="站点地址无效"):
        adapter.login(
            SiteLoginRequest(
                request_id="request-5",
                site_key="crabpt",
                account_id=0,
                site_url="https://example.com",
                credentials={"username": "tester", "password": "secret"},
            ),
        )


def test_ptcafe_classifies_login_results():
    """咖啡应复用 NexusPHP 验证码错误、凭据错误和首页成功判定。"""
    assert PtCafeLoginAdapter._classify_result(
        "https://ptcafe.club/takelogin.php",
        "<h2>失败</h2><td>图片代码无效！图片代码已被清除！</td>",
    ) == "captcha_error"
    assert PtCafeLoginAdapter._classify_result(
        "https://ptcafe.club/takelogin.php",
        "<h2>登录失败！</h2><td>用户名或密码不正确！或者你还没有通过验证</td>",
    ) == "credential_error"
    assert PtCafeLoginAdapter._classify_result(
        "https://ptcafe.club/login.php",
        "<title>咖啡 :: 首页 - Powered by NexusPHP</title>"
        '<div>欢迎回来 <a href="userdetails.php?id=1">tester</a></div>'
        '<a href="logout.php">退出</a>',
    ) == "success"


def test_ptcafe_adapter_rejects_non_ptcafe_target():
    """咖啡账号凭据不得发送到第三方域名。"""
    adapter = PtCafeLoginAdapter(
        Settings(),
        _AllowAllGuard(),
        recognizer=lambda _: "abcd",
        context_factory=lambda **_: None,
    )

    with pytest.raises(ValueError, match="站点地址无效"):
        adapter.login(
            SiteLoginRequest(
                request_id="request-6",
                site_key="ptcafe",
                account_id=0,
                site_url="https://example.com",
                credentials={"username": "tester", "password": "secret"},
            ),
        )


def test_totp_matches_rfc_6238_sha1_vector():
    """六位 TOTP 应匹配 RFC 6238 SHA-1 测试向量的后六位。"""
    assert generate_totp(
        "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
        timestamp=59,
    ) == "287082"


def test_pter_classifies_login_results():
    """猫站应识别 Turnstile 失败、账号失败和已登录首页。"""
    assert PterLoginAdapter._classify_result(
        "https://pterclub.net/takelogin.php",
        "<h2>Are you a bot?</h2><td>Verify not success! 验证未通过!</td>",
    ) == "turnstile_error"
    assert PterLoginAdapter._classify_result(
        "https://pterclub.net/takelogin.php",
        "<h2>登录失败！</h2><td>用户名或密码不正确！或者你还没有通过验证</td>",
    ) == "credential_error"
    assert PterLoginAdapter._classify_result(
        "https://pterclub.net/login.php",
        "<title>ＰＴ之友俱乐部 :: 首页 PTerClub</title>"
        '<div>欢迎回来 <a href="userdetails.php?id=1">tester</a></div>'
        '<a href="#" data-url="logout.php">退出</a>',
    ) == "success"


def test_pter_adapter_rejects_non_pter_target():
    """猫站账号、密码及2FA密钥不得发送到第三方域名。"""
    adapter = PterLoginAdapter(
        Settings(),
        _AllowAllGuard(),
        context_factory=lambda **_: None,
    )

    with pytest.raises(ValueError, match="站点地址无效"):
        adapter.login(
            SiteLoginRequest(
                request_id="request-7",
                site_key="pter",
                account_id=0,
                site_url="https://example.com",
                credentials={
                    "username": "tester",
                    "password": "secret",
                    "twoFactorSecret": "JBSWY3DPEHPK3PXP",
                },
            ),
        )


class _AtomicSubmitPage:
    """记录动态表单原子提交传入页面的参数。"""

    def __init__(self, result: dict):
        self.result = result
        self.arguments: dict | None = None

    def evaluate(self, _: str, arguments: dict) -> dict:
        self.arguments = arguments
        return self.result


def test_pttime_uses_atomic_dom_submit_for_dynamic_login_form():
    """PTTime 应在同一次 DOM 操作中重新定位、填写并提交动态表单。"""
    page = _AtomicSubmitPage({"ok": True})

    PttimeLoginAdapter._atomic_dom_submit(
        page,
        PTTIME_DEFINITION,
        "https://www.pttime.org",
        "tester",
        "secret",
    )

    assert PTTIME_DEFINITION.atomic_dom_submit is True
    assert page.arguments == {
        "formSelector": 'form[action$="takelogin.php"][method="post"]',
        "submitSelector": 'button[type="submit"]',
        "expectedOrigin": "https://www.pttime.org",
        "username": "tester",
        "password": "secret",
    }


def test_pttime_atomic_dom_submit_rejects_changed_form_action():
    """PTTime 动态表单提交前仍须校验提交目标域名。"""
    page = _AtomicSubmitPage({"ok": False, "reason": "action-origin-invalid"})

    with pytest.raises(ValueError, match="登录表单提交地址无效"):
        PttimeLoginAdapter._atomic_dom_submit(
            page,
            PTTIME_DEFINITION,
            "https://www.pttime.org",
            "tester",
            "secret",
        )
