#!/usr/bin/env python3
"""Cross-platform installer and launcher for Kiro Simple Admin."""

from __future__ import annotations

import argparse
import ast
import getpass
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
ENV_FILE = ROOT / ".env"
REQUIREMENTS_FILE = ROOT / "requirements.txt"
REQUIREMENTS_MARKER = VENV_DIR / ".kiro-requirements.sha256"
MIN_PYTHON = (3, 12)


def fail(message: str) -> None:
    print(f"错误：{message}", file=sys.stderr)
    raise SystemExit(1)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value[:1] in {'"', "'"} and raw_value[-1:] == raw_value[:1]:
            try:
                value = ast.literal_eval(raw_value)
            except (SyntaxError, ValueError):
                value = raw_value[1:-1]
        else:
            value = raw_value
        values[key] = str(value)
    return values


def quote_env(value: str) -> str:
    if value and re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def prompt_value(
    label: str,
    *,
    default: str = "",
    required: bool = False,
    secret: bool = False,
) -> str:
    while True:
        suffix = " [保持现有值]" if secret and default else f" [{default}]" if default else ""
        prompt = f"{label}{suffix}: "
        value = getpass.getpass(prompt) if secret else input(prompt)
        value = value.strip()
        if not value:
            value = default
        if value or not required:
            return value
        print("该项不能为空。")


def env_or_default(name: str, existing: dict[str, str], default: str = "") -> str:
    return os.environ.get(name, existing.get(name, default)).strip()


def build_configuration(existing: dict[str, str], non_interactive: bool) -> dict[str, str]:
    defaults = {
        "ADMIN_USERNAME": env_or_default("ADMIN_USERNAME", existing, "admin"),
        "ADMIN_PASSWORD": env_or_default("ADMIN_PASSWORD", existing),
        "SESSION_SECRET": env_or_default("SESSION_SECRET", existing),
        "SESSION_TTL_HOURS": env_or_default("SESSION_TTL_HOURS", existing, "12"),
        "COOKIE_SECURE": env_or_default("COOKIE_SECURE", existing, "false"),
        "AWS_PROFILE": env_or_default("AWS_PROFILE", existing),
        "SSO_REGION": env_or_default("SSO_REGION", existing, "us-east-1"),
        "KIRO_REGION": env_or_default("KIRO_REGION", existing, "us-east-1"),
        "IDENTITY_STORE_ID": env_or_default("IDENTITY_STORE_ID", existing),
        "INSTANCE_ARN": env_or_default("INSTANCE_ARN", existing),
        "REPORT_BUCKET": env_or_default("REPORT_BUCKET", existing),
        "REPORT_PREFIX": env_or_default("REPORT_PREFIX", existing, "user-activity-reports"),
        "REPORT_OVERAGE_PRICE_PER_CREDIT": env_or_default(
            "REPORT_OVERAGE_PRICE_PER_CREDIT", existing, "0.04"
        ),
    }

    generated_password = False
    if non_interactive:
        config = defaults
    else:
        print("\n配置 Kiro 管理台。直接回车可接受默认值或保留现有值。")
        config = {
            "ADMIN_USERNAME": prompt_value(
                "管理员用户名", default=defaults["ADMIN_USERNAME"], required=True
            ),
            "ADMIN_PASSWORD": prompt_value(
                "管理员密码（留空自动生成）",
                default=defaults["ADMIN_PASSWORD"],
                secret=True,
            ),
            "SESSION_SECRET": defaults["SESSION_SECRET"],
            "SESSION_TTL_HOURS": prompt_value(
                "会话有效小时数", default=defaults["SESSION_TTL_HOURS"], required=True
            ),
            "COOKIE_SECURE": prompt_value(
                "是否只允许 HTTPS Cookie（true/false）",
                default=defaults["COOKIE_SECURE"],
                required=True,
            ).lower(),
            "AWS_PROFILE": prompt_value(
                "AWS profile（使用默认凭据链时留空）",
                default=defaults["AWS_PROFILE"],
            ),
            "SSO_REGION": prompt_value(
                "Identity Center 区域", default=defaults["SSO_REGION"], required=True
            ),
            "KIRO_REGION": prompt_value(
                "Kiro 区域", default=defaults["KIRO_REGION"], required=True
            ),
            "IDENTITY_STORE_ID": prompt_value(
                "Identity Store ID", default=defaults["IDENTITY_STORE_ID"], required=True
            ),
            "INSTANCE_ARN": prompt_value(
                "Identity Center Instance ARN",
                default=defaults["INSTANCE_ARN"],
                required=True,
            ),
            "REPORT_BUCKET": prompt_value(
                "月报 S3 bucket（未配置可留空）",
                default=defaults["REPORT_BUCKET"],
            ),
            "REPORT_PREFIX": prompt_value(
                "月报 S3 prefix", default=defaults["REPORT_PREFIX"], required=True
            ),
            "REPORT_OVERAGE_PRICE_PER_CREDIT": prompt_value(
                "每 Credit 估算超额单价",
                default=defaults["REPORT_OVERAGE_PRICE_PER_CREDIT"],
                required=True,
            ),
        }

    if not config["ADMIN_PASSWORD"]:
        config["ADMIN_PASSWORD"] = secrets.token_urlsafe(24)
        generated_password = True
    if not config["SESSION_SECRET"]:
        config["SESSION_SECRET"] = secrets.token_urlsafe(48)

    validate_configuration(config)
    if generated_password:
        print("\n已生成管理员密码，请立即保存：")
        print(config["ADMIN_PASSWORD"])
    return config


