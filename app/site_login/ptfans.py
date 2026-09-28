"""PTFans 图片验证码与挑战响应登录。"""

from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition


DEFINITION = SiteDefinition(
    "ptfans",
    "PTFans",
    ("ptfans.cc", "cusat.win"),
    "ptfans.cc",
    image_captcha=True,
    two_factor_field="two_step_code",
    form_selector='#login-form[action$="takelogin.php"][method="post"]',
    submit_selector="#submit-btn",
)


class PtfansLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
