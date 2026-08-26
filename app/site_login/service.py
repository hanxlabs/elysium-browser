"""站点登录适配器选择服务。"""

from app.config import Settings
from app.captcha.ocr import LocalCaptchaOcr
from app.models import SiteLoginRequest, SiteLoginResponse
from app.security import OutboundUrlGuard
from app.site_login.base import SiteLoginAdapter
from app.site_login.btschool import BtschoolLoginAdapter
from app.site_login.chdbits import ChdbitsLoginAdapter
from app.site_login.crabpt import CrabptLoginAdapter
from app.site_login.cspt import CsptLoginAdapter
from app.site_login.cyanbug import CyanbugLoginAdapter
from app.site_login.daxiangjiao import DaxiangjiaoLoginAdapter
from app.site_login.discfan import DiscfanLoginAdapter
from app.site_login.hddolby import HddolbyLoginAdapter
from app.site_login.hdfans import HdfansLoginAdapter
from app.site_login.hdhome import HdhomeLoginAdapter
from app.site_login.hxpt import HxptLoginAdapter
from app.site_login.hhanclub import HhanclubLoginAdapter
from app.site_login.itzmx import ItzmxLoginAdapter
from app.site_login.kufei import KufeiLoginAdapter
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
from app.site_login.vclib import VclibLoginAdapter


class SiteLoginService:
    """只负责选择站点适配器，不承载任何站点登录细节。"""

    def __init__(
        self,
        settings: Settings,
        adapters: list[SiteLoginAdapter] | None = None,
    ):
        """注册当前网关已支持的站点登录适配器。"""
        if adapters is not None:
            self._adapters = adapters
            return

        # URL validation is stateless and its DNS cache is process-wide. More
        # importantly, an ONNX session is expensive (the bundled model is about
        # 28 MB), so every captcha-capable adapter must share one thread-safe OCR
        # instance instead of lazily loading its own copy of the model.
        url_guard = OutboundUrlGuard()
        captcha_recognizer = LocalCaptchaOcr()
        self._adapters = [
            SunnyPtLoginAdapter(settings, url_guard),
            BtschoolLoginAdapter(settings, url_guard, captcha_recognizer),
            ChdbitsLoginAdapter(settings, url_guard, captcha_recognizer),
            CrabptLoginAdapter(settings, url_guard, captcha_recognizer),
            PtCafeLoginAdapter(settings, url_guard, captcha_recognizer),
            CsptLoginAdapter(settings, url_guard, captcha_recognizer),
            CyanbugLoginAdapter(settings, url_guard, captcha_recognizer),
            DaxiangjiaoLoginAdapter(settings, url_guard, captcha_recognizer),
            DiscfanLoginAdapter(settings, url_guard, captcha_recognizer),
            HddolbyLoginAdapter(settings, url_guard, captcha_recognizer),
            HdfansLoginAdapter(settings, url_guard, captcha_recognizer),
            HdhomeLoginAdapter(settings, url_guard, captcha_recognizer),
            HxptLoginAdapter(settings, url_guard, captcha_recognizer),
            HhanclubLoginAdapter(settings, url_guard, captcha_recognizer),
            ItzmxLoginAdapter(settings, url_guard, captcha_recognizer),
            KufeiLoginAdapter(settings, url_guard, captcha_recognizer),
            MonikadesignLoginAdapter(settings, url_guard, captcha_recognizer),
            MuxuegeLoginAdapter(settings, url_guard, captcha_recognizer),
            NiceptLoginAdapter(settings, url_guard, captcha_recognizer),
            NovahdLoginAdapter(settings, url_guard, captcha_recognizer),
            PtsbaoLoginAdapter(settings, url_guard, captcha_recognizer),
            PtskitLoginAdapter(settings, url_guard, captcha_recognizer),
            PttimeLoginAdapter(settings, url_guard, captcha_recognizer),
            TangptLoginAdapter(settings, url_guard, captcha_recognizer),
            VclibLoginAdapter(settings, url_guard, captcha_recognizer),
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
