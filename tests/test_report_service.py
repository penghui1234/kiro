from __future__ import annotations

import io

import pytest

from app.config import Settings
from app.report_renderer import build_monthly_report_chart_data, render_monthly_report
from app.report_service import S3MonthlyReportService


class FakeSTS:
    def get_caller_identity(self):
        return {"Account": "828414850215"}


class FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects
        self.list_calls: list[dict] = []

    def list_objects_v2(self, **kwargs):
        self.list_calls.append(kwargs)
        return {
            "Contents": [{"Key": key, "Size": len(value)} for key, value in self.objects.items()],
            "IsTruncated": False,
        }

    def get_object(self, *, Bucket: str, Key: str):
        assert Bucket == "fake-report-bucket"
        return {"Body": io.BytesIO(self.objects[Key])}


class FakeSession:
    def __init__(self, s3: FakeS3):
        self.s3 = s3

    def client(self, service: str, **_kwargs):
        if service == "sts":
            return FakeSTS()
        if service == "s3":
            return self.s3
        raise AssertionError(f"unexpected service: {service}")


def settings(**overrides) -> Settings:
    values = {
        "ADMIN_PASSWORD": "test-password-12345",
        "SESSION_SECRET": "test-session-secret-that-is-long-enough",
        "IDENTITY_STORE_ID": "d-1234567890",
        "INSTANCE_ARN": "arn:aws:sso:::instance/ssoins-1234567890abcdef",
        "REPORT_BUCKET": "fake-report-bucket",
        "REPORT_PREFIX": "user-activity-reports",
    }
    values.update(overrides)
    return Settings(**values)


def report_key(date: str, client: str = "IDE") -> str:
    compact = date.replace("-", "")
    year, month, day = date.split("-")
    return (
        "user-activity-reports/AWSLogs/828414850215/KiroLogs/user_report/"
        f"us-east-1/{year}/{month}/{day}/00/"
        f"KIRO_{client}_828414850215_user_report_{compact}0000.csv"
    )


HEADER = (
    "Date,UserId,Client_Type,Chat_Conversations,Credits_Used,Overage_Cap,"
    "Overage_Credits_Used,Overage_Enabled,ProfileId,Subscription_Tier,"
    "Total_Messages,New_User,User_Email,Usage_Limit,auto_messages,gpt_5.6_sol_messages\n"
)


@pytest.mark.asyncio
async def test_s3_report_service_aggregates_latest_month_with_fake_s3() -> None:
    objects = {
        report_key("2026-08-31"): (
            HEADER + "2026-08-31,user-1,KIRO_IDE,1,999,10000,0,true,p,PRO_PLUS,99,false,"
            "ignored@example.com,2000,0,99\n"
        ).encode(),
        report_key("2026-09-01"): (
            HEADER + "2026-09-01,user-1,KIRO_IDE,2,100.5,10000,0,true,p,PRO_PLUS,10,false,"
            "current@example.com,2000,2,8\n"
            + "2026-09-01,user-2,KIRO_IDE,1,50,10000,0,true,p,PRO,5,false,"
            "second@example.com,1000,1,4\n"
        ).encode(),
        report_key("2026-09-01", "CLI"): (
            HEADER + "2026-09-01,user-1,KIRO_CLI,1,25,10000,3,true,p,PRO_PLUS,4,false,"
            "current@example.com,2000,0,4\n"
        ).encode(),
    }
    fake_s3 = FakeS3(objects)
    service = S3MonthlyReportService(settings(), session=FakeSession(fake_s3))

    report = await service.load_latest()

    assert report.status == "ok"
    assert report.account_id == "828414850215"
    assert report.period == "2026-09"
    assert report.data_through == "2026-09-01"
    assert report.source_objects == 2
    assert report.total_credits == pytest.approx(175.5)
    assert report.total_quota == pytest.approx(3000)
    assert report.total_overage == pytest.approx(3)
    assert report.total_messages == 19
    assert report.client_credits == {"IDE": 150.5, "CLI": 25.0}
    assert report.daily_client_credits == {"2026-09-01": {"CLI": 25.0, "IDE": 150.5}}
    assert [user.email for user in report.users] == [
        "current@example.com",
        "second@example.com",
    ]
    assert fake_s3.list_calls[0]["Prefix"].endswith("/828414850215/KiroLogs/user_report/us-east-1/")

    html = render_monthly_report(report)
    chart_data = build_monthly_report_chart_data(report)
    assert chart_data["daily"] == {
        "categories": ["01"],
        "series": [
            {"name": "IDE", "data": [150.5]},
            {"name": "CLI", "data": [25.0]},
        ],
    }
    assert chart_data["quota"]["categories"] == ["current", "second"]
    assert chart_data["overage"]["credits"] == [3.0]
    assert "828414850215" in html
    assert "current@example.com" in html
    assert html.count('class="chart-300"') == 3
    assert html.count('class="chart-420"') == 2
    assert "关键洞察" in html
    assert 'id="monthly-report-data"' in html
    assert "ignored@example.com" not in html
    assert "948860674776" not in html
    assert "@ke.com" not in html


@pytest.mark.asyncio
async def test_report_service_has_safe_empty_and_failure_states() -> None:
    unconfigured = S3MonthlyReportService(settings(REPORT_BUCKET=""))
    assert (await unconfigured.load_latest()).status == "unconfigured"

    class BrokenSession:
        def client(self, _service: str, **_kwargs):
            raise RuntimeError("secret backend detail")

    unavailable = S3MonthlyReportService(settings(), session=BrokenSession())
    report = await unavailable.load_latest()
    assert report.status == "unavailable"
    assert "secret backend detail" not in report.message
