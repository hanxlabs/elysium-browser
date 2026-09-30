"""Kelu 图片验证码与可选两步验证登录。"""

from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition


DEFINITION = SiteDefinition(
    "kelu",
    "Kelu",
    ("our.kelu.one",),
    "our.kelu.one",
    image_captcha=True,
    two_factor_field="two_step_code",
    form_selector='#login-form[action$="takelogin.php"][method="post"]',
    submit_selector="#submit-btn",
)


class KeluLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
