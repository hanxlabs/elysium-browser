from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition


# 修道院登录页使用 NexusPHP 图片验证码和可选两步验证，提交由 #submit-btn 触发。
DEFINITION = SiteDefinition(
    "xdy",
    "修道院",
    ("xdypt.vip",),
    "xdypt.vip",
    image_captcha=True,
    challenge=True,
    two_factor_field="two_step_code",
    submit_selector="#submit-btn",
)


class XdyLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
