"""浏览器结果处理测试。"""

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
