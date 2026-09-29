"""HDTIME standard NexusPHP login with an optional two-step code."""

from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition


DEFINITION = SiteDefinition(
    "hdtime",
    "HDTIME",
    ("hdtime.org",),
    "hdtime.org",
    two_factor_field="two_step_code",
    form_selector='form[action$="takelogin.php"][method="post"]',
    submit_selector='input[type="submit"][value="登录"]',
)


class HdtimeLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
