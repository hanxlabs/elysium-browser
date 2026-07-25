"""网关安全边界测试。"""

import pytest

from app.security import OutboundUrlGuard


def test_allows_public_http_host(monkeypatch):
    """公网 HTTP(S) 主机不再要求位于预设白名单。"""
    guard = OutboundUrlGuard()
    monkeypatch.setattr(OutboundUrlGuard, "_is_private_or_loopback", staticmethod(lambda _: False))

    guard.ensure_allowed("https://example.com/")


def test_rejects_private_ip():
    """私网 IP 无论如何都不得访问。"""
    guard = OutboundUrlGuard()

    with pytest.raises(ValueError, match="本机、私网或保留地址"):
        guard.ensure_allowed("http://127.0.0.1/")
