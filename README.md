# Kiro Simple Admin

面向单个 AWS IAM Identity Center 实例的轻量 Kiro 管理后台。

- Python 3.12 + FastAPI + Uvicorn
- 原生 HTML/CSS/JavaScript，无 Node 构建
- 单容器或本机进程运行
- 无数据库、Alembic、Redis 和任务队列
- 用户、订阅和月报按请求读取 AWS
- 单管理员 Cookie 会话认证

## 功能

- 管理员登录、退出和登录限流
- 实时用户列表、搜索、CSV 导入/导出
- 邮箱验证、密码重置和带强确认的用户删除
- 实时订阅列表、单个/批量分配、套餐变更和取消
- 从 S3 Kiro User Activity Reports 聚合月报
- Highcharts 月报图表、用户明细和洞察

## 最简单的非 Docker 安装

前置条件只有：

1. Python 3.12 或更高版本
2. 可用的 AWS 默认凭据链或 AWS CLI profile
3. 能访问 AWS API；图表还需要访问 jsDelivr CDN

### Windows

在 PowerShell 中运行：

```powershell
py -3.12 setup.py --run
```

### Linux / macOS

```bash
python3.12 setup.py --run
```

首次运行会自动：

1. 创建 `.venv`
2. 安装 `requirements.txt` 中的固定版本依赖
3. 交互生成 `.env`
4. 生成管理员密码和 Session Secret（如果未提供）
5. 只读检查 STS、Identity Center、Kiro 订阅和 S3 月报
6. 启动 `http://127.0.0.1:8000`

已有 `.env` 时不会覆盖。依赖没有变化时也不会重复安装。

### 常用 setup 参数

```text
python setup.py --run                    配置后立即启动
python setup.py --check                  检查现有配置和 AWS 连接
python setup.py --reconfigure            重新交互配置，保留现有值作为默认
python setup.py --run --host 0.0.0.0     允许局域网访问
python setup.py --run --port 8080        修改监听端口
python setup.py --skip-aws-check         跳过 AWS 只读检查
python setup.py --force-install          强制重新安装依赖
```

局域网监听示例：

```bash
python3.12 setup.py --run --host 0.0.0.0 --port 8080
```

随后访问：

```text
http://<本机IP>:8080
```

需要同时在 Windows Defender 防火墙或 Linux 防火墙中允许该端口。公网部署建议使用 IIS、Caddy 或 Nginx 提供 HTTPS，不要直接暴露 Uvicorn。

### 无交互安装

可通过环境变量提供配置：

```bash
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD='replace-with-a-long-random-password'
export IDENTITY_STORE_ID='d-xxxxxxxxxx'
export INSTANCE_ARN='arn:aws:sso:::instance/ssoins-xxxxxxxxxxxxxxxx'
export AWS_PROFILE='kiro-admin'
export REPORT_BUCKET='your-kiro-report-bucket'
python3.12 setup.py --non-interactive --run
```

未提供 `ADMIN_PASSWORD` 或 `SESSION_SECRET` 时，setup 会安全生成；管理员密码只在终端显示一次。

## AWS 凭据

应用使用 boto3 默认凭据链：

- EC2 IAM Role（生产环境首选）
- 环境变量
- `~/.aws/credentials` 和 `~/.aws/config`
- AWS CLI/SSO profile

只有在确实需要指定 profile 时才设置 `AWS_PROFILE`。不要设置空的 `AWS_PROFILE=`；使用默认凭据链时应删除该变量。

Windows 的共享凭据通常位于：

```text
C:\Users\<用户名>\.aws\credentials
C:\Users\<用户名>\.aws\config
```

使用 SSO profile 时先运行：

```bash
aws sso login --profile kiro-admin
```

SSO 登录会过期，适合本地人工使用；长期无人值守服务应使用最小权限 IAM 身份。不要使用 Root Access Key。

## 手动非 Docker 启动

如果不使用 setup，Linux/macOS 命令为：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows PowerShell 命令为：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows 上请确认 `.env` 的 ACL 只允许运行服务的账号读取；使用 `setup.py` 时会自动尝试收紧 ACL。

## Docker 启动

```bash
cp .env.example .env
chmod 600 .env
docker compose --env-file .env up -d --build
```

Compose 默认监听：

```text
http://0.0.0.0:8080
```

## 主要配置

| 变量 | 说明 |
|---|---|
| `ADMIN_USERNAME` | 管理员用户名 |
| `ADMIN_PASSWORD` | 至少 12 位的管理员密码 |
| `SESSION_SECRET` | 至少 32 位的 Cookie 签名密钥 |
| `COOKIE_SECURE` | HTTPS 部署必须设置为 `true` |
| `AWS_PROFILE` | 可选；默认凭据链无需设置 |
| `SSO_REGION` | Identity Center 区域 |
| `KIRO_REGION` | Kiro 订阅区域 |
| `IDENTITY_STORE_ID` | Identity Store ID |
| `INSTANCE_ARN` | Identity Center Instance ARN |
| `REPORT_BUCKET` | 可选；Kiro User Activity Reports 的 S3 bucket |
| `REPORT_PREFIX` | 默认 `user-activity-reports` |
| `REPORT_OVERAGE_PRICE_PER_CREDIT` | 估算超额单价，默认 `0.04` |

月报费用是估算值，不代表 AWS 正式账单。S3 报告也可能存在投递延迟，页面会显示数据截至日期。

## 测试

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check app tests setup.py
.venv/bin/python -m ruff format --check app tests setup.py
```

测试使用 Fake AWS Gateway 和 Fake S3，不会修改真实用户、订阅或月报。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/login` | 管理员登录 |
| POST | `/api/logout` | 退出 |
| GET | `/api/session` | 当前会话 |
| GET | `/api/overview` | 实时概览 |
| GET | `/api/users?q=` | 用户列表和搜索 |
| POST | `/api/users/batch` | 批量导入用户 |
| POST | `/api/users/{id}/verify-email` | 发送邮箱验证 |
| POST | `/api/users/{id}/reset-password` | 发送重置邮件 |
| DELETE | `/api/users/{id}` | 删除用户（强确认） |
| GET | `/api/subscriptions` | 实时订阅列表 |
| POST | `/api/subscriptions` | 分配订阅 |
| POST | `/api/subscriptions/batch` | 批量分配订阅 |
| PATCH | `/api/subscriptions/{principal_id}` | 变更套餐 |
| DELETE | `/api/subscriptions/{principal_id}` | 取消订阅 |
| GET | `/api/reports/monthly/view` | 受保护的最新月报 |

OpenAPI：`/api/docs`。

## 安全说明

- `.env` 已被 Git 忽略，不要把密码或 AWS 凭据提交到仓库。
- Cookie 使用 `HttpOnly + SameSite=Strict`。
- `COOKIE_SECURE=false` 仅适用于本机 HTTP；HTTPS 必须设置为 `true`。
- 应用不在本地保存 AWS 用户、订阅、密码或月报数据。
- 验证邮箱、重置密码、删除用户和订阅变更会立即调用 AWS。
- 当前实现为单进程、单管理员，无多角色和跨实例共享会话。
