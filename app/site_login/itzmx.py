from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition("itzmx", "PT分享站", ("pt.itzmx.com",), "pt.itzmx.com", image_captcha=True, submit_selector='input[type="submit"]')

class ItzmxLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
