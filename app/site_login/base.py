"""站点登录适配器公共协议。"""

from abc import ABC, abstractmethod

from app.models import SiteLoginRequest, SiteLoginResponse


class SiteLoginAdapter(ABC):
    """定义单个站点独立登录流程的最小协议。"""

    @abstractmethod
    def supports(self, site_key: str) -> bool:
        """判断当前适配器是否支持指定站点。"""

    @abstractmethod
    def login(self, request: SiteLoginRequest) -> SiteLoginResponse:
        """按站点规则执行一次登录且不做重试。"""
