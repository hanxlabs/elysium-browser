from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition


# 聆音Club 使用 NexusPHP 图片验证码，可选两步验证，登录由脚本按钮触发。
DEFINITION = SiteDefinition(
    "soulvoice",
    "聆音Club",
    ("pt.soulvoice.club",),
    "pt.soulvoice.club",
    image_captcha=True,
    two_factor_field="two_step_code",
    submit_selector="#submit-btn",
)


class SoulvoiceLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
