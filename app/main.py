from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from app.auth import COOKIE_NAME, create_session, verify_session
from app.aws_gateway import AWSGateway, AWSGatewayError
from app.config import get_settings
from app.report_renderer import render_monthly_report
from app.report_service import S3MonthlyReportService

STATIC_DIR = Path(__file__).parent / "static"
Plan = Literal[
    "KIRO_ENTERPRISE_PRO",
    "KIRO_ENTERPRISE_PRO_PLUS",
    "KIRO_ENTERPRISE_PRO_MAX",
    "KIRO_ENTERPRISE_PRO_POWER",
]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SubscriptionRequest(BaseModel):
    principal_id: str = Field(min_length=1, max_length=128)
    subscription_type: Plan


class BatchSubscriptionRequest(BaseModel):
    principal_ids: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    ] = Field(min_length=1, max_length=100)
    subscription_type: Plan


class ChangeSubscriptionRequest(BaseModel):
    subscription_type: Plan


class BatchUserItem(BaseModel):
    user_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    display_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
    ]
    email: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=3,
            max_length=320,
            pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        ),
    ]


class BatchUsersRequest(BaseModel):
    users: list[BatchUserItem] = Field(min_length=1, max_length=100)
    dry_run: bool = False


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@lru_cache
def get_gateway() -> AWSGateway:
    return AWSGateway(get_settings())


@lru_cache
def get_report_service() -> S3MonthlyReportService:
    return S3MonthlyReportService(get_settings())


def require_admin(request: Request) -> str:
    settings = get_settings()
    payload = verify_session(request.cookies.get(COOKIE_NAME), settings)
    if payload is None:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "UNAUTHORIZED", "message": "请先登录"}},
        )
    return str(payload["sub"])


Admin = Annotated[str, Depends(require_admin)]
Gateway = Annotated[AWSGateway, Depends(get_gateway)]
ReportService = Annotated[S3MonthlyReportService, Depends(get_report_service)]
_login_attempts: dict[str, deque[float]] = defaultdict(deque)


