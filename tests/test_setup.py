from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import setup as local_setup

_CONFIG_ENV_KEYS = [
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "SESSION_SECRET",
    "SESSION_TTL_HOURS",
    "COOKIE_SECURE",
    "AWS_PROFILE",
    "SSO_REGION",
    "KIRO_REGION",
    "IDENTITY_STORE_ID",
    "INSTANCE_ARN",
    "REPORT_BUCKET",
    "REPORT_PREFIX",
    "REPORT_OVERAGE_PRICE_PER_CREDIT",
]


def clear_config_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_env_file_round_trip_and_secret_permissions(tmp_path: Path) -> None:
    config = {
        "ADMIN_USERNAME": "admin user",
        "ADMIN_PASSWORD": 'long-password-with-"quote"',
        "SESSION_SECRET": "s" * 48,
        "SESSION_TTL_HOURS": "12",
        "COOKIE_SECURE": "false",
        "AWS_PROFILE": "",
        "SSO_REGION": "us-east-1",
        "KIRO_REGION": "us-east-1",
        "IDENTITY_STORE_ID": "d-1234567890",
        "INSTANCE_ARN": "arn:aws:sso:::instance/ssoins-1234567890abcdef",
        "REPORT_BUCKET": "",
        "REPORT_PREFIX": "user-activity-reports",
        "REPORT_OVERAGE_PRICE_PER_CREDIT": "0.04",
    }
    env_file = tmp_path / ".env"

    local_setup.write_env_file(config, env_file)

    parsed = local_setup.parse_env_file(env_file)
    assert parsed == {key: value for key, value in config.items() if key != "AWS_PROFILE"}
    assert "AWS_PROFILE=" not in env_file.read_text()
    if local_setup.os.name != "nt":
        assert env_file.stat().st_mode & 0o777 == 0o600


def test_non_interactive_configuration_generates_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_config_environment(monkeypatch)
    monkeypatch.setenv("IDENTITY_STORE_ID", "d-1234567890")
    monkeypatch.setenv("INSTANCE_ARN", "arn:aws:sso:::instance/ssoins-1234567890abcdef")

    config = local_setup.build_configuration({}, non_interactive=True)

    assert config["ADMIN_USERNAME"] == "admin"
    assert len(config["ADMIN_PASSWORD"]) >= 12
    assert len(config["SESSION_SECRET"]) >= 32
    assert config["AWS_PROFILE"] == ""
    assert config["REPORT_PREFIX"] == "user-activity-reports"


def test_existing_configuration_is_preserved_non_interactively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_config_environment(monkeypatch)
    existing = {
        "ADMIN_USERNAME": "existing-admin",
        "ADMIN_PASSWORD": "existing-password",
        "SESSION_SECRET": "x" * 40,
        "SESSION_TTL_HOURS": "24",
        "COOKIE_SECURE": "true",
        "SSO_REGION": "eu-west-1",
        "KIRO_REGION": "eu-west-1",
        "IDENTITY_STORE_ID": "d-existing",
        "INSTANCE_ARN": "arn:aws:sso:::instance/ssoins-existing",
        "REPORT_BUCKET": "reports",
        "REPORT_PREFIX": "prefix",
        "REPORT_OVERAGE_PRICE_PER_CREDIT": "0.05",
    }

    config = local_setup.build_configuration(existing, non_interactive=True)

    assert config["ADMIN_USERNAME"] == "existing-admin"
    assert config["ADMIN_PASSWORD"] == "existing-password"
    assert config["COOKIE_SECURE"] == "true"
    assert config["REPORT_BUCKET"] == "reports"


def test_configuration_validation_rejects_unsafe_values() -> None:
    config = {
        "ADMIN_PASSWORD": "short",
        "SESSION_SECRET": "x" * 40,
        "SESSION_TTL_HOURS": "12",
        "COOKIE_SECURE": "false",
        "IDENTITY_STORE_ID": "d-test",
        "INSTANCE_ARN": "arn:aws:sso:::instance/ssoins-test",
        "REPORT_OVERAGE_PRICE_PER_CREDIT": "0.04",
    }
    with pytest.raises(SystemExit):
        local_setup.validate_configuration(config)


def test_setup_parser_exposes_simple_run_and_check_commands() -> None:
    parser = local_setup.build_parser()
    run_args = parser.parse_args(["--run", "--host", "0.0.0.0", "--port", "8080"])
    check_args = parser.parse_args(["--check", "--skip-aws-check"])

    assert run_args.run is True
    assert run_args.host == "0.0.0.0"
    assert run_args.port == 8080
    assert check_args.check is True
    assert check_args.skip_aws_check is True


def test_venv_python_uses_platform_specific_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_setup.os, "name", "nt")
    assert local_setup.venv_python() == local_setup.VENV_DIR / "Scripts" / "python.exe"
    monkeypatch.setattr(local_setup.os, "name", "posix")
    assert local_setup.venv_python() == local_setup.VENV_DIR / "bin" / "python"


