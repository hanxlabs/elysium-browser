"""登录适配器使用的本地 TOTP 生成工具。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import struct
import time
from urllib.parse import parse_qs, urlparse


def generate_totp(
    secret: str,
    *,
    timestamp: float | None = None,
    minimum_validity_seconds: int = 5,
) -> str:
    """生成六位 SHA-1 TOTP；临近周期结束时等待下一个周期。"""
    key = _decode_secret(secret)
    now = time.time() if timestamp is None else timestamp
    if timestamp is None:
        remaining = 30 - (int(now) % 30)
        if remaining <= minimum_validity_seconds:
            time.sleep(remaining + 0.1)
            now = time.time()
    counter = int(now) // 30
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return f"{binary % 1_000_000:06d}"


def _decode_secret(secret: str) -> bytes:
    value = secret.strip()
    if value.lower().startswith("otpauth://"):
        parsed = urlparse(value)
        value = parse_qs(parsed.query).get("secret", [""])[0]
    normalized = re.sub(r"[\s-]+", "", value).upper().rstrip("=")
    if not normalized or not re.fullmatch(r"[A-Z2-7]+", normalized):
        raise ValueError("2FA密钥格式无效")
    padded = normalized + "=" * ((8 - len(normalized) % 8) % 8)
    try:
        decoded = base64.b32decode(padded, casefold=True)
    except Exception as error:
        raise ValueError("2FA密钥格式无效") from error
    if not decoded:
        raise ValueError("2FA密钥格式无效")
    return decoded
