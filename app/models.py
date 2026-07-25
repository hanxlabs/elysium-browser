"""浏览器网关 API 数据模型。"""

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class FetchPageRequest(BaseModel):
    """受限页面抓取请求。"""

    request_id: str = Field(min_length=1, max_length=128)
    site_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    account_id: int = Field(ge=1)
    url: HttpUrl
    cookie: str | None = Field(default=None, max_length=65536)
    headers: dict[str, str] = Field(default_factory=dict, max_length=16)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    wait_until: Literal["domcontentloaded", "load", "networkidle"] = "domcontentloaded"

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        """只允许与页面导航兼容且不影响网络边界的请求头。"""
        blocked_headers = {"cookie", "host", "proxy-authorization", "connection", "content-length"}
        normalized: dict[str, str] = {}
        for name, value in headers.items():
            normalized_name = name.strip()
            if not normalized_name or normalized_name.lower() in blocked_headers:
                raise ValueError(f"不允许设置请求头: {name}")
            if len(normalized_name) > 128 or len(value) > 4096:
                raise ValueError("请求头长度超限")
            normalized[normalized_name] = value
        return normalized


class BrowserCookie(BaseModel):
    """浏览器返回的同站 Cookie。"""

    name: str
    value: str
    domain: str
    path: str
    expires: float | None = None
    http_only: bool = False
    secure: bool = False
    same_site: str | None = None


class FetchPageResponse(BaseModel):
    """受限页面抓取结果。"""

    request_id: str
    status: int | None
    final_url: str
    html: str
    html_truncated: bool
    cookies: list[BrowserCookie]
    page_title: str | None
    challenge_detected: bool
    duration_ms: int


class ErrorResponse(BaseModel):
    """统一错误响应。"""

    detail: str


class SiteLoginRequest(BaseModel):
    """按站点执行一次登录的内部请求。"""

    request_id: str = Field(min_length=1, max_length=128)
    site_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    account_id: int = Field(ge=1)
    site_url: HttpUrl
    credentials: dict[str, str] = Field(default_factory=dict, max_length=8)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)

    @field_validator("credentials")
    @classmethod
    def validate_credentials(cls, credentials: dict[str, str]) -> dict[str, str]:
        """限制凭据字段数量和长度，具体必填项由站点适配器校验。"""
        normalized: dict[str, str] = {}
        for name, value in credentials.items():
            normalized_name = name.strip()
            if not normalized_name or len(normalized_name) > 64 or len(value) > 4096:
                raise ValueError("登录凭据字段无效")
            normalized[normalized_name] = value
        return normalized


class SiteLoginCredential(BaseModel):
    """站点登录成功后返回给 Elysium 的临时凭据。"""

    bearer_token: str | None = None
    cookie: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class SiteLoginResponse(BaseModel):
    """按站点执行一次登录的结果。"""

    request_id: str
    site_key: str
    success: bool
    message: str
    credential: SiteLoginCredential | None = None
    duration_ms: int
