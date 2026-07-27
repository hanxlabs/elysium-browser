"""咖啡图片验证码登录适配器。"""

from app.site_login.btschool import BtschoolLoginAdapter


class PtCafeLoginAdapter(BtschoolLoginAdapter):
    """使用 NexusPHP 图片验证码流程登录咖啡。"""

    _SITE_KEY = "ptcafe"
    _SITE_NAME = "咖啡"
    _HOST = "ptcafe.club"
    _SUBMIT_SELECTOR = '#submit-btn, input[type="button"][value="登录"]'
