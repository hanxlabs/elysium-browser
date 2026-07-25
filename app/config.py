"""浏览器网关运行配置。"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从环境变量读取并校验浏览器网关配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="BROWSER_GATEWAY_",
        case_sensitive=False,
        extra="ignore",
        enable_decoding=False,
    )

    token: str = ""
    headless: bool = True
    humanize: bool = True
    human_preset: str = "default"
    max_concurrency: int = Field(default=2, ge=1, le=8)
    default_timeout_seconds: int = Field(default=45, ge=1, le=60)
    max_timeout_seconds: int = Field(default=60, ge=1, le=120)
    max_html_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=8 * 1024 * 1024)

    @field_validator("human_preset")
    @classmethod
    def validate_human_preset(cls, value: str) -> str:
        """限制为 CloakBrowser 支持的拟人化预设。"""
        normalized = value.strip().lower()
        if normalized not in {"default", "careful"}:
            raise ValueError("BROWSER_GATEWAY_HUMAN_PRESET 必须为 default 或 careful")
        return normalized


@lru_cache
def get_settings() -> Settings:
    """获取进程级单例配置。"""
    return Settings()