def valid_configuration() -> dict[str, str]:
    return {
        "ADMIN_PASSWORD": "valid-password-12345",
        "SESSION_SECRET": "x" * 40,
        "SESSION_TTL_HOURS": "12",
        "COOKIE_SECURE": "false",
        "IDENTITY_STORE_ID": "d-test",
        "INSTANCE_ARN": "arn:aws:sso:::instance/ssoins-test",
        "REPORT_OVERAGE_PRICE_PER_CREDIT": "0.04",
    }


def test_configuration_accepts_legacy_uuid_identity_store_id() -> None:
    config = valid_configuration()
    config["IDENTITY_STORE_ID"] = "12345678-1234-1234-1234-1234567890ab"
    local_setup.validate_configuration(config)


def test_secure_env_file_repairs_existing_posix_permissions(tmp_path: Path) -> None:
    if local_setup.os.name == "nt":
        pytest.skip("POSIX permission test")
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_PASSWORD=secret\n")
    env_file.chmod(0o644)

    local_setup.secure_env_file(env_file)

    assert env_file.stat().st_mode & 0o777 == 0o600


def test_secure_env_file_rebuilds_protected_windows_acl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_PASSWORD=secret\n")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(local_setup.os, "name", "nt")
    monkeypatch.setenv("USERNAME", "test-user")
    monkeypatch.setenv("USERDOMAIN", "TESTDOMAIN")
    monkeypatch.setattr(local_setup.subprocess, "run", fake_run)

    local_setup.secure_env_file(env_file)

    assert len(calls) == 1
    command = calls[0]
    assert command[:4] == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
    ]
    assert "$acl.SetAccessRuleProtection($true, $false)" in command[4]
    assert "NT AUTHORITY\\SYSTEM" in command[4]
    assert command[-2:] == [str(env_file), "TESTDOMAIN\\test-user"]


def test_aws_check_rejects_unavailable_configured_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "account": "123456789012",
        "arn": "arn:aws:iam::123456789012:role/test",
        "users": 1,
        "subscriptions": 1,
        "report": "unavailable",
    }

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(local_setup.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        local_setup.check_aws_read_only(Path("python"))


def test_generated_start_command_keeps_host_port_and_powershell_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_setup.os, "name", "posix")
    posix = local_setup.format_start_command(Path("/venv/python"), "0.0.0.0", 8080)
    monkeypatch.setattr(local_setup.os, "name", "nt")
    windows = local_setup.format_start_command(Path("C:/venv/python.exe"), "0.0.0.0", 8080)

    assert posix.endswith("--run --host 0.0.0.0 --port 8080")
    assert windows.startswith("& ")
    assert windows.endswith("--run --host 0.0.0.0 --port 8080")


def test_check_with_missing_env_does_not_create_virtualenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = False

    def unexpected_ensure(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("ensure_virtualenv must not run")

    monkeypatch.setattr(local_setup, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(local_setup, "ensure_virtualenv", unexpected_ensure)

    with pytest.raises(SystemExit):
        local_setup.main(["--check", "--skip-aws-check"])
    assert called is False


def test_server_failure_is_mapped_to_installer_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(local_setup.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        local_setup.run_server(Path("python"), "127.0.0.1", 8000)


def test_check_with_existing_env_does_not_change_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("test=true\n")
    if local_setup.os.name != "nt":
        env_file.chmod(0o644)
    python = tmp_path / "python"
    python.touch()
    secure_called = False

    def unexpected_secure(_path):
        nonlocal secure_called
        secure_called = True

    monkeypatch.setattr(local_setup, "ENV_FILE", env_file)
    monkeypatch.setattr(local_setup, "venv_python", lambda: python)
    monkeypatch.setattr(local_setup, "secure_env_file", unexpected_secure)
    monkeypatch.setattr(local_setup, "validate_runtime", lambda _python: None)

    before = env_file.stat()
    assert local_setup.main(["--check", "--skip-aws-check"]) == 0
    after = env_file.stat()
    assert secure_called is False
    assert after.st_mode == before.st_mode
    assert after.st_mtime_ns == before.st_mtime_ns


@pytest.mark.skipif(local_setup.os.name != "nt", reason="Windows ACL integration test")
def test_windows_acl_contains_only_current_user_and_system(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_PASSWORD=secret\n")

    local_setup.secure_env_file(env_file)

    script = (
        "(Get-Acl -LiteralPath $args[0]).Access | "
        "ForEach-Object { $_.IdentityReference.Value } | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script, str(env_file)],
        text=True,
        capture_output=True,
        check=True,
    )
    identities = json.loads(result.stdout)
    if isinstance(identities, str):
        identities = [identities]
    normalized = {identity.lower() for identity in identities}
    username = (local_setup.os.environ.get("USERNAME") or "").lower()
    assert len(normalized) == 2
    assert any(identity.endswith("\\system") for identity in normalized)
    assert any(identity.endswith(f"\\{username}") for identity in normalized)
