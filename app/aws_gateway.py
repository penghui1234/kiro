from __future__ import annotations

import asyncio
import json
from typing import Any

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from app.config import Settings

SUBSCRIPTION_TYPE_FALLBACKS = {
    "KIRO_ENTERPRISE_PRO": "Q_DEVELOPER_STANDALONE_PRO",
    "KIRO_ENTERPRISE_PRO_PLUS": "Q_DEVELOPER_STANDALONE_PRO_PLUS",
    "KIRO_ENTERPRISE_PRO_POWER": "Q_DEVELOPER_STANDALONE_POWER",
}


class AWSGatewayError(Exception):
    def __init__(self, operation: str, message: str, status_code: int = 502):
        self.operation = operation
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _amazon_q_type(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("amazonQ") or value.get("AmazonQ") or "")
    return str(value or "")


def normalize_subscription(raw: dict[str, Any]) -> dict[str, Any]:
    principal_id = raw.get("PrincipalId") or raw.get("principalId")
    principal = raw.get("principal")
    if not principal_id and isinstance(principal, dict):
        principal_id = principal.get("user") or principal.get("User")

    subscription_type = raw.get("SubscriptionType") or raw.get("subscriptionType")
    if not subscription_type:
        subscription_type = _amazon_q_type(raw.get("activatedType")) or _amazon_q_type(
            raw.get("type")
        )

    status = str(raw.get("Status") or raw.get("status") or "unknown").lower()
    if status == "cancelled":
        status = "canceled"

    return {
        "principal_id": str(principal_id or ""),
        "subscription_type": str(subscription_type or ""),
        "status": status,
        "aggregated_type": raw.get("aggregatedSubscriptionType"),
        "activation_date": raw.get("activationDate"),
        "usage": raw.get("Usage") or raw.get("usage"),
    }


