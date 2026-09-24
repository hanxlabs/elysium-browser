"""PTLGS（劳改所）自动登录适配器。"""

from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition


DEFINITION = SiteDefinition(
    "ptlgs",
    "PTLGS",
    ("ptlgs.org",),
    "ptlgs.org",
    host_suffixes=("ptlgs.org",),
    image_captcha=True,
    two_factor_field="two_step_code",
    form_selector='form[action$="takelogin.php"][method="post"]',
    submit_selector='input[type="submit"]',
)


class PtlgsLoginAdapter(ConfiguredSiteLoginAdapter):
    """PTLGS 使用 NexusPHP 图片验证码，可选两步验证。"""

    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
