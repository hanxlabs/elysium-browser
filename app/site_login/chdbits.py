from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition


DEFINITION = SiteDefinition(
    "chdbits",
    "CHDBits",
    ("ptchdbits.co", "chdbits.co"),
    "ptchdbits.co",
    host_suffixes=("ptchdbits.co", "chdbits.co"),
    image_captcha=True,
    form_selector='form[action$="takelogin.php"][method="post"]',
    submit_selector='input[type="submit"]',
)


class ChdbitsLoginAdapter(ConfiguredSiteLoginAdapter):
    """CHDBits 图片验证码登录。"""

    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
