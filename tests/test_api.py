from __future__ import annotations

from fastapi.testclient import TestClient

from app.report_service import MonthlyReport, UserMonthlyUsage
from tests.conftest import FakeGateway, FakeReportService


def test_health_and_static_page(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    response = client.get("/")
    assert response.status_code == 200
    assert "Kiro 管理台" in response.text


def test_login_script_keeps_form_reference_across_await(client: TestClient) -> None:
    index = client.get("/")
    script = client.get("/static/app.js")

    assert "app.js?v=20260906-9" in index.text
    assert "const formElement = event.currentTarget" in script.text
    assert "const form = new FormData(formElement)" in script.text
    assert "formElement.reset()" in script.text
    assert "event.currentTarget.reset()" not in script.text


def test_views_cache_after_first_load_and_show_update_times(client: TestClient) -> None:
    index = client.get("/").text
    script = client.get("/static/app.js").text

    for view in ("overview", "users", "subscriptions"):
        assert f'id="{view}-updated-at"' in index
        assert f"if (state.loaded.{view} && !force) return" in script
        assert f"state.loaded.{view} = true" in script
        assert f"markUpdated('{view}')" in script

    assert "async function loadOverview({ force = false } = {})" in script
    assert "async function loadUsers({ force = false } = {})" in script
    assert "async function loadSubscriptions({ force = false } = {})" in script
    assert "loadMonthlyReport({ force })" in script
    assert "cache: 'no-store'" in script
    assert "() => loadOverview({ force: true })" in script
    assert "() => loadUsers({ force: true })" in script
    assert "() => loadSubscriptions({ force: true })" in script


def test_authentication_cookie_flow(client: TestClient) -> None:
    assert client.get("/api/session").status_code == 401
    bad_login = client.post("/api/login", json={"username": "admin", "password": "bad"})
    assert bad_login.status_code == 401
    login = client.post("/api/login", json={"username": "admin", "password": "test-password-12345"})
    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    assert client.get("/api/session").json() == {"username": "admin"}
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/session").status_code == 401


def test_users_search_and_reset(logged_in_client: TestClient, fake_gateway: FakeGateway) -> None:
    response = logged_in_client.get("/api/users?q=alice")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    reset = logged_in_client.post("/api/users/user-1/reset-password")
    assert reset.status_code == 200
    assert fake_gateway.reset_user_ids == ["user-1"]


def test_overview_and_enriched_subscriptions(logged_in_client: TestClient) -> None:
    overview = logged_in_client.get("/api/overview").json()
    assert overview == {"users": 2, "subscriptions": 1, "active": 1, "pending": 0}
    subscription = logged_in_client.get("/api/subscriptions").json()["items"][0]
    assert subscription["user_name"] == "alice@example.com"
    assert subscription["email"] == "alice@example.com"


def test_subscription_actions(logged_in_client: TestClient, fake_gateway: FakeGateway) -> None:
    plan = "KIRO_ENTERPRISE_PRO_PLUS"
    assert (
        logged_in_client.post(
            "/api/subscriptions", json={"principal_id": "user-2", "subscription_type": plan}
        ).status_code
        == 200
    )
    assert (
        logged_in_client.patch(
            "/api/subscriptions/user-1", json={"subscription_type": plan}
        ).status_code
        == 200
    )
    assert logged_in_client.delete("/api/subscriptions/user-1").status_code == 200
    assert fake_gateway.actions == [
        ("assign", "user-2", plan),
        ("change", "user-1", plan),
        ("cancel", "user-1", None),
    ]


def test_invalid_plan_is_rejected_before_aws(logged_in_client: TestClient) -> None:
    response = logged_in_client.post(
        "/api/subscriptions",
        json={"principal_id": "user-2", "subscription_type": "UNKNOWN"},
    )
    assert response.status_code == 422


def test_change_subscription_rejects_invalid_state_and_noop(
    logged_in_client: TestClient, fake_gateway: FakeGateway
) -> None:
    unchanged = logged_in_client.patch(
        "/api/subscriptions/user-1",
        json={"subscription_type": "KIRO_ENTERPRISE_PRO"},
    )
    assert unchanged.status_code == 409
    assert unchanged.json()["error"]["code"] == "SUBSCRIPTION_PLAN_UNCHANGED"

    fake_gateway.subscriptions[0]["status"] = "pending"
    pending = logged_in_client.patch(
        "/api/subscriptions/user-1",
        json={"subscription_type": "KIRO_ENTERPRISE_PRO_PLUS"},
    )
    assert pending.status_code == 409
    assert pending.json()["error"]["code"] == "SUBSCRIPTION_INVALID_STATE"

    missing = logged_in_client.patch(
        "/api/subscriptions/missing-user",
        json={"subscription_type": "KIRO_ENTERPRISE_PRO_PLUS"},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SUBSCRIPTION_NOT_FOUND"
    assert fake_gateway.actions == []


def test_monthly_report_is_private_and_embedded(
    client: TestClient, fake_report_service: FakeReportService
) -> None:
    report_path = "/api/reports/monthly/view"
    assert client.get(report_path).status_code == 401
    assert client.get("/api/reports/2026-06/view").status_code == 404
    assert client.get("/api/reports/2026-06/script.js").status_code == 404
    assert client.get("/reports/2026-06").status_code == 404

    index = client.get("/")
    assert index.status_code == 200
    assert 'id="monthly-report-host"' in index.text
    assert "正在读取当前账号月度用量报告" in index.text
    assert "cdn.jsdelivr.net/npm/highcharts@12.1.2" in index.text
    assert "/static/monthly-report.js?v=20260906-2" in index.text
    assert "2026-06" not in index.text
    assert "948860674776" not in index.text
    assert "@ke.com" not in index.text
    assert "script-src 'self' https://cdn.jsdelivr.net" in index.headers["content-security-policy"]
    chart_script = client.get("/static/monthly-report.js")
    assert chart_script.status_code == 200
    assert chart_script.text.count("Highcharts.chart") == 5
    assert "window.initMonthlyReport" in chart_script.text
    assert "图表组件加载失败，请检查网络后刷新页面" in chart_script.text
    assert "948860674776" not in chart_script.text
    assert "@ke.com" not in chart_script.text

    login = client.post(
        "/api/login",
        json={"username": "admin", "password": "test-password-12345"},
    )
    assert login.status_code == 200

    empty = client.get(report_path)
    assert empty.status_code == 200
    assert "月度用量报告未配置" in empty.text
    assert "REPORT_BUCKET" in empty.text
    assert "948860674776" not in empty.text
    assert "@ke.com" not in empty.text
    assert "模拟数据" not in empty.text
    assert empty.headers["cache-control"] == "no-store"

    fake_report_service.report = MonthlyReport(
        status="ok",
        message="",
        account_id="828414850215",
        period="2026-09",
        data_through="2026-09-04",
        users=[
            UserMonthlyUsage(
                user_id="current-user",
                email="current-user@example.com",
                tier="PRO_PLUS",
                usage_limit=2000,
                overage_cap=10000,
                credits=2350.5,
                overage_credits=350.5,
                messages=420,
                model_messages={"gpt 5.6 sol": 420},
                latest_date="2026-09-04",
            )
        ],
        daily_credits={"2026-09-04": 2350.5},
        daily_client_credits={"2026-09-04": {"IDE": 2200.5, "CLI": 150}},
        client_credits={"IDE": 2200.5, "CLI": 150},
        model_messages={"gpt 5.6 sol": 420},
        source_objects=1,
    )
    populated = client.get(report_path)
    assert populated.status_code == 200
    assert "Kiro 企业版月度用量报告" in populated.text
    assert "828414850215" in populated.text
    assert "current-user@example.com" in populated.text
    assert "2026-09" in populated.text
    assert 'id="chart-daily"' in populated.text
    assert 'id="chart-model"' in populated.text
    assert 'id="chart-ide-cli"' in populated.text
    assert 'id="chart-user-quota"' in populated.text
    assert 'id="chart-overage"' in populated.text
    assert "套餐月度配额" in populated.text
    assert "用户明细（按 Credits 降序）" in populated.text
    assert "关键洞察" in populated.text
    assert "超额预警" in populated.text
    assert 'id="monthly-report-data"' in populated.text
    assert "948860674776" not in populated.text
    assert "@ke.com" not in populated.text
    assert "模拟数据" not in populated.text
    assert fake_report_service.calls == 2


def test_batch_create_users_supports_partial_results(
    logged_in_client: TestClient, fake_gateway: FakeGateway
) -> None:
    fake_gateway.create_user_failures.add("existing")
    response = logged_in_client.post(
        "/api/users/batch",
        json={
            "users": [
                {
                    "user_name": "new-user",
                    "display_name": "New User",
                    "email": "new@example.com",
                },
                {
                    "user_name": "existing",
                    "display_name": "Existing User",
                    "email": "existing@example.com",
                },
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["succeeded"] == 1
    assert response.json()["failed"] == 1
    assert response.json()["items"][1]["message"] == "用户已存在"
    assert fake_gateway.created_users == [("new-user", "New User", "new@example.com")]


def test_batch_create_users_rejects_invalid_or_duplicate_input(
    logged_in_client: TestClient, fake_gateway: FakeGateway
) -> None:
    invalid = logged_in_client.post(
        "/api/users/batch",
        json={"users": [{"user_name": "new-user", "display_name": "New User", "email": "bad"}]},
    )
    assert invalid.status_code == 422

    duplicate = logged_in_client.post(
        "/api/users/batch",
        json={
            "users": [
                {"user_name": "same", "display_name": "One", "email": "one@example.com"},
                {"user_name": "SAME", "display_name": "Two", "email": "two@example.com"},
            ]
        },
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["error"]["code"] == "DUPLICATE_USER_NAME"
    assert fake_gateway.created_users == []


def test_batch_subscription_assignment_returns_per_user_results(
    logged_in_client: TestClient, fake_gateway: FakeGateway
) -> None:
    fake_gateway.assign_subscription_failures.add("user-3")
    plan = "KIRO_ENTERPRISE_PRO_PLUS"
    response = logged_in_client.post(
        "/api/subscriptions/batch",
        json={"principal_ids": ["user-1", "user-2", "user-3"], "subscription_type": plan},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert response.json()["succeeded"] == 1
    assert response.json()["failed"] == 2
    assert response.json()["items"][0]["message"] == "用户已有 active 订阅"
    assert response.json()["items"][2]["message"] == "分配失败"
    assert fake_gateway.actions == [("assign", "user-2", plan)]


def test_batch_subscription_assignment_rejects_duplicate_users(
    logged_in_client: TestClient, fake_gateway: FakeGateway
) -> None:
    response = logged_in_client.post(
        "/api/subscriptions/batch",
        json={
            "principal_ids": ["user-2", "user-2"],
            "subscription_type": "KIRO_ENTERPRISE_PRO",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DUPLICATE_PRINCIPAL_ID"
    assert fake_gateway.actions == []


def test_batch_assignment_uses_native_dom_text_content(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert "function selectedAssignPlanLabel()" in script
    assert "selectedOption?.textContent?.trim()" in script
    assert "option:checked').text()" not in script


def test_user_csv_export_is_present_and_safely_encoded(client: TestClient) -> None:
    index = client.get("/").text
    script = client.get("/static/app.js").text
    assert 'id="export-users-button"' in index
    assert "function buildUsersCsv(users)" in script
    assert "['用户名称', '显示名称', '邮箱']" in script
    assert "return `\\ufeff${" in script
    assert ".join('\\r\\n')" in script
    assert "^\\s*[=+\\-@]" in script
    assert """protectedText.replaceAll('"', '""')""" in script
    assert "await api('/api/users')" in script


def test_batch_create_users_is_idempotent_for_existing_users(
    logged_in_client: TestClient, fake_gateway: FakeGateway
) -> None:
    response = logged_in_client.post(
        "/api/users/batch",
        json={
            "users": [
                {
                    "user_name": "alice@example.com",
                    "display_name": "Changed Display Name",
                    "email": "alice@example.com",
                }
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["created"] == 0
    assert response.json()["skipped"] == 1
    assert response.json()["failed"] == 0
    assert response.json()["items"][0]["status"] == "skipped"
    assert response.json()["items"][0]["message"] == "用户名称和邮箱已存在，已跳过"
    assert fake_gateway.created_users == []


def test_batch_create_users_reports_existing_email_in_chinese(
    logged_in_client: TestClient, fake_gateway: FakeGateway
) -> None:
    response = logged_in_client.post(
        "/api/users/batch",
        json={
            "users": [
                {
                    "user_name": "different-name",
                    "display_name": "Different Name",
                    "email": "alice@example.com",
                }
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["failed"] == 1
    assert response.json()["items"][0]["status"] == "failed"
    assert response.json()["items"][0]["message"] == ("邮箱已被现有用户 alice@example.com 使用")
    assert fake_gateway.created_users == []


def test_batch_create_users_dry_run_never_writes(
    logged_in_client: TestClient, fake_gateway: FakeGateway
) -> None:
    response = logged_in_client.post(
        "/api/users/batch",
        json={
            "dry_run": True,
            "users": [
                {
                    "user_name": "new-dry-run",
                    "display_name": "Dry Run",
                    "email": "dry-run@example.com",
                }
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["ready"] == 1
    assert response.json()["items"][0]["status"] == "ready"
    assert fake_gateway.created_users == []


def test_verify_email_and_reset_password_actions(
    logged_in_client: TestClient, fake_gateway: FakeGateway
) -> None:
    verify = logged_in_client.post("/api/users/user-1/verify-email")
    reset = logged_in_client.post("/api/users/user-1/reset-password")
    assert verify.status_code == reset.status_code == 200
    assert verify.json()["message"] == "邮箱验证邮件已发送"
    assert reset.json()["message"] == "密码重置邮件已发送"
    assert fake_gateway.verified_user_ids == ["user-1"]
    assert fake_gateway.reset_user_ids == ["user-1"]


def test_delete_user_requires_exact_name_and_no_active_subscription(
    logged_in_client: TestClient, fake_gateway: FakeGateway
) -> None:
    mismatch = logged_in_client.delete("/api/users/user-2", params={"confirm_user_name": "wrong"})
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == "DELETE_CONFIRMATION_MISMATCH"

    blocked = logged_in_client.delete(
        "/api/users/user-1", params={"confirm_user_name": "alice@example.com"}
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "USER_HAS_SUBSCRIPTION"

    deleted = logged_in_client.delete("/api/users/user-2", params={"confirm_user_name": "bob"})
    assert deleted.status_code == 200
    assert deleted.json()["message"] == "用户已删除"
    assert fake_gateway.deleted_user_ids == ["user-2"]
