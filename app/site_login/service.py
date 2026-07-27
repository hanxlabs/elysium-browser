"""站点登录适配器选择服务。"""

from app.config import Settings
from app.models import SiteLoginRequest, SiteLoginResponse
from app.security import OutboundUrlGuard
from app.site_login.base import SiteLoginAdapter
from app.site_login.btschool import BtschoolLoginAdapter
from app.site_login.crabpt import CrabptLoginAdapter
from app.site_login.cspt import CsptLoginAdapter
from app.site_login.cyanbug import CyanbugLoginAdapter
from app.site_login.daxiangjiao import DaxiangjiaoLoginAdapter
from app.site_login.discfan import DiscfanLoginAdapter
from app.site_login.hddolby import HddolbyLoginAdapter
from app.site_login.hdfans import HdfansLoginAdapter
from app.site_login.hdhome import HdhomeLoginAdapter
from app.site_login.hxpt import HxptLoginAdapter
from app.site_login.itzmx import ItzmxLoginAdapter
from app.site_login.monikadesign import MonikadesignLoginAdapter
from app.site_login.muxuege import MuxuegeLoginAdapter
from app.site_login.nicept import NiceptLoginAdapter
from app.site_login.novahd import NovahdLoginAdapter
from app.site_login.ptcafe import PtCafeLoginAdapter
from app.site_login.ptsbao import PtsbaoLoginAdapter
from app.site_login.ptskit import PtskitLoginAdapter
from app.site_login.pttime import PttimeLoginAdapter
from app.site_login.sunnypt import SunnyPtLoginAdapter
from app.site_login.tangpt import TangptLoginAdapter


class SiteLoginService:
    """只负责选择站点适配器，不承载任何站点登录细节。"""

    def __init__(
        self,
        settings: Settings,
        adapters: list[SiteLoginAdapter] | None = None,
    ):
        """注册当前网关已支持的站点登录适配器。"""
        self._adapters = adapters if adapters is not None else [
            SunnyPtLoginAdapter(settings, OutboundUrlGuard()),
            BtschoolLoginAdapter(settings, OutboundUrlGuard()),
            CrabptLoginAdapter(settings, OutboundUrlGuard()),
            PtCafeLoginAdapter(settings, OutboundUrlGuard()),
            CsptLoginAdapter(settings, OutboundUrlGuard()),
            CyanbugLoginAdapter(settings, OutboundUrlGuard()),
            DaxiangjiaoLoginAdapter(settings, OutboundUrlGuard()),
            DiscfanLoginAdapter(settings, OutboundUrlGuard()),
            HddolbyLoginAdapter(settings, OutboundUrlGuard()),
            HdfansLoginAdapter(settings, OutboundUrlGuard()),
            HdhomeLoginAdapter(settings, OutboundUrlGuard()),
            HxptLoginAdapter(settings, OutboundUrlGuard()),
            ItzmxLoginAdapter(settings, OutboundUrlGuard()),
            MonikadesignLoginAdapter(settings, OutboundUrlGuard()),
            MuxuegeLoginAdapter(settings, OutboundUrlGuard()),
            NiceptLoginAdapter(settings, OutboundUrlGuard()),
            NovahdLoginAdapter(settings, OutboundUrlGuard()),
            PtsbaoLoginAdapter(settings, OutboundUrlGuard()),
            PtskitLoginAdapter(settings, OutboundUrlGuard()),
            PttimeLoginAdapter(settings, OutboundUrlGuard()),
            TangptLoginAdapter(settings, OutboundUrlGuard()),
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
