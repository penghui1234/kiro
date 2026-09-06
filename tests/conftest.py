from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ.update(
    {
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "test-password-12345",
        "SESSION_SECRET": "test-session-secret-that-is-long-enough",
        "IDENTITY_STORE_ID": "d-1234567890",
        "INSTANCE_ARN": "arn:aws:sso:::instance/ssoins-1234567890abcdef",
        "SSO_REGION": "us-east-1",
        "KIRO_REGION": "us-east-1",
    }
)

from app.aws_gateway import AWSGatewayError
from app.config import get_settings
from app.main import _login_attempts, app, get_gateway, get_report_service
from app.report_service import MonthlyReport


class FakeGateway:
    def __init__(self) -> None:
        self.reset_user_ids: list[str] = []
        self.verified_user_ids: list[str] = []
        self.deleted_user_ids: list[str] = []
        self.created_users: list[tuple[str, str, str]] = []
        self.create_user_failures: set[str] = set()
        self.assign_subscription_failures: set[str] = set()
        self.actions: list[tuple[str, str, str | None]] = []
        self.users = [
            {
                "user_id": "user-1",
                "user_name": "alice@example.com",
                "display_name": "Alice",
                "email": "alice@example.com",
            },
            {
                "user_id": "user-2",
                "user_name": "bob",
                "display_name": "Bob",
                "email": "bob@example.com",
            },
        ]
        self.subscriptions = [
            {
                "principal_id": "user-1",
                "subscription_type": "KIRO_ENTERPRISE_PRO",
                "status": "active",
                "aggregated_type": "INDIVIDUAL",
                "activation_date": None,
                "usage": None,
            }
        ]

    async def list_users(self) -> list[dict[str, Any]]:
        return self.users

    async def list_subscriptions(self) -> list[dict[str, Any]]:
        return self.subscriptions

    async def create_user(self, user_name: str, display_name: str, email: str) -> str:
        if user_name in self.create_user_failures:
            raise AWSGatewayError("identitystore.create_user", "用户已存在", 409)
        self.created_users.append((user_name, display_name, email))
        return f"created-{len(self.created_users)}"

    async def verify_email(self, user_id: str) -> None:
        self.verified_user_ids.append(user_id)

    async def reset_password(self, user_id: str) -> None:
        self.reset_user_ids.append(user_id)

    async def delete_user(self, user_id: str) -> None:
        self.deleted_user_ids.append(user_id)

    async def assign_subscription(self, principal_id: str, subscription_type: str) -> None:
        if principal_id in self.assign_subscription_failures:
            raise AWSGatewayError("AmazonQDeveloperService.CreateAssignment", "分配失败")
        self.actions.append(("assign", principal_id, subscription_type))

    async def change_subscription(self, principal_id: str, subscription_type: str) -> None:
        self.actions.append(("change", principal_id, subscription_type))

    async def cancel_subscription(self, principal_id: str) -> None:
        self.actions.append(("cancel", principal_id, None))


class FakeReportService:
    def __init__(self) -> None:
        self.report = MonthlyReport(
            status="unconfigured",
            message="尚未配置 Kiro User Activity Reports 的 S3 桶。",
        )
        self.calls = 0
        self.months: list[str | None] = []

    async def load_latest(self, month: str | None = None) -> MonthlyReport:
        self.calls += 1
        self.months.append(month)
        return self.report


@pytest.fixture
def fake_gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def fake_report_service() -> FakeReportService:
    return FakeReportService()


@pytest.fixture
def client(fake_gateway: FakeGateway, fake_report_service: FakeReportService):
    get_settings.cache_clear()
    get_gateway.cache_clear()
    get_report_service.cache_clear()
    _login_attempts.clear()
    app.dependency_overrides[get_gateway] = lambda: fake_gateway
    app.dependency_overrides[get_report_service] = lambda: fake_report_service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def logged_in_client(client: TestClient) -> TestClient:
    response = client.post(
        "/api/login", json={"username": "admin", "password": "test-password-12345"}
    )
    assert response.status_code == 200
    return client
