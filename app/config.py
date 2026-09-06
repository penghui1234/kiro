from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = Field(min_length=12)
    SESSION_SECRET: str = Field(min_length=32)
    SESSION_TTL_HOURS: int = Field(default=12, ge=1, le=168)
    COOKIE_SECURE: bool = False

    AWS_PROFILE: str | None = None
    SSO_REGION: str = "us-east-1"
    KIRO_REGION: str = "us-east-1"
    IDENTITY_STORE_ID: str
    INSTANCE_ARN: str
    REPORT_BUCKET: str | None = None
    REPORT_PREFIX: str = "user-activity-reports"
    REPORT_OVERAGE_PRICE_PER_CREDIT: float = Field(default=0.04, ge=0, le=100)

    @field_validator("IDENTITY_STORE_ID")
    @classmethod
    def validate_identity_store_id(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("d-") and len(value) != 36:
            raise ValueError("IDENTITY_STORE_ID 格式无效")
        return value

    @field_validator("INSTANCE_ARN")
    @classmethod
    def validate_instance_arn(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("arn:aws:sso:::instance/"):
            raise ValueError("INSTANCE_ARN 格式无效")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
