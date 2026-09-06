from __future__ import annotations

import asyncio
import csv
import io
import math
import re
from dataclasses import dataclass, field
from typing import Literal

import boto3

from app.config import Settings

_REPORT_KEY = re.compile(
    r"/user_report/(?P<region>[^/]+)/(?P<year>\d{4})/(?P<month>\d{2})/"
    r"(?P<day>\d{2})/\d{2}/KIRO_(?P<client>[A-Z]+)_(?P<account>\d{12})_"
    r"user_report_(?P<stamp>\d{8})\d{4}\.csv$"
)
_REQUIRED_COLUMNS = {
    "Date",
    "UserId",
    "Client_Type",
    "Credits_Used",
    "Subscription_Tier",
    "Total_Messages",
    "Usage_Limit",
}
_MAX_REPORT_OBJECT_SIZE = 5 * 1024 * 1024


@dataclass
class UserMonthlyUsage:
    user_id: str
    email: str
    tier: str = "UNKNOWN"
    usage_limit: float = 0
    overage_cap: float = 0
    credits: float = 0
    overage_credits: float = 0
    messages: int = 0
    conversations: int = 0
    model_messages: dict[str, int] = field(default_factory=dict)
    latest_date: str = ""


@dataclass
class MonthlyReport:
    status: Literal["ok", "unconfigured", "no_data", "unavailable"]
    message: str
    account_id: str | None = None
    period: str | None = None
    data_through: str | None = None
    users: list[UserMonthlyUsage] = field(default_factory=list)
    daily_credits: dict[str, float] = field(default_factory=dict)
    daily_client_credits: dict[str, dict[str, float]] = field(default_factory=dict)
    client_credits: dict[str, float] = field(default_factory=dict)
    model_messages: dict[str, int] = field(default_factory=dict)
    source_objects: int = 0
    overage_price_per_credit: float = 0.04

    @property
    def total_credits(self) -> float:
        return sum(user.credits for user in self.users)

    @property
    def total_quota(self) -> float:
        return sum(user.usage_limit for user in self.users)

    @property
    def total_overage(self) -> float:
        return sum(user.overage_credits for user in self.users)

    @property
    def total_messages(self) -> int:
        return sum(user.messages for user in self.users)