def validate_configuration(config: dict[str, str]) -> None:
    if len(config["ADMIN_PASSWORD"]) < 12:
        fail("ADMIN_PASSWORD 至少需要 12 个字符")
    if len(config["SESSION_SECRET"]) < 32:
        fail("SESSION_SECRET 至少需要 32 个字符")
    identity_store_id = config["IDENTITY_STORE_ID"]
    legacy_identity_store_id = re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        identity_store_id,
    )
    if not identity_store_id.startswith("d-") and not legacy_identity_store_id:
        fail("IDENTITY_STORE_ID 格式无效")
    if not config["INSTANCE_ARN"].startswith("arn:aws:sso:::instance/"):
        fail("INSTANCE_ARN 格式无效")
    if config["COOKIE_SECURE"] not in {"true", "false"}:
        fail("COOKIE_SECURE 只能是 true 或 false")
    try:
        ttl = int(config["SESSION_TTL_HOURS"])
        price = float(config["REPORT_OVERAGE_PRICE_PER_CREDIT"])
    except ValueError:
        fail("会话时长和超额单价必须是数字")
    if not 1 <= ttl <= 168:
        fail("SESSION_TTL_HOURS 必须在 1 到 168 之间")
    if not 0 <= price <= 100:
        fail("REPORT_OVERAGE_PRICE_PER_CREDIT 必须在 0 到 100 之间")


def secure_env_file(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    if os.name != "nt":
        path.chmod(0o600)
        return

    username = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    principal = f"{domain}\\{username}" if domain else username
    powershell = r"""
param([string]$Path, [string]$Principal)
$acl = [System.Security.AccessControl.FileSecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
$rights = [System.Security.AccessControl.FileSystemRights]::FullControl
$type = [System.Security.AccessControl.AccessControlType]::Allow
$inheritance = [System.Security.AccessControl.InheritanceFlags]::None
$propagation = [System.Security.AccessControl.PropagationFlags]::None
foreach ($identity in @($Principal, 'NT AUTHORITY\SYSTEM')) {
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $identity, $rights, $inheritance, $propagation, $type
    )
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $Path -AclObject $acl
"""
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        powershell,
        str(path),
        principal,
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True)
    except OSError as exc:
        fail(f"无法运行 PowerShell 收紧 .env ACL：{exc}")
    if result.returncode != 0:
        fail("无法收紧 .env ACL，请确认当前账号有权修改该文件")


