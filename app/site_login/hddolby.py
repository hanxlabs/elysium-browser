from dataclasses import replace

from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

_LEGACY_HOSTS = ("www.hddolby.com", "hddolby.com")

DEFINITION = SiteDefinition(
    "hddolby",
    "HDDolby",
    ("www.uhddolby.com", "uhddolby.com"),
    "www.uhddolby.com",
    image_captcha=True,
    two_factor_field="2fa",
    submit_selector='input[type="submit"]',
)


class HddolbyLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)

    @staticmethod
    def _build_origin(site_url: str, definition: SiteDefinition) -> str:
        # Validate legacy URLs too, then keep credentials and cookies on the new host.
        ConfiguredSiteLoginAdapter._build_origin(
            site_url, replace(definition, hosts=(*definition.hosts, *_LEGACY_HOSTS)),
        )
        return f"https://{definition.default_host}"
