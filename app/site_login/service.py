"""站点登录适配器选择服务。"""

from app.config import Settings
from app.models import SiteLoginRequest, SiteLoginResponse
from app.security import OutboundUrlGuard
from app.site_login.base import SiteLoginAdapter
from app.site_login.sunnypt import SunnyPtLoginAdapter


class SiteLoginService:
    """只负责选择站点适配器，不承载任何站点登录细节。"""

    def __init__(
        self,
        settings: Settings,
        adapters: list[SiteLoginAdapter] | None = None,
    ):
        """注册当前网关已支持的站点登录适配器。"""
        self._adapters = adapters if adapters is not None else [
            SunnyPtLoginAdapter(settings, OutboundUrlGuard())
        ]

    def login(self, request: SiteLoginRequest) -> SiteLoginResponse:
        """选择匹配的站点适配器并执行一次登录。"""
        adapter = next(
            (candidate for candidate in self._adapters if candidate.supports(request.site_key)),
            None,
        )
        if adapter is None:
            raise ValueError(f"站点暂未配置自动登录适配器: {request.site_key}")
        return adapter.login(request)
