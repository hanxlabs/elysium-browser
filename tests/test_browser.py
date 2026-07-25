"""浏览器结果处理测试。"""

from app.browser import CloakBrowserFetcher, _is_challenge_page
from app.config import Settings


def test_truncates_html_by_utf8_bytes():
    """响应 HTML 必须按字节大小限制。"""
    fetcher = CloakBrowserFetcher(Settings(max_html_bytes=1024))

    html, truncated = fetcher._truncate_html("你好世界" * 200)

    assert truncated is True
    assert len(html.encode("utf-8")) <= 1024


def test_returns_only_same_site_cookies():
    """第三方 Cookie 不得返回给调用方。"""
    fetcher = CloakBrowserFetcher(Settings())
    cookies = fetcher._same_site_cookies(
        [
            {"name": "session", "value": "a", "domain": ".example.org", "path": "/"},
            {"name": "third", "value": "b", "domain": "cdn.example.net", "path": "/"},
        ],
        "https://pt.example.org/index.php",
    )

    assert [cookie.name for cookie in cookies] == ["session"]


def test_detects_challenge_marker():
    """验证页只能被标记，不应触发额外行为。"""
    assert _is_challenge_page("<div>安全验证</div>") is True
    assert _is_challenge_page("<html>ordinary content</html>") is False
