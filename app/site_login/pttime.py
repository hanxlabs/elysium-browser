"""PTTime 自动登录适配器。"""

from app.site_login.shared import ConfiguredSiteLoginAdapter, SiteDefinition

DEFINITION = SiteDefinition(
    "pttime",
    "PTTime",
    ("www.pttime.org",),
    "www.pttime.org",
    submit_selector='button[type="submit"]',
    # PTTime 的云层动画和 Cloudflare JSD 会持续改动 DOM。使用一次性重新定位、
    # 填写和提交，避免 CloakBrowser 在拟人化检查期间拿到已脱离的表单元素。
    atomic_dom_submit=True,
)


class PttimeLoginAdapter(ConfiguredSiteLoginAdapter):
    def __init__(self, settings, url_guard, recognizer=None, context_factory=None):
        super().__init__(settings, url_guard, DEFINITION, recognizer, context_factory)
