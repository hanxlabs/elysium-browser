from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition


DEFINITION = SiteDefinition(
    "agsvpt",
    "AGSVPT",
    ("www.agsvpt.com", "pt.agsvpt.cn", "new.agsvpt.cn"),
    "www.agsvpt.com",
    host_suffixes=("agsvpt.com", "agsvpt.cn"),
    two_factor_field="two_step_code",
    form_selector='form[onsubmit*="handleLogin"]',
    submit_selector="#loginBtn",
)


class AgsvptLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
