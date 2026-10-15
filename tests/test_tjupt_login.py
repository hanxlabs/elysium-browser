"""北洋园原生登录和后续两步验证的离线回归测试。"""

from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
import re
from urllib.parse import urljoin

import pytest

from app.config import Settings
from app.models import SiteLoginRequest
from app.site_login.shared import ConfiguredSiteLoginAdapter
from app.site_login.tjupt import DEFINITION, TjuptLoginAdapter


_USERNAME = "fixture-login-user"
_PASSWORD = "fixture-login-password"
_SECRET = "JBSWY3DPEHPK3PXP"
_CODE = "654321"
_HIDDEN_USERNAME = "fixture-server-user"
_HIDDEN_PASSWORD = "fixture-server-password"

_LOGIN_HTML = """
<html><title>北洋园登录</title><body>
<form action="takelogin.php" method="post">
  <input name="username" type="text">
  <input name="password" type="password">
  <input name="logout" type="checkbox" value="yes" checked>
  <input type="button" value="登录" onclick="submitLogin()">
</form></body></html>
"""


def _verify_html(action: str = "takelogin.php", *, code: str = "") -> str:
    """模拟服务器返回的独立表单，包括必须保留的隐藏值。"""
    return f"""
    <html><title>两步验证</title><body>
    <h1>登录两步验证</h1><p>注意：连续 <strong>10</strong> 次错误尝试会导致你的IP地址被禁用！你还有10次尝试机会。</p>
    <form name="verifyform" action="{escape(action, quote=True)}" method="post">
      <input name="username" type="hidden" value="{_HIDDEN_USERNAME}">
      <input name="password" type="hidden" value="{_HIDDEN_PASSWORD}">
      <input name="logout" type="hidden" value="yes">
      <input name="verify_code" type="text" value="{escape(code, quote=True)}">
      <input type="submit" value="验证">
    </form></body></html>
    """


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str]
    parent: "_Element | None" = None
    children: list["_Element"] = field(default_factory=list)

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()


class _FormParser(HTMLParser):
    """保留输入属性和父子关系，使测试实际按选择器填写和提交表单。"""

    def __init__(self, html: str):
        super().__init__()
        self.root = _Element("document", {})
        self.current = self.root
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = _Element(tag, {key: value or "" for key, value in attrs}, self.current)
        self.current.children.append(element)
        if tag not in {"input", "br", "img", "meta", "link", "hr"}:
            self.current = element

    def handle_endtag(self, tag: str) -> None:
        ancestor = self.current
        while ancestor.parent is not None:
            if ancestor.tag == tag:
                self.current = ancestor.parent
                return
            ancestor = ancestor.parent


def _matches(element: _Element, selector: str) -> bool:
    """支持适配器使用的标签、ID、属性等值和后缀选择器。"""
    tag = re.match(r"^[A-Za-z][\w-]*", selector)
    if tag and element.tag != tag.group():
        return False
    identifier = re.search(r"#([\w-]+)", selector)
    if identifier and element.attrs.get("id") != identifier.group(1):
        return False
    for name, operator, value in re.findall(r'\[([\w-]+)(\$=|=)"([^\"]*)"\]', selector):
        actual = element.attrs.get(name)
        if actual is None or (operator == "=" and actual != value):
            return False
        if operator == "$=" and not actual.endswith(value):
            return False
    return True


class _FakeLocator:
    def __init__(self, page: "_FakePage", elements: list[_Element]):
        self.page = page
        self.elements = elements

    @property
    def first(self) -> "_FakeLocator":
        return _FakeLocator(self.page, self.elements[:1])

    def count(self) -> int:
        return len(self.elements)

    def locator(self, selector: str) -> "_FakeLocator":
        return _FakeLocator(self.page, [
            child for element in self.elements for child in element.descendants()
            if any(_matches(child, item.strip()) for item in selector.split(","))
        ])

    def get_attribute(self, name: str) -> str | None:
        assert len(self.elements) == 1
        return self.elements[0].attrs.get(name)

    def fill(self, value: str) -> None:
        assert len(self.elements) == 1, "填写的字段必须存在且唯一"
        element = self.elements[0]
        assert element.tag == "input" and element.attrs.get("type") != "hidden"
        if self.page.fail_verify_fill and element.attrs.get("name") == "verify_code":
            raise RuntimeError(f"fixture Locator.fill failed with value={value}")
        element.attrs["value"] = value
        self.page.fills.append((self.page.stage, element.attrs.get("name"), value))

    def click(self) -> None:
        assert len(self.elements) == 1
        self.page.submit(self.elements[0])


