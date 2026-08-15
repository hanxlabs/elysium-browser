"""浏览器结果处理测试。"""

import pytest

from app.browser import CloakBrowserFetcher
from app.config import Settings
from app.models import FetchPageRequest, FetchResourceRequest

from app.browser_shared import (
    cookie_header_for_host,
    filter_same_site_cookies,
    is_challenge_page,
    truncate_html_bytes,
)


def test_truncates_html_by_utf8_bytes():
    """响应 HTML 必须按字节大小限制。"""
    max_bytes = 1024
    html, truncated = truncate_html_bytes("你好世界" * 200, max_bytes)

    assert truncated is True
    assert len(html.encode("utf-8")) <= max_bytes


def test_returns_only_same_site_cookies():
    """第三方 Cookie 不得返回给调用方。"""
    cookies = filter_same_site_cookies(
        [
            {"name": "session", "value": "a", "domain": ".example.org", "path": "/"},
            {"name": "third", "value": "b", "domain": "cdn.example.net", "path": "/"},
        ],
        "https://pt.example.org/index.php",
    )

    assert [cookie.name for cookie in cookies] == ["session"]


def test_detects_challenge_marker():
    """验证页只能被标记，不应触发额外行为。"""
    assert is_challenge_page("<div>安全验证</div>") is True
    assert is_challenge_page("<html>ordinary content</html>") is False


def test_cookie_header_keeps_only_target_domain():
    """登录响应 Cookie 头不得混入第三方上下文 Cookie。"""
    header = cookie_header_for_host(
        [
            {"name": "session", "value": "abc", "domain": ".example.org"},
            {"name": "tracking", "value": "ignored", "domain": "example.net"},
        ],
        "pt.example.org",
    )

    assert header == "session=abc"


def test_fetch_request_bounds_post_load_settle_time():
    """中间验证页等待时间必须受 API 模型限制。"""
    request = FetchPageRequest(
        request_id="sign-1",
        site_key="audiences",
        account_id=1,
        url="https://audiences.me/attendance.php",
        settle_seconds=15,
    )
    assert request.settle_seconds == 15

    with pytest.raises(ValueError):
        FetchPageRequest(
            request_id="sign-2",
            site_key="audiences",
            account_id=1,
            url="https://audiences.me/attendance.php",
            settle_seconds=31,
        )


def test_resource_request_supports_rendered_get_and_api_post():
    rendered = FetchResourceRequest(
        request_id="data-html-1",
        site_key="audiences",
        account_id=1,
        url="https://audiences.me/userdetails.php?id=1",
        render_page=True,
        settle_seconds=5,
    )
    api = FetchResourceRequest(
        request_id="data-api-1",
        site_key="mteam",
        account_id=1,
        url="https://api.m-team.cc/api/member/profile",
        method="POST",
        headers={"Content-Type": "application/json", "x-api-key": "token"},
        body="{}",
    )

    assert rendered.render_page is True
    assert api.method == "POST"


def test_resource_request_rejects_cookie_header():
    with pytest.raises(ValueError):
        FetchResourceRequest(
            request_id="data-bad-header",
            site_key="audiences",
            account_id=1,
            url="https://audiences.me/",
            headers={"Cookie": "secret=value"},
        )


def test_resource_fetch_uses_browser_context_request_and_returns_cookie(monkeypatch):
    class FakeResponse:
        status = 200
        url = "https://api.m-team.cc/api/member/profile"

        @staticmethod
        def text():
            return '{"code":"0"}'

    class FakeRequestContext:
        def __init__(self):
            self.arguments = None

        def fetch(self, url, **kwargs):
            self.arguments = (url, kwargs)
            return FakeResponse()

    class FakeContext:
        def __init__(self):
            self.request = FakeRequestContext()
            self.closed = False

        def route(self, *_args):
            return None

        def add_cookies(self, _cookies):
            return None

        def cookies(self):
            return [{"name": "session", "value": "fresh", "domain": "api.m-team.cc", "path": "/"}]

        def close(self):
            self.closed = True

    context = FakeContext()
    monkeypatch.setattr("app.browser.launch_isolated_context", lambda _settings: context)
    fetcher = CloakBrowserFetcher(Settings())
    request = FetchResourceRequest(
        request_id="data-api-fake",
        site_key="mteam",
        account_id=1,
        url="https://api.m-team.cc/api/member/profile",
        method="POST",
        body="{}",
    )

    result = fetcher._fetch_resource_with_browser(request, 10)

    assert result.html == '{"code":"0"}'
    assert result.cookies[0].value == "fresh"
    assert context.request.arguments[1]["max_redirects"] == 0
    assert context.closed is True


def test_resource_fetch_rejects_rendered_post(monkeypatch):
    class FakeContext:
        def route(self, *_args):
            return None

        def close(self):
            return None

    monkeypatch.setattr("app.browser.launch_isolated_context", lambda _settings: FakeContext())
    fetcher = CloakBrowserFetcher(Settings())
    request = FetchResourceRequest(
        request_id="data-render-post",
        site_key="audiences",
        account_id=1,
        url="https://audiences.me/",
        method="POST",
        render_page=True,
    )

    with pytest.raises(ValueError, match="只支持 GET"):
        fetcher._fetch_resource_with_browser(request, 10)