def _check_login_rate_limit(client_ip: str) -> None:
    now = time.monotonic()
    attempts = _login_attempts[client_ip]
    while attempts and attempts[0] < now - 60:
        attempts.popleft()
    if len(attempts) >= 10:
        raise AppError("LOGIN_RATE_LIMIT", "登录尝试过多，请一分钟后重试", 429)
    attempts.append(now)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Kiro Simple Admin",
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' https://cdn.jsdelivr.net; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/reports/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(AWSGatewayError)
    async def aws_error_handler(_request: Request, exc: AWSGatewayError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "AWS_OPERATION_ERROR",
                    "message": f"AWS 操作 {exc.operation} 失败: {exc.message}",
                }
            },
        )

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "kiro-simple"}

    @app.get("/ready")
    async def ready():
        settings = get_settings()
        return {
            "status": "ready",
            "identity_store_id": settings.IDENTITY_STORE_ID,
            "region": settings.SSO_REGION,
        }

    @app.post("/api/login")
    async def login(body: LoginRequest, request: Request, response: Response):
        settings = get_settings()
        client_ip = request.client.host if request.client else "unknown"
        _check_login_rate_limit(client_ip)
        username_ok = secrets.compare_digest(body.username, settings.ADMIN_USERNAME)
        password_ok = secrets.compare_digest(body.password, settings.ADMIN_PASSWORD)
        if not username_ok or not password_ok:
            raise AppError("INVALID_CREDENTIALS", "用户名或密码错误", 401)
        _login_attempts.pop(client_ip, None)
        token = create_session(settings.ADMIN_USERNAME, settings)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=settings.SESSION_TTL_HOURS * 3600,
            httponly=True,
            secure=settings.COOKIE_SECURE,
            samesite="strict",
            path="/",
        )
        return {"username": settings.ADMIN_USERNAME}

    @app.post("/api/logout")
    async def logout(_admin: Admin, response: Response):
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"message": "已退出登录"}

    @app.get("/api/session")
    async def session(admin: Admin):
        return {"username": admin}

    @app.get("/api/users")
    async def users(_admin: Admin, gateway: Gateway, q: str | None = None):
        items = await gateway.list_users()
        if q:
            needle = q.lower().strip()
            items = [
                item
                for item in items
                if needle
                in " ".join(
                    str(item.get(key) or "") for key in ("user_name", "display_name", "email")
                ).lower()
            ]
        return {"items": items, "total": len(items)}

    @app.post("/api/users/batch")
    async def batch_create_users(body: BatchUsersRequest, _admin: Admin, gateway: Gateway):
        names = [item.user_name.casefold() for item in body.users]
        emails = [item.email.casefold() for item in body.users]
        if len(names) != len(set(names)):
            raise AppError("DUPLICATE_USER_NAME", "导入内容中存在重复的用户名称", 400)
        if len(emails) != len(set(emails)):
            raise AppError("DUPLICATE_EMAIL", "导入内容中存在重复的邮箱", 400)

        existing_users = await gateway.list_users()
        existing_by_name = {
            str(item.get("user_name") or "").casefold(): item
            for item in existing_users
            if item.get("user_name")
        }
        existing_by_email = {
            str(item.get("email") or "").casefold(): item
            for item in existing_users
            if item.get("email")
        }

        results = []
        for item in body.users:
            by_name = existing_by_name.get(item.user_name.casefold())
            by_email = existing_by_email.get(item.email.casefold())
            same_user = bool(
                by_name
                and by_email
                and (
                    by_name is by_email
                    or (
                        by_name.get("user_id") and by_name.get("user_id") == by_email.get("user_id")
                    )
                )
            )
            if same_user:
                results.append(
                    {
                        "user_name": item.user_name,
                        "status": "skipped",
                        "success": True,
                        "user_id": by_name.get("user_id"),
                        "message": "用户名称和邮箱已存在，已跳过",
                    }
                )
                continue
            if by_name and by_email:
                message = "用户名称和邮箱分别属于不同的现有用户"
            elif by_name:
                message = f"用户名称已存在（现有邮箱：{by_name.get('email') or '未设置'}）"
            elif by_email:
                message = (
                    f"邮箱已被现有用户 {by_email.get('user_name') or by_email.get('user_id')} 使用"
                )
            else:
                message = ""
            if message:
                results.append(
                    {
                        "user_name": item.user_name,
                        "status": "failed",
                        "success": False,
                        "user_id": None,
                        "message": message,
                    }
                )
                continue
            if body.dry_run:
                results.append(
                    {
                        "user_name": item.user_name,
                        "status": "ready",
                        "success": True,
                        "user_id": None,
                        "message": "校验通过，dry-run 未创建",
                    }
                )
                continue
            try:
                user_id = await gateway.create_user(item.user_name, item.display_name, item.email)
                results.append(
                    {
                        "user_name": item.user_name,
                        "status": "created",
                        "success": True,
                        "user_id": user_id,
                        "message": "创建成功，已请求 AWS 发送密码设置邀请邮件（链接最长有效 7 天）",
                    }
                )
            except AWSGatewayError as exc:
                message = (
                    "用户名称或邮箱已被其他用户使用，请刷新后重试"
                    if "ConflictException" in exc.message
                    else exc.message
                )
                results.append(
                    {
                        "user_name": item.user_name,
                        "status": "failed",
                        "success": False,
                        "user_id": None,
                        "message": message,
                    }
                )

        created = sum(item["status"] == "created" for item in results)
        skipped = sum(item["status"] == "skipped" for item in results)
        ready = sum(item["status"] == "ready" for item in results)
        failed = sum(item["status"] == "failed" for item in results)
        return {
            "total": len(results),
            "succeeded": created + skipped + ready,
            "created": created,
            "skipped": skipped,
            "ready": ready,
            "failed": failed,
            "items": results,
        }

    @app.post("/api/users/{user_id}/verify-email")
    async def verify_email(user_id: str, _admin: Admin, gateway: Gateway):
        await gateway.verify_email(user_id)
        return {"message": "邮箱验证邮件已发送"}

    @app.post("/api/users/{user_id}/reset-password")
    async def reset_password(user_id: str, _admin: Admin, gateway: Gateway):
        await gateway.reset_password(user_id)
        return {"message": "密码重置邮件已发送"}

    @app.delete("/api/users/{user_id}")
    async def delete_user(
        user_id: str,
        confirm_user_name: Annotated[str, Query(min_length=1, max_length=128)],
        _admin: Admin,
        gateway: Gateway,
    ):
        users_list, subscriptions_list = await asyncio.gather(
            gateway.list_users(), gateway.list_subscriptions()
        )
        user = next((item for item in users_list if item["user_id"] == user_id), None)
        if user is None:
            raise AppError("USER_NOT_FOUND", "未找到该用户", 404)
        if not secrets.compare_digest(confirm_user_name, user["user_name"]):
            raise AppError("DELETE_CONFIRMATION_MISMATCH", "确认用户名不匹配", 400)
        blocking_subscription = next(
            (
                item
                for item in subscriptions_list
                if item["principal_id"] == user_id and item["status"] in {"active", "pending"}
            ),
            None,
        )
        if blocking_subscription:
            raise AppError(
                "USER_HAS_SUBSCRIPTION",
                f"用户存在 {blocking_subscription['status']} 订阅，请先取消订阅",
                409,
            )
        await gateway.delete_user(user_id)
        return {"message": "用户已删除"}

    async def subscription_view(gateway: AWSGateway) -> list[dict]:
        users_list, subscriptions_list = await asyncio.gather(
            gateway.list_users(), gateway.list_subscriptions()
        )
        users_by_id = {item["user_id"]: item for item in users_list}
        result = []
        for subscription in subscriptions_list:
            user = users_by_id.get(subscription["principal_id"], {})
            result.append(
                {
                    **subscription,
                    "user_name": user.get("user_name"),
                    "display_name": user.get("display_name"),
                    "email": user.get("email"),
                }
            )
        return sorted(result, key=lambda item: (item.get("user_name") or "").lower())

    @app.get("/api/subscriptions")
    async def subscriptions(_admin: Admin, gateway: Gateway):
        items = await subscription_view(gateway)
        return {"items": items, "total": len(items)}

    @app.post("/api/subscriptions")
    async def assign_subscription(body: SubscriptionRequest, _admin: Admin, gateway: Gateway):
        await gateway.assign_subscription(body.principal_id, body.subscription_type)
        return {"message": "订阅已分配"}

    @app.post("/api/subscriptions/batch")
    async def batch_assign_subscriptions(
        body: BatchSubscriptionRequest, _admin: Admin, gateway: Gateway
    ):
        if len(body.principal_ids) != len(set(body.principal_ids)):
            raise AppError("DUPLICATE_PRINCIPAL_ID", "分配列表中存在重复用户", 400)

        current_subscriptions = {
            item["principal_id"]: item for item in await gateway.list_subscriptions()
        }
        results = []
        for principal_id in body.principal_ids:
            current = current_subscriptions.get(principal_id)
            if current and current["status"] in {"active", "pending"}:
                results.append(
                    {
                        "principal_id": principal_id,
                        "success": False,
                        "message": f"用户已有 {current['status']} 订阅",
                    }
                )
                continue
            try:
                await gateway.assign_subscription(principal_id, body.subscription_type)
                results.append(
                    {
                        "principal_id": principal_id,
                        "success": True,
                        "message": "分配请求已提交",
                    }
                )
            except AWSGatewayError as exc:
                results.append(
                    {
                        "principal_id": principal_id,
                        "success": False,
                        "message": exc.message,
                    }
                )

        succeeded = sum(item["success"] for item in results)
        return {
            "total": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "items": results,
        }

    @app.patch("/api/subscriptions/{principal_id}")
    async def change_subscription(
        principal_id: str,
        body: ChangeSubscriptionRequest,
        _admin: Admin,
        gateway: Gateway,
    ):
        subscriptions_list = await gateway.list_subscriptions()
        subscription = next(
            (item for item in subscriptions_list if item["principal_id"] == principal_id),
            None,
        )
        if subscription is None:
            raise AppError("SUBSCRIPTION_NOT_FOUND", "未找到该用户的订阅", 404)
        if subscription["status"] != "active":
            raise AppError(
                "SUBSCRIPTION_INVALID_STATE",
                f"订阅当前状态为 {subscription['status']}，仅 active 状态可以变更套餐",
                409,
            )
        if subscription["subscription_type"] == body.subscription_type:
            raise AppError(
                "SUBSCRIPTION_PLAN_UNCHANGED",
                f"当前订阅已是 {body.subscription_type}，无需重复变更",
                409,
            )
        await gateway.change_subscription(principal_id, body.subscription_type)
        return {"message": "订阅套餐已更新"}

    @app.delete("/api/subscriptions/{principal_id}")
    async def cancel_subscription(principal_id: str, _admin: Admin, gateway: Gateway):
        await gateway.cancel_subscription(principal_id)
        return {"message": "订阅已取消"}

    @app.get("/api/overview")
    async def overview(_admin: Admin, gateway: Gateway):
        users_list, subscriptions_list = await asyncio.gather(
            gateway.list_users(), gateway.list_subscriptions()
        )
        return {
            "users": len(users_list),
            "subscriptions": len(subscriptions_list),
            "active": sum(item["status"] == "active" for item in subscriptions_list),
            "pending": sum(item["status"] == "pending" for item in subscriptions_list),
        }

    @app.get("/api/reports/monthly/view", include_in_schema=False)
    async def monthly_report_view(
        _admin: Admin,
        report_service: ReportService,
        month: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
    ):
        report = await report_service.load_latest(month)
        return HTMLResponse(render_monthly_report(report))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
