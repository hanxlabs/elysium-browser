from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition("cspt", "财神", ("cspt.top", "cspt.cc", "cspt.date"), "cspt.top", two_factor_field="two_step_code", submit_selector='input[type="submit"]')

class CsptLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
