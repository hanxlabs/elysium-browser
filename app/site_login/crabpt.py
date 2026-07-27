"""蟹黄堡图片验证码登录适配器。"""

from app.site_login.btschool import BtschoolLoginAdapter


class CrabptLoginAdapter(BtschoolLoginAdapter):
    """使用 NexusPHP 图片验证码流程登录蟹黄堡。"""

    _SITE_KEY = "crabpt"
    _SITE_NAME = "蟹黄堡"
    _HOST = "crabpt.vip"
    _SUBMIT_SELECTOR = '#submit-btn, input[type="button"][value="登录"]'
