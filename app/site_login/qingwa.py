from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition("qingwa", "青蛙", ("www.qingwapt.com", "www.qingwapt.org", "www.qingwa.pro", "qingwapt.com"), "www.qingwapt.com", turnstile=True, two_factor_field="two_step_code", submit_selector="#submit_login", reveal_selector="#login")

class QingwaLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
