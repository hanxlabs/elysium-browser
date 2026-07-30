"""VC-Lib 自动登录适配器。"""

from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition


DEFINITION = SiteDefinition(
    "vclib",
    "VC-Lib",
    ("pt.vclib.online",),
    "pt.vclib.online",
    host_suffixes=("vclib.online",),
    image_captcha=True,
    challenge=True,
    two_factor_field="two_step_code",
    form_selector="#login-form",
    submit_selector="#submit-btn",
)


class VclibLoginAdapter(ConfiguredSiteLoginAdapter):
    """复用通用 NexusPHP 浏览器流程完成 OCR、2FA 和 challenge 登录。"""

    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
