from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition("daxiangjiao", "大香蕉", ("pt.daxiangjiao.org",), "pt.daxiangjiao.org", image_captcha=True, challenge=True, two_factor_field="two_step_code")

class DaxiangjiaoLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
