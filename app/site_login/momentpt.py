from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition("momentpt", "MomentPT", ("www.momentpt.top", "momentpt.top"), "www.momentpt.top",
                            image_captcha=True, two_factor_field="two_step_code")


class MomentptLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