class _FakePage:
    def __init__(
        self, *, two_factor: bool = True, result: str = "success",
        action: str = "takelogin.php", fail_verify_fill: bool = False,
    ):
        self.two_factor = two_factor
        self.result = result
        self.action = action
        self.fail_verify_fill = fail_verify_fill
        self.stage = "unopened"
        self.url = "about:blank"
        self.html = ""
        self.root = _Element("document", {})
        self.fills: list[tuple[str, str, str]] = []
        self.submissions: list[dict] = []
        self.navigations: list[str] = []
        self.events: dict[str, object] = {}
        self.frames: list[object] = []
        self.context: _FakeContext | None = None

    def _set_html(self, stage: str, html: str, path: str) -> None:
        self.stage = stage
        self.html = html
        self.root = _FormParser(html).root
        self.url = urljoin("https://tjupt.org/", path)

    def set_default_timeout(self, _: int) -> None:
        pass

    def goto(self, url: str, **_: object) -> None:
        self.navigations.append(url)
        self._set_html("login", _LOGIN_HTML, "/login.php")

    def on(self, event: str, callback: object) -> None:
        self.events[event] = callback

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, [self.root]).locator(selector)

    def content(self) -> str:
        return self.html

    def title(self) -> str:
        return "北洋园测试页"

    def evaluate(self, script: str, *_: object) -> object:
        if script == "() => navigator.userAgent":
            return "fixture-agent"
        if script == "() => navigator.language":
            return "zh-CN"
        if "responseFieldCount" in script:
            return {"responseFieldCount": 0, "responseLengths": []}
        raise AssertionError("测试只允许原生按钮提交，不允许脚本重建或提交表单")

    def submit(self, button: _Element) -> None:
        form = button.parent
        assert form is not None and form.tag == "form"
        fields = {
            element.attrs["name"]: element.attrs.get("value", "")
            for element in form.descendants()
            if element.tag == "input" and "name" in element.attrs
            and (element.attrs.get("type") != "checkbox" or "checked" in element.attrs)
        }
        self.submissions.append({
            "stage": self.stage,
            "action": form.attrs.get("action"),
            "fields": fields,
            "button_type": button.attrs.get("type"),
            "context": self.context,
        })
        if self.stage == "login" and self.two_factor:
            self._set_html("verify", _verify_html(self.action), "/takelogin.php")
        elif self.stage == "verify" and self.result != "success":
            html = _verify_html(self.action, code=fields.get("verify_code", ""))
            if self.result == "error":
                html += "<p>两步验证码错误</p>"
            self._set_html("verify-result", html, "/takelogin.php")
        else:
            self._set_html("success", '<a href="logout.php">退出</a>', "/index.php")


class _FakeContext:
    def __init__(self, page: _FakePage):
        self.page = page
        page.context = self
        self.closed = False
        self.page_count = 0

    def route(self, *_: object) -> None:
        pass

    def new_page(self) -> _FakePage:
        self.page_count += 1
        return self.page

    def cookies(self, *_: object) -> list[dict]:
        return [{"name": "session", "value": "fixture-session", "domain": "tjupt.org"}]

    def close(self) -> None:
        self.closed = True


class _AllowAllGuard:
    def ensure_allowed(self, _: str) -> None:
        pass


def _setup(page: _FakePage) -> tuple[TjuptLoginAdapter, _FakeContext]:
    context = _FakeContext(page)
    adapter = TjuptLoginAdapter(
        Settings(), _AllowAllGuard(), recognizer=object(), context_factory=lambda **_: context,
    )
    return adapter, context


def _request(secret: str | None = _SECRET) -> SiteLoginRequest:
    credentials = {"username": _USERNAME, "password": _PASSWORD}
    if secret is not None:
        credentials["twoFactorSecret"] = secret
    return SiteLoginRequest(
        request_id="request-tjupt-fixture", site_key="tjupt", account_id=1,
        site_url="https://tjupt.org", credentials=credentials, timeout_seconds=1,
    )


def _patch_totp(monkeypatch, page: _FakePage) -> list[str]:
    calls: list[str] = []

    def generate(secret: str, *, minimum_validity_seconds: int) -> str:
        assert page.stage == "verify"
        assert len(page.submissions) == 1
        assert minimum_validity_seconds >= 5
        calls.append(secret)
        return _CODE

    monkeypatch.setattr("app.site_login.shared.generate_totp", generate)
    return calls


def test_tjupt_two_factor_uses_server_form_and_same_context(monkeypatch):
    page = _FakePage()
    adapter, context = _setup(page)
    calls = _patch_totp(monkeypatch, page)

    result = adapter.login(_request())

    assert result.success is True
    assert result.credential.cookie == "session=fixture-session"
    assert calls == [_SECRET]
    assert page.navigations == ["https://tjupt.org/login.php"]
    assert context.page_count == 1 and context.closed is True
    assert [item["button_type"] for item in page.submissions] == ["button", "submit"]
    assert all(item["context"] is context for item in page.submissions)
    assert page.submissions[0]["fields"] == {
        "username": _USERNAME, "password": _PASSWORD, "logout": "yes",
    }
    assert page.submissions[1]["fields"] == {
        "username": _HIDDEN_USERNAME, "password": _HIDDEN_PASSWORD,
        "logout": "yes", "verify_code": _CODE,
    }
    assert page.fills == [
        ("login", "username", _USERNAME), ("login", "password", _PASSWORD),
        ("verify", "verify_code", _CODE),
    ]