def write_env_file(config: dict[str, str], path: Path = ENV_FILE) -> None:
    lines = [
        "# Generated by setup.py. Do not commit this file.",
        f"ADMIN_USERNAME={quote_env(config['ADMIN_USERNAME'])}",
        f"ADMIN_PASSWORD={quote_env(config['ADMIN_PASSWORD'])}",
        f"SESSION_SECRET={quote_env(config['SESSION_SECRET'])}",
        f"SESSION_TTL_HOURS={quote_env(config['SESSION_TTL_HOURS'])}",
        f"COOKIE_SECURE={quote_env(config['COOKIE_SECURE'])}",
        "",
    ]
    if config["AWS_PROFILE"]:
        lines.append(f"AWS_PROFILE={quote_env(config['AWS_PROFILE'])}")
    lines.extend(
        [
            f"SSO_REGION={quote_env(config['SSO_REGION'])}",
            f"KIRO_REGION={quote_env(config['KIRO_REGION'])}",
            f"IDENTITY_STORE_ID={quote_env(config['IDENTITY_STORE_ID'])}",
            f"INSTANCE_ARN={quote_env(config['INSTANCE_ARN'])}",
            "",
            f"REPORT_BUCKET={quote_env(config['REPORT_BUCKET'])}",
            f"REPORT_PREFIX={quote_env(config['REPORT_PREFIX'])}",
            "REPORT_OVERAGE_PRICE_PER_CREDIT="
            f"{quote_env(config['REPORT_OVERAGE_PRICE_PER_CREDIT'])}",
            "",
        ]
    )
    content = "\n".join(lines)
    if os.name == "nt":
        path.write_text(content, encoding="utf-8")
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as env_handle:
            env_handle.write(content)
    secure_env_file(path)


def requirements_digest() -> str:
    return hashlib.sha256(REQUIREMENTS_FILE.read_bytes()).hexdigest()


def ensure_virtualenv(skip_install: bool = False, force_install: bool = False) -> Path:
    python = venv_python()
    if not python.exists():
        print(f"\n创建 Python 虚拟环境：{VENV_DIR}")
        try:
            venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        except Exception as exc:
            fail(f"创建虚拟环境失败：{exc}")
    if skip_install:
        return python

    digest = requirements_digest()
    installed_digest = (
        REQUIREMENTS_MARKER.read_text(encoding="utf-8").strip()
        if REQUIREMENTS_MARKER.exists()
        else ""
    )
    if force_install or installed_digest != digest:
        print("\n安装固定版本 Python 依赖……")
        try:
            subprocess.run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "-r",
                    str(REQUIREMENTS_FILE),
                ],
                cwd=ROOT,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            fail("依赖安装失败，请检查网络、代理和 Python 环境后重试")
        REQUIREMENTS_MARKER.write_text(digest, encoding="utf-8")
    else:
        print("\nPython 依赖未变化，跳过安装。")
    return python


