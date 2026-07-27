from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition("52movie", "52MOVIE", ("www.52movie.top",), "www.52movie.top", True, False, False, "two_step_code", submit_selector='input[type="submit"]')

class Movie52LoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
