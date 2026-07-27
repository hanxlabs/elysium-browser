from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition("hdhome", "HDHome", ("hdhome.org",), "hdhome.org", two_factor_field="scode", submit_selector='input[type="submit"]')

class HdhomeLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
