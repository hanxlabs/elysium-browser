"""TLFBits 用户名、密码与图片验证码登录。"""

from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition(
    "tlfbits", "TLFBits", ("pt.eastgame.org",), "pt.eastgame.org",
    image_captcha=True,
    form_selector='form[action$="takelogin.php"][method="post"]',
    submit_selector='input[type="submit"][value="登录"]',
)


class TlfbitsLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
