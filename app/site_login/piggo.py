from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition(
    "piggo",
    "猪猪",
    ("piggo.me",),
    "piggo.me",
    challenge=True,
    two_factor_field="two_step_code",
    cloudflare_managed=True,
)


class PiggoLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
