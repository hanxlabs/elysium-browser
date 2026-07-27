from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition(
    "audiences",
    "Audiences",
    ("audiences.me",),
    "audiences.me",
    image_captcha=True,
    two_factor_field="scode",
    submit_selector='input[type="submit"]',
    cloudflare_managed=True,
)


class AudiencesLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