class AWSGateway:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = boto3.Session(
            profile_name=settings.AWS_PROFILE or None,
            region_name=settings.SSO_REGION,
        )

    async def _boto_call(self, service: str, method: str, **kwargs: Any) -> dict[str, Any]:
        client = self.session.client(service, region_name=self.settings.SSO_REGION)

        def call() -> dict[str, Any]:
            return getattr(client, method)(**kwargs)

        try:
            return await asyncio.to_thread(call)
        except Exception as exc:
            raise AWSGatewayError(f"{service}.{method}", str(exc)) from exc

    async def _sigv4_post(
        self,
        *,
        url: str,
        target: str,
        payload: dict[str, Any],
        service: str,
        region: str,
    ) -> dict[str, Any]:
        credentials = self.session.get_credentials()
        if credentials is None:
            raise AWSGatewayError(target, "未找到 AWS 凭证")
        body = json.dumps(payload, separators=(",", ":"))
        request = AWSRequest(
            method="POST",
            url=url,
            data=body.encode(),
            headers={
                "Content-Type": "application/x-amz-json-1.0",
                "X-Amz-Target": target,
            },
        )
        SigV4Auth(credentials.get_frozen_credentials(), service, region).add_auth(request)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    url, content=body.encode(), headers=dict(request.headers)
                )
        except httpx.HTTPError as exc:
            raise AWSGatewayError(target, str(exc)) from exc
        if response.status_code not in {200, 201, 202, 204}:
            try:
                data = response.json()
                code = data.get("__type") or data.get("code") or f"HTTP_{response.status_code}"
                message = data.get("message") or data.get("Message") or response.text
            except ValueError:
                code = f"HTTP_{response.status_code}"
                message = response.text[:500]
            raise AWSGatewayError(target, f"[{code}] {message}")
        return response.json() if response.content else {}

    async def list_users(self) -> list[dict[str, Any]]:
        users: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            params: dict[str, Any] = {
                "IdentityStoreId": self.settings.IDENTITY_STORE_ID,
                "MaxResults": 100,
            }
            if token:
                params["NextToken"] = token
            response = await self._boto_call("identitystore", "list_users", **params)
            for raw in response.get("Users", []):
                emails = raw.get("Emails", [])
                email = next(
                    (item.get("Value") for item in emails if item.get("Primary")), None
                ) or next((item.get("Value") for item in emails if item.get("Value")), None)
                users.append(
                    {
                        "user_id": raw.get("UserId", ""),
                        "user_name": raw.get("UserName", ""),
                        "display_name": raw.get("DisplayName"),
                        "email": email,
                    }
                )
            token = response.get("NextToken")
            if not token:
                break
        return sorted(users, key=lambda item: item["user_name"].lower())

    async def create_user(self, user_name: str, display_name: str, email: str) -> str:
        name_parts = display_name.split(maxsplit=1)
        given_name = name_parts[0]
        family_name = name_parts[1] if len(name_parts) > 1 else name_parts[0]
        response = await self._sigv4_post(
            url=(f"https://identitystore.{self.settings.SSO_REGION}.amazonaws.com/identitystore/"),
            target="AWSIdentityStoreService.CreateUser",
            payload={
                "IdentityStoreId": self.settings.IDENTITY_STORE_ID,
                "UserName": user_name,
                "UserAttributes": {
                    "emails": {
                        "ComplexListValue": [
                            {
                                "value": {"StringValue": email},
                                "type": {"StringValue": "work"},
                                "primary": {"BooleanValue": True},
                            }
                        ]
                    },
                    "name": {
                        "ComplexValue": {
                            "givenName": {"StringValue": given_name},
                            "familyName": {"StringValue": family_name},
                        }
                    },
                    "displayName": {"StringValue": display_name},
                },
                "Active": True,
                "PasswordMode": "EMAIL",
            },
            service="identitystore",
            region=self.settings.SSO_REGION,
        )
        user = response.get("User")
        user_id = str(user.get("UserId") or "") if isinstance(user, dict) else ""
        if not user_id:
            raise AWSGatewayError("AWSIdentityStoreService.CreateUser", "AWS 响应缺少 User.UserId")
        return user_id

    async def verify_email(self, user_id: str) -> None:
        await self._sigv4_post(
            url=(
                f"https://pvs-controlplane.{self.settings.SSO_REGION}.prod.authn.identity.aws.dev/"
            ),
            target="AWSPasswordControlPlaneService.StartEmailVerification",
            payload={
                "UserId": user_id,
                "IdentityStoreId": self.settings.IDENTITY_STORE_ID,
            },
            service="sso-directory",
            region=self.settings.SSO_REGION,
        )

    async def reset_password(self, user_id: str) -> None:
        await self._sigv4_post(
            url=f"https://identitystore.{self.settings.SSO_REGION}.amazonaws.com/",
            target="SWBUPService.UpdatePassword",
            payload={"UserId": user_id, "PasswordMode": "EMAIL"},
            service="userpool",
            region=self.settings.SSO_REGION,
        )

    async def delete_user(self, user_id: str) -> None:
        await self._boto_call(
            "identitystore",
            "delete_user",
            IdentityStoreId=self.settings.IDENTITY_STORE_ID,
            UserId=user_id,
        )

    async def list_subscriptions(self) -> list[dict[str, Any]]:
        subscriptions: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            payload: dict[str, Any] = {
                "instanceArn": self.settings.INSTANCE_ARN,
                "maxResults": 100,
                "subscriptionRegion": self.settings.KIRO_REGION,
            }
            if token:
                payload["nextToken"] = token
            response = await self._sigv4_post(
                url=f"https://service.user-subscriptions.{self.settings.SSO_REGION}.amazonaws.com/",
                target="AWSZornControlPlaneService.ListUserSubscriptions",
                payload=payload,
                service="user-subscriptions",
                region=self.settings.SSO_REGION,
            )
            items = response.get("subscriptions")
            if items is None:
                items = response.get("UserSubscriptions", [])
            subscriptions.extend(normalize_subscription(item) for item in items)
            token = response.get("nextToken") or response.get("NextToken")
            if not token:
                break
        return subscriptions

    async def _subscription_action(
        self, target: str, principal_id: str, subscription_type: str | None = None
    ) -> dict[str, Any]:
        payload = {
            "instanceArn": self.settings.INSTANCE_ARN,
            "principalId": principal_id,
            "principalType": "USER",
        }
        if subscription_type:
            payload["subscriptionType"] = subscription_type
        try:
            return await self._sigv4_post(
                url=f"https://codewhisperer.{self.settings.KIRO_REGION}.amazonaws.com/",
                target=f"AmazonQDeveloperService.{target}",
                payload=payload,
                service="q",
                region=self.settings.KIRO_REGION,
            )
        except AWSGatewayError as exc:
            if "ConflictException" in exc.message:
                raise AWSGatewayError(
                    exc.operation,
                    "资源当前状态不允许此操作。请刷新订阅并确认状态和目标套餐；"
                    "如果来源包含 GROUP，请在对应组分配中调整",
                    409,
                ) from exc
            fallback = SUBSCRIPTION_TYPE_FALLBACKS.get(subscription_type or "")
            if target not in {"CreateAssignment", "UpdateAssignment"} or not fallback:
                raise
            if "ValidationException" not in exc.message:
                raise
            payload["subscriptionType"] = fallback
            return await self._sigv4_post(
                url=f"https://codewhisperer.{self.settings.KIRO_REGION}.amazonaws.com/",
                target=f"AmazonQDeveloperService.{target}",
                payload=payload,
                service="q",
                region=self.settings.KIRO_REGION,
            )

    async def assign_subscription(self, principal_id: str, subscription_type: str) -> None:
        await self._subscription_action("CreateAssignment", principal_id, subscription_type)

    async def change_subscription(self, principal_id: str, subscription_type: str) -> None:
        await self._subscription_action("UpdateAssignment", principal_id, subscription_type)

    async def cancel_subscription(self, principal_id: str) -> None:
        await self._subscription_action("DeleteAssignment", principal_id)
