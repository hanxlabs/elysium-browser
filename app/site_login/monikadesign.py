from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition("monikadesign", "MonikaDesign", ("monikadesign.uk",), "monikadesign.uk", login_path="/login", form_selector='form[action$="/login"]', submit_selector="#login-button", unit3d=True)

class MonikadesignLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
