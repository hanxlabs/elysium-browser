"""TJUPT submits through its native submitLogin() button."""

from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition(
    "tjupt", "北洋园", ("tjupt.org", "www.tjupt.org"), "tjupt.org",
    form_selector='form[action$="takelogin.php"][method="post"]',
    submit_selector='input[type="button"][value="登录"]',
)


class TjuptLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
