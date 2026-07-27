from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition("hddolby", "HDDolby", ("www.hddolby.com",), "www.hddolby.com", True, False, False, "2fa", submit_selector='input[type="submit"]')

class HddolbyLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
