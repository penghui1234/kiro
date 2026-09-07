from __future__ import annotations

from typing import Any

import pytest

from app.aws_gateway import AWSGateway, AWSGatewayError, normalize_subscription
from app.config import Settings


def test_normalize_real_k_subscription() -> None:
    item = normalize_subscription(
        {
            "principal": {"user": "user-1"},
            "type": {"amazonQ": "KIRO_ENTERPRISE_PRO_PLUS"},
            "activatedType": {"amazonQ": "KIRO_ENTERPRISE_PRO_PLUS"},
            "status": "ACTIVE",
            "aggregatedSubscriptionType": "INDIVIDUAL_AND_GROUP",
        }
    )
    assert item == {
        "principal_id": "user-1",
        "subscription_type": "KIRO_ENTERPRISE_PRO_PLUS",
        "status": "active",
        "aggregated_type": "INDIVIDUAL_AND_GROUP",
        "activation_date": None,
        "usage": None,
    }


def test_normalize_legacy_subscription() -> None:
    item = normalize_subscription(
        {
            "PrincipalId": "user-2",
            "SubscriptionType": "KIRO_ENTERPRISE_PRO",
            "Status": "PENDING",
        }
    )
    assert item["principal_id"] == "user-2"
    assert item["subscription_type"] == "KIRO_ENTERPRISE_PRO"
    assert item["status"] == "pending"


def test_normalize_cancelled_status() -> None:
    item = normalize_subscription(
        {
            "principal": {"user": "user-3"},
            "type": {"amazonQ": "KIRO_ENTERPRISE_PRO"},
            "status": "CANCELLED",
        }
    )
    assert item["status"] == "canceled"


def gateway_settings() -> Settings:
    return Settings(
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD="test-password-12345",
        SESSION_SECRET="test-session-secret-that-is-long-enough",
        IDENTITY_STORE_ID="d-1234567890",
        INSTANCE_ARN="arn:aws:sso:::instance/ssoins-1234567890abcdef",
        AWS_PROFILE=None,
    )


def test_gateway_does_not_force_profile_by_default(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeSession:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("app.aws_gateway.boto3.Session", FakeSession)
    AWSGateway(gateway_settings())
    assert captured["profile_name"] is None


@pytest.mark.asyncio
async def test_password_and_subscription_request_contracts(monkeypatch) -> None:
    gateway = AWSGateway(gateway_settings())
    calls: list[dict[str, Any]] = []

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(gateway, "_sigv4_post", fake_post)
    await gateway.reset_password("user-1")
    await gateway.assign_subscription("user-1", "KIRO_ENTERPRISE_PRO")
    await gateway.change_subscription("user-1", "KIRO_ENTERPRISE_PRO_PLUS")
    await gateway.cancel_subscription("user-1")

    assert calls[0]["target"] == "SWBUPService.UpdatePassword"
    assert calls[0]["service"] == "userpool"
    assert calls[0]["payload"] == {"UserId": "user-1", "PasswordMode": "EMAIL"}
    assert [call["target"] for call in calls[1:]] == [
        "AmazonQDeveloperService.CreateAssignment",
        "AmazonQDeveloperService.UpdateAssignment",
        "AmazonQDeveloperService.DeleteAssignment",
    ]
    assert all(call["service"] == "q" for call in calls[1:])
    assert calls[1]["payload"]["subscriptionType"] == "KIRO_ENTERPRISE_PRO"
    assert "subscriptionType" not in calls[3]["payload"]


@pytest.mark.asyncio
async def test_create_user_request_contract(monkeypatch) -> None:
    gateway = AWSGateway(gateway_settings())
    calls: list[dict[str, Any]] = []

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"User": {"UserId": "created-user"}}

    monkeypatch.setattr(gateway, "_sigv4_post", fake_post)
    user_id = await gateway.create_user("new-user", "New User", "new@example.com")

    assert user_id == "created-user"
    assert calls == [
        {
            "url": "https://identitystore.us-east-1.amazonaws.com/identitystore/",
            "target": "AWSIdentityStoreService.CreateUser",
            "payload": {
                "IdentityStoreId": "d-1234567890",
                "UserName": "new-user",
                "UserAttributes": {
                    "emails": {
                        "ComplexListValue": [
                            {
                                "value": {"StringValue": "new@example.com"},
                                "type": {"StringValue": "work"},
                                "primary": {"BooleanValue": True},
                            }
                        ]
                    },
                    "name": {
                        "ComplexValue": {
                            "givenName": {"StringValue": "New"},
                            "familyName": {"StringValue": "User"},
                        }
                    },
                    "displayName": {"StringValue": "New User"},
                },
                "Active": True,
                "PasswordMode": "EMAIL",
            },
            "service": "identitystore",
            "region": "us-east-1",
        }
    ]


