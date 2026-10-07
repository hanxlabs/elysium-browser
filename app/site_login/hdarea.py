"""HDArea's supplied login form uses username/password without a CAPTCHA."""

from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition(
    "hdarea", "HDArea", ("hdarea.club",), "hdarea.club",
    form_selector='form[action$="takelogin.php"][method="post"]',
    submit_selector='input[type="submit"][value="登录"]',
)


class HdareaLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