class S3MonthlyReportService:
    def __init__(self, settings: Settings, session=None):
        self.settings = settings
        self.session = session or boto3.Session(
            profile_name=settings.AWS_PROFILE or None,
            region_name=settings.SSO_REGION,
        )

    async def load_latest(self, period: str | None = None) -> MonthlyReport:
        if not self.settings.REPORT_BUCKET:
            return MonthlyReport(
                status="unconfigured",
                period=period,
                message="尚未配置 Kiro User Activity Reports 的 S3 桶。",
            )
        try:
            return await asyncio.to_thread(self._load_latest_sync, period)
        except Exception:
            return MonthlyReport(
                status="unavailable",
                period=period,
                message="当前无法读取 Kiro User Activity Reports，请稍后刷新。",
            )

    def _load_latest_sync(self, period: str | None = None) -> MonthlyReport:
        account_id = str(self.session.client("sts").get_caller_identity()["Account"])
        prefix = self.settings.REPORT_PREFIX.strip("/")
        base_prefix = (
            f"{prefix}/AWSLogs/{account_id}/KiroLogs/user_report/{self.settings.KIRO_REGION}/"
        )
        s3 = self.session.client("s3", region_name=self.settings.KIRO_REGION)
        objects = self._list_report_objects(s3, base_prefix, account_id)
        if not objects:
            return MonthlyReport(
                status="no_data",
                account_id=account_id,
                period=period,
                message=(
                    f"{period} 暂无可用的 Kiro User Activity Reports。"
                    if period
                    else "当前账号尚无可用的 Kiro User Activity Reports。"
                ),
            )

        selected_period = period or max(item[1][:7] for item in objects)
        selected = [item for item in objects if item[1].startswith(selected_period)]
        if not selected:
            return MonthlyReport(
                status="no_data",
                account_id=account_id,
                period=selected_period,
                message=f"{selected_period} 暂无可用的 Kiro User Activity Reports。",
            )
        report = MonthlyReport(
            status="ok",
            message="",
            account_id=account_id,
            period=selected_period,
            overage_price_per_credit=self.settings.REPORT_OVERAGE_PRICE_PER_CREDIT,
        )
        parsed_objects = 0
        for key, _date, size in selected:
            if size > _MAX_REPORT_OBJECT_SIZE:
                continue
            response = s3.get_object(Bucket=self.settings.REPORT_BUCKET, Key=key)
            body = response["Body"]
            try:
                raw = body.read(_MAX_REPORT_OBJECT_SIZE + 1)
            finally:
                close = getattr(body, "close", None)
                if close:
                    close()
            if len(raw) > _MAX_REPORT_OBJECT_SIZE:
                continue
            parsed_objects += self._merge_csv(report, raw)

        if not report.users:
            return MonthlyReport(
                status="no_data",
                account_id=account_id,
                period=selected_period,
                message=f"{selected_period} 暂无可解析的 Kiro 用户用量数据。",
            )
        report.source_objects = parsed_objects
        report.users.sort(key=lambda user: user.credits, reverse=True)
        report.daily_credits = dict(sorted(report.daily_credits.items()))
        report.daily_client_credits = {
            date: dict(sorted(clients.items()))
            for date, clients in sorted(report.daily_client_credits.items())
        }
        report.client_credits = dict(
            sorted(report.client_credits.items(), key=lambda item: item[1], reverse=True)
        )
        report.model_messages = dict(
            sorted(report.model_messages.items(), key=lambda item: item[1], reverse=True)
        )
        report.data_through = max(report.daily_credits)
        return report

    def _list_report_objects(self, s3, prefix: str, account_id: str) -> list[tuple[str, str, int]]:
        objects: list[tuple[str, str, int]] = []
        continuation_token: str | None = None
        while True:
            params: dict[str, object] = {
                "Bucket": self.settings.REPORT_BUCKET,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if continuation_token:
                params["ContinuationToken"] = continuation_token
            response = s3.list_objects_v2(**params)
            for item in response.get("Contents", []):
                key = str(item.get("Key") or "")
                match = _REPORT_KEY.search(key)
                if not match or match.group("account") != account_id:
                    continue
                report_date = f"{match.group('year')}-{match.group('month')}-{match.group('day')}"
                objects.append((key, report_date, int(item.get("Size") or 0)))
            continuation_token = response.get("NextContinuationToken")
            if not continuation_token:
                break
        return objects

    @staticmethod
    def _merge_csv(report: MonthlyReport, raw: bytes) -> int:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        if not reader.fieldnames or not _REQUIRED_COLUMNS.issubset(reader.fieldnames):
            return 0

        users_by_id = {user.user_id: user for user in report.users}
        rows = 0
        for row in reader:
            date = str(row.get("Date") or "").strip()
            if not report.period or not date.startswith(report.period):
                continue
            user_id = str(row.get("UserId") or "").strip()
            email = str(row.get("User_Email") or "").strip()
            if not user_id and not email:
                continue
            key = user_id or email.lower()
            user = users_by_id.get(key)
            if user is None:
                user = UserMonthlyUsage(user_id=key, email=email or user_id)
                users_by_id[key] = user
                report.users.append(user)

            credits = _number(row.get("Credits_Used"))
            overage = _number(row.get("Overage_Credits_Used"))
            messages = _integer(row.get("Total_Messages"))
            user.credits += credits
            user.overage_credits += overage
            user.messages += messages
            user.conversations += _integer(row.get("Chat_Conversations"))
            report.daily_credits[date] = report.daily_credits.get(date, 0) + credits

            client = str(row.get("Client_Type") or "UNKNOWN").removeprefix("KIRO_")
            report.client_credits[client] = report.client_credits.get(client, 0) + credits
            daily_clients = report.daily_client_credits.setdefault(date, {})
            daily_clients[client] = daily_clients.get(client, 0) + credits

            for column, value in row.items():
                if not column.lower().endswith("_messages") or column == "Total_Messages":
                    continue
                model = column[: -len("_messages")].replace("_", " ")
                count = _integer(value)
                if not count:
                    continue
                user.model_messages[model] = user.model_messages.get(model, 0) + count
                report.model_messages[model] = report.model_messages.get(model, 0) + count

            if date >= user.latest_date:
                user.latest_date = date
                user.email = email or user.email
                user.tier = str(row.get("Subscription_Tier") or "UNKNOWN")
                user.usage_limit = _number(row.get("Usage_Limit"))
                user.overage_cap = _number(row.get("Overage_Cap"))
            rows += 1
        return int(rows > 0)


def _number(value: object) -> float:
    try:
        number = float(str(value or 0))
    except (TypeError, ValueError):
        return 0
    return number if math.isfinite(number) else 0


def _integer(value: object) -> int:
    return max(0, int(_number(value)))