@pytest.mark.parametrize("secret", (None, _SECRET, "invalid!!!"))
def test_tjupt_without_challenge_does_not_generate_or_fill_otp(monkeypatch, secret):
    page = _FakePage(two_factor=False)
    adapter, context = _setup(page)

    def unexpected_totp(*_: object, **__: object) -> str:
        pytest.fail("未出现二步验证表单时不应生成验证码")

    monkeypatch.setattr("app.site_login.shared.generate_totp", unexpected_totp)
    result = adapter.login(_request(secret))

    assert result.success is True
    assert len(page.submissions) == 1
    assert all(name != "verify_code" for _, name, _ in page.fills)
    assert context.closed is True


def test_tjupt_missing_two_factor_secret_returns_actionable_failure(monkeypatch):
    page = _FakePage()
    adapter, context = _setup(page)
    calls = _patch_totp(monkeypatch, page)

    result = adapter.login(_request(None))

    assert result.success is False
    assert result.message == "北洋园两步验证需要在账号中配置2FA密钥"
    assert len(page.submissions) == 1 and calls == []
    assert context.closed is True


def test_tjupt_invalid_two_factor_secret_is_not_submitted():
    page = _FakePage()
    adapter, context = _setup(page)

    result = adapter.login(_request("invalid!!!"))

    assert result.success is False
    assert result.message == "北洋园2FA密钥格式无效"
    assert len(page.submissions) == 1
    assert all(name != "verify_code" for _, name, _ in page.fills)
    assert context.closed is True


@pytest.mark.parametrize("outcome", ("error", "repeat"))
def test_tjupt_rejected_or_repeated_challenge_is_not_retried(monkeypatch, outcome):
    page = _FakePage(result=outcome)
    adapter, context = _setup(page)
    calls = _patch_totp(monkeypatch, page)

    result = adapter.login(_request())

    assert result.success is False
    assert result.credential is None
    if outcome == "error":
        assert "2FA验证码错误" in result.message
    assert calls == [_SECRET]
    assert len(page.submissions) == 2
    assert len(page.navigations) == 1 and context.page_count == 1
    assert context.closed is True


@pytest.mark.parametrize("action", ("https://example.net/takelogin.php", "http://tjupt.org/takelogin.php"))
def test_tjupt_two_factor_rejects_unsafe_action_before_generating_otp(monkeypatch, action):
    page = _FakePage(action=action)
    adapter, context = _setup(page)
    calls = _patch_totp(monkeypatch, page)

    with pytest.raises(ValueError, match="登录表单提交地址无效"):
        adapter.login(_request())

    assert calls == []
    assert len(page.submissions) == 1
    assert context.closed is True


@pytest.mark.parametrize("field", (
    '<input name="verify_code" value="987654">',
    '<input value="987654" type="text" name="verify_code">',
))
def test_tjupt_verify_code_is_redacted_even_without_generated_secret(field):
    assert "987654" not in ConfiguredSiteLoginAdapter._redact(field, [])
    assert "[REDACTED]" in ConfiguredSiteLoginAdapter._redact(field, [])


def test_tjupt_failed_two_factor_diagnostics_redact_credentials(monkeypatch, caplog):
    page = _FakePage(result="error")
    adapter, _ = _setup(page)
    _patch_totp(monkeypatch, page)

    result = adapter.login(_request())

    assert result.success is False
    assert "Browser脱敏页面HTML" in caplog.text
    for value in (_USERNAME, _PASSWORD, _SECRET, _CODE, _HIDDEN_PASSWORD):
        assert value not in caplog.text


def test_tjupt_two_factor_fill_exception_redacts_sensitive_traceback(monkeypatch, caplog):
    page = _FakePage(fail_verify_fill=True)
    adapter, context = _setup(page)
    calls = _patch_totp(monkeypatch, page)

    result = adapter.login(_request())

    assert result.success is False
    assert result.credential is None
    assert "RuntimeError" in result.message
    assert calls == [_SECRET] and len(page.submissions) == 1
    assert context.closed is True
    assert "RuntimeError" in caplog.text
    for value in (_USERNAME, _PASSWORD, _SECRET, _CODE, _HIDDEN_PASSWORD):
        assert value not in caplog.text


def test_tjupt_two_factor_warning_is_not_classified_as_rejection():
    assert ConfiguredSiteLoginAdapter._classify(
        "https://tjupt.org/takelogin.php", _verify_html(), DEFINITION,
    ) == "unknown"


def test_tjupt_declares_a_separate_two_factor_form():
    assert DEFINITION.two_factor_field == "verify_code"
    assert DEFINITION.two_factor_form_selector == 'form[name="verifyform"]'
    assert DEFINITION.two_factor_submit_selector == 'input[type="submit"]'
