from __future__ import annotations

import time

from app.auth import create_session, verify_session
from app.config import Settings


def settings() -> Settings:
    return Settings(
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD="test-password-12345",
        SESSION_SECRET="test-session-secret-that-is-long-enough",
        IDENTITY_STORE_ID="d-1234567890",
        INSTANCE_ARN="arn:aws:sso:::instance/ssoins-1234567890abcdef",
    )


def test_session_round_trip() -> None:
    config = settings()
    token = create_session("admin", config)
    assert verify_session(token, config)["sub"] == "admin"


def test_tampered_session_is_rejected() -> None:
    config = settings()
    token = create_session("admin", config)
    encoded, signature = token.split(".")
    assert verify_session(f"{encoded}x.{signature}", config) is None


def test_expired_session_is_rejected(monkeypatch) -> None:
    config = settings()
    token = create_session("admin", config)
    monkeypatch.setattr(time, "time", lambda: 9_999_999_999)
    assert verify_session(token, config) is None
