from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition("muxuege", "慕雪阁", ("pt.muxuege.org",), "pt.muxuege.org", challenge=True, two_factor_field="two_step_code")

class MuxuegeLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
