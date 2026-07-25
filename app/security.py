"""网关认证与出站地址约束。"""

import hmac
import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status

from app.config import Settings


def require_gateway_token(request: Request) -> None:
    """验证内部调用令牌；未配置令牌时拒绝业务请求。"""
    settings: Settings = request.app.state.settings
    if not settings.token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="浏览器网关未配置 BROWSER_GATEWAY_TOKEN",
        )
    supplied = request.headers.get("X-Elysium-Gateway-Token", "")
    if not hmac.compare_digest(supplied, settings.token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="浏览器网关认证失败")


class OutboundUrlGuard:
    """限制浏览器只能访问公网 HTTP(S) 地址。"""

    def ensure_allowed(self, url: str) -> None:
        """校验 URL 协议和 DNS 解析结果。"""
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("只允许访问 HTTP(S) URL")
        hostname = parsed.hostname.lower().rstrip(".")
        if self._is_private_or_loopback(hostname):
            raise ValueError("不允许访问本机、私网或保留地址")

    @staticmethod
    @lru_cache(maxsize=256)
    def _is_private_or_loopback(hostname: str) -> bool:
        """解析 DNS 并拒绝解析到非公网地址的主机。"""
        try:
            addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except socket.gaierror as error:
            raise ValueError(f"无法解析目标主机: {hostname}") from error
        resolved = {address[4][0] for address in addresses}
        if not resolved:
            raise ValueError(f"无法解析目标主机: {hostname}")
        return any(not ipaddress.ip_address(address).is_global for address in resolved)
