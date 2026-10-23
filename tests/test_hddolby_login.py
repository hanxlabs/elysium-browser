"""HDDolby 域名迁移的离线登录回归测试。"""

from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.models import SiteLoginRequest
from app.site_login.hddolby import DEFINITION, HddolbyLoginAdapter
from app.site_login.service import SiteLoginService


@pytest.mark.parametrize("host", (
    "www.uhddolby.com", "uhddolby.com", "www.hddolby.com", "hddolby.com",
))
def test_hddolby_login_uses_new_host_for_credentials_and_cookies(host: str):
    """旧配置与新裸域都应直接在新 www 域完成登录并提取 Cookie。"""
    guard = MagicMock()
    recognizer = MagicMock()
    recognizer.recognize.return_value = "abcd"
    page = MagicMock()
    page.url = "https://www.uhddolby.com/login.php"
    page.content.return_value = '<form action="takelogin.php" method="post"></form>'
    page.evaluate.side_effect = lambda script: "fixture-agent" if "userAgent" in script else "zh-CN"
    context = MagicMock()
    context.new_page.return_value = page
    context.cookies.side_effect = lambda origin: (
        [
            {"name": "session", "value": "fixture-session", "domain": "www.uhddolby.com"},
            {"name": "unrelated", "value": "ignored", "domain": "www.hddolby.com"},
        ]
        if origin == "https://www.uhddolby.com" else []
    )
    form = MagicMock()
    form.count.return_value = 1
    form.get_attribute.return_value = "https://www.uhddolby.com/takelogin.php"
    fields = {}

    def field(selector):
        if selector not in fields:
            fields[selector] = MagicMock()
        return fields[selector]

    form.locator.side_effect = field
    page.locator.return_value = form
    form.first = form

    def submit():
        page.url = "https://www.uhddolby.com/index.php"
        page.content.return_value = '<a href="logout.php">退出</a>'

    field(DEFINITION.submit_selector).first.click.side_effect = submit
    adapter = HddolbyLoginAdapter(
        Settings(), guard, recognizer=recognizer, context_factory=lambda **_: context,
    )

    result = adapter.login(SiteLoginRequest(
        request_id="hddolby-migration",
        site_key="hddolby",
        account_id=1,
        site_url=f"https://{host}/",
        credentials={"username": "fixture-user", "password": "fixture-password"},
    ))

    assert result.success is True
    page.goto.assert_called_once_with(
        "https://www.uhddolby.com/login.php", wait_until="load", timeout=45_000,
    )
    field('input[name="username"]').fill.assert_called_once_with("fixture-user")
    field('input[type="password"]').fill.assert_called_once_with("fixture-password")
    field(adapter._CAPTCHA_INPUT_SELECTOR).fill.assert_called_once_with("abcd")
    recognizer.recognize.assert_called_once()
    context.cookies.assert_called_once_with("https://www.uhddolby.com")
    assert result.credential is not None
    assert result.credential.cookie == "session=fixture-session"
    assert result.credential.headers["Referer"] == "https://www.uhddolby.com/"
    context.close.assert_called_once()


@pytest.mark.parametrize("url", (
    "http://www.uhddolby.com/",
    "https://www.uhddolby.com.evil.example/",
    "https://notuhddolby.com/",
    "https://t.uhddolby.com/",
    "https://www.uhddolby.com:8443/",
    "https://user:password@www.uhddolby.com/",
    "https://www.hddolby.com.evil.example/",
    "https://www.hddolby.com:8443/",
))
def test_hddolby_rejects_invalid_origin_before_canonicalizing(url: str):
    with pytest.raises(ValueError, match="站点地址无效"):
        HddolbyLoginAdapter._build_origin(url, DEFINITION)


def test_hddolby_new_host_allowed_for_resources_navigation_and_form_submission():
    adapter = HddolbyLoginAdapter(Settings(), MagicMock())
    context = MagicMock()
    blocked = []
    adapter._install_multi_request_guard(context, DEFINITION, blocked)
    guard_route = context.route.call_args.args[1]

    for host in ("www.uhddolby.com", "uhddolby.com"):
        url = f"https://{host}/takelogin.php"
        adapter._ensure_allowed_page_url(url, DEFINITION)
        adapter._ensure_form_action(url, "https://www.uhddolby.com/login.php", "", DEFINITION)
        route = MagicMock()
        route.request.url = url
        guard_route(route)
        route.continue_.assert_called_once()
        route.abort.assert_not_called()

    for host in ("www.uhddolby.com.evil.example", "www.hddolby.com"):
        route = MagicMock()
        route.request.url = f"https://{host}/takelogin.php"
        guard_route(route)
        route.abort.assert_called_once()
        with pytest.raises(ValueError, match="提交地址无效"):
            adapter._ensure_form_action(route.request.url, "https://www.uhddolby.com/login.php", "", DEFINITION)
        with pytest.raises(ValueError, match="离开允许域名"):
            adapter._ensure_allowed_page_url(route.request.url, DEFINITION)
    assert len(blocked) == 2


def test_hddolby_adapter_remains_registered_with_existing_login_protocol():
    assert any(adapter.supports("hddolby") for adapter in SiteLoginService(Settings())._adapters)
    assert DEFINITION.default_host == "www.uhddolby.com"
    assert DEFINITION.image_captcha is True
    assert DEFINITION.two_factor_field == "2fa"
