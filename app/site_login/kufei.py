from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition


DEFINITION = SiteDefinition(
    "kufei",
    "库非",
    ("kufei.org",),
    "kufei.org",
    turnstile=True,
    two_factor_field="two_step_code",
    form_selector="#login-form",
    submit_selector="#submit-btn",
)


class KufeiLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(
            settings,
            url_guard,
            DEFINITION,
            recognizer,
            context_factory,
        )
