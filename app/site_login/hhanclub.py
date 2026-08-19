from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition


DEFINITION = SiteDefinition(
    "hhanclub",
    "HHanClub",
    ("hhanclub.net",),
    "hhanclub.net",
    host_suffixes=("hhanclub.net",),
    image_captcha=True,
    two_factor_field="two_step_code",
    form_selector='form[action$="takelogin.php"][method="post"]',
    submit_selector='input[type="submit"]',
)


class HhanclubLoginAdapter(ConfiguredSiteLoginAdapter):
    """HHanClub 图片验证码与可选 TOTP 登录。"""

    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