def validate_runtime(python: Path) -> None:
    code = "from app.config import get_settings; get_settings(); print('配置有效')"
    try:
        result = subprocess.run(
            [str(python), "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        fail(f"无法运行虚拟环境 Python：{exc}")
    if result.returncode != 0:
        details = result.stderr.strip().splitlines()
        fail(f"应用配置校验失败：{details[-1] if details else '未知错误'}")
    print(result.stdout.strip())


def check_aws_read_only(python: Path) -> None:
    print("\n执行 AWS 只读连通性检查……")
    code = r"""
import asyncio
import json
from app.aws_gateway import AWSGateway
from app.config import get_settings
from app.report_service import S3MonthlyReportService

async def main():
    settings = get_settings()
    gateway = AWSGateway(settings)
    identity = await gateway._boto_call("sts", "get_caller_identity")
    users, subscriptions, report = await asyncio.gather(
        gateway.list_users(),
        gateway.list_subscriptions(),
        S3MonthlyReportService(settings, session=gateway.session).load_latest(),
    )
    print(json.dumps({
        "account": identity.get("Account"),
        "arn": identity.get("Arn"),
        "users": len(users),
        "subscriptions": len(subscriptions),
        "report": report.status,
    }))

asyncio.run(main())
"""
    try:
        result = subprocess.run(
            [str(python), "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        fail(f"无法运行虚拟环境 Python：{exc}")
    if result.returncode != 0:
        details = result.stderr.strip().splitlines()
        fail(f"AWS 只读检查失败：{details[-1] if details else '未知错误'}")
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        fail("AWS 只读检查返回了无法解析的结果")
    if data.get("report") == "unavailable":
        fail("AWS 基础检查通过，但无法读取已配置的月报 S3 数据源")
    print(
        "AWS 检查通过："
        f"account={data['account']} users={data['users']} "
        f"subscriptions={data['subscriptions']} report={data['report']}"
    )
    if str(data.get("arn", "")).endswith(":root"):
        print("警告：当前使用 Root 凭据，请尽快替换为最小权限 IAM 身份。")


def run_server(python: Path, host: str, port: int) -> None:
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    print(f"\n启动 Kiro 管理台：http://{display_host}:{port}")
    print("按 Ctrl+C 停止。\n")
    try:
        result = subprocess.run(
            [
                str(python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                host,
                "--port",
                str(port),
                "--workers",
                "1",
            ],
            cwd=ROOT,
        )
    except KeyboardInterrupt:
        print("\n服务已停止。")
        return
    except OSError as exc:
        fail(f"无法启动虚拟环境 Python：{exc}")
    if result.returncode != 0:
        fail(f"服务启动失败（退出码 {result.returncode}），请检查端口和上方日志")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="一键配置并启动 Kiro Simple Admin（Windows/Linux/macOS）"
    )
    parser.add_argument("--run", action="store_true", help="配置完成后立即启动服务")
    parser.add_argument("--check", action="store_true", help="仅校验现有安装和 AWS 连接")
    parser.add_argument(
        "--reconfigure", action="store_true", help="重新交互生成 .env（保留现有值作为默认）"
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="从环境变量生成配置，不进行交互提问",
    )
    parser.add_argument("--skip-install", action="store_true", help="跳过依赖安装")
    parser.add_argument("--force-install", action="store_true", help="强制重新安装依赖")
    parser.add_argument("--skip-aws-check", action="store_true", help="跳过 AWS 只读连通性检查")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    return parser


def format_start_command(python: Path, host: str, port: int) -> str:
    command = f'"{python}" "{ROOT / "setup.py"}" --run --host {host} --port {port}'
    return f"& {command}" if os.name == "nt" else command


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if sys.version_info < MIN_PYTHON:
        fail("需要 Python 3.12 或更高版本")
    if not 1 <= args.port <= 65535:
        fail("端口必须在 1 到 65535 之间")
    if not REQUIREMENTS_FILE.exists():
        fail(f"找不到 {REQUIREMENTS_FILE.name}，请在项目根目录运行 setup.py")

    if args.check:
        if not ENV_FILE.exists():
            fail("找不到 .env，请先运行 python setup.py")
        python = venv_python()
        if not python.exists():
            fail("找不到 .venv，请先运行 python setup.py")
    else:
        python = ensure_virtualenv(args.skip_install, args.force_install)
        existing = parse_env_file(ENV_FILE)
        if not ENV_FILE.exists() or args.reconfigure:
            config = build_configuration(existing, args.non_interactive)
            write_env_file(config)
            print(f"配置已写入：{ENV_FILE}")
        else:
            print(f"\n保留现有配置：{ENV_FILE}")
        secure_env_file(ENV_FILE)

    validate_runtime(python)
    if not args.skip_aws_check:
        check_aws_read_only(python)

    if args.check:
        print("\n检查完成，未修改本地配置。")
        return 0
    if args.run:
        run_server(python, args.host, args.port)
    else:
        command = format_start_command(python, args.host, args.port)
        display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
        print("\nSetup 完成。启动命令：")
        print(command)
        print(f"启动后访问：http://{display_host}:{args.port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
