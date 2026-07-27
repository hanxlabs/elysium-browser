from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition("hdfans", "HDFans", ("hdfans.org",), "hdfans.org", True, False, False, "two_step_code", submit_selector='input[type="submit"]')

class HdfansLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
