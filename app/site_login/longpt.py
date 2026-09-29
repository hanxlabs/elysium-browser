"""LongPT NexusPHP Turnstile、挑战响应与可选两步验证登录。"""

from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition


DEFINITION = SiteDefinition(
    "longpt",
    "LongPT",
    ("longpt.org",),
    "longpt.org",
    turnstile=True,
    challenge=True,
    two_factor_field="two_step_code",
    form_selector='form[action$="takelogin.php"][method="post"]',
    submit_selector="#submit-btn",
)


class LongptLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