@pytest.mark.asyncio
async def test_assignment_retries_legacy_plan_on_validation_error(monkeypatch) -> None:
    gateway = AWSGateway(gateway_settings())
    calls: list[dict[str, Any]] = []

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        calls.append({**kwargs, "payload": dict(kwargs["payload"])})
        if len(calls) == 1:
            raise AWSGatewayError("CreateAssignment", "[ValidationException] unsupported plan")
        return {}

    monkeypatch.setattr(gateway, "_sigv4_post", fake_post)
    await gateway.assign_subscription("user-1", "KIRO_ENTERPRISE_PRO_PLUS")

    assert [call["payload"]["subscriptionType"] for call in calls] == [
        "KIRO_ENTERPRISE_PRO_PLUS",
        "Q_DEVELOPER_STANDALONE_PRO_PLUS",
    ]


@pytest.mark.asyncio
async def test_subscription_list_request_and_pagination_contract(monkeypatch) -> None:
    gateway = AWSGateway(gateway_settings())
    responses = [
        {
            "subscriptions": [
                {
                    "principal": {"user": "user-1"},
                    "type": {"amazonQ": "KIRO_ENTERPRISE_PRO"},
                    "status": "ACTIVE",
                }
            ],
            "nextToken": "page-2",
        },
        {
            "subscriptions": [
                {
                    "principal": {"user": "user-2"},
                    "type": {"amazonQ": "KIRO_ENTERPRISE_PRO_PLUS"},
                    "status": "PENDING",
                }
            ]
        },
    ]
    calls: list[dict[str, Any]] = []

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr(gateway, "_sigv4_post", fake_post)
    items = await gateway.list_subscriptions()

    assert len(items) == 2
    assert calls[0]["target"] == "AWSZornControlPlaneService.ListUserSubscriptions"
    assert calls[0]["service"] == "user-subscriptions"
    assert calls[1]["payload"]["nextToken"] == "page-2"


@pytest.mark.asyncio
async def test_assignment_conflict_is_mapped_to_state_error(monkeypatch) -> None:
    gateway = AWSGateway(gateway_settings())
    calls: list[dict[str, Any]] = []

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        raise AWSGatewayError(
            "AmazonQDeveloperService.UpdateAssignment",
            "[com.amazon.kiro.controlplane#ConflictException] Resource is in an invalid state.",
        )

    monkeypatch.setattr(gateway, "_sigv4_post", fake_post)
    with pytest.raises(AWSGatewayError) as caught:
        await gateway.change_subscription("user-1", "KIRO_ENTERPRISE_PRO_PLUS")

    assert caught.value.status_code == 409
    assert "资源当前状态不允许" in caught.value.message
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_verify_email_and_delete_user_request_contracts(monkeypatch) -> None:
    gateway = AWSGateway(gateway_settings())
    signed_calls: list[dict[str, Any]] = []
    boto_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        signed_calls.append(kwargs)
        return {}

    async def fake_boto_call(service: str, method: str, **kwargs: Any) -> dict[str, Any]:
        boto_calls.append((service, method, kwargs))
        return {}

    monkeypatch.setattr(gateway, "_sigv4_post", fake_post)
    monkeypatch.setattr(gateway, "_boto_call", fake_boto_call)
    await gateway.verify_email("user-1")
    await gateway.delete_user("user-1")

    assert signed_calls == [
        {
            "url": "https://pvs-controlplane.us-east-1.prod.authn.identity.aws.dev/",
            "target": "AWSPasswordControlPlaneService.StartEmailVerification",
            "payload": {"UserId": "user-1", "IdentityStoreId": "d-1234567890"},
            "service": "sso-directory",
            "region": "us-east-1",
        }
    ]
    assert boto_calls == [
        (
            "identitystore",
            "delete_user",
            {"IdentityStoreId": "d-1234567890", "UserId": "user-1"},
        )
    ]
