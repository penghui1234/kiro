# Kiro Simple Admin

一个面向单个 AWS IAM Identity Center 实例的轻量 Kiro 管理后台。

## 为什么重新实现

旧系统包含多账号、MySQL、Alembic、React、后台调度器和本地同步表，运行与排障成本较高。本项目刻意保持简单：

- 单个 Identity Center 实例
- 单个 FastAPI 容器
- 原生 HTML/CSS/JavaScript，无 Node 构建
- 无数据库、无本地同步、无后台任务
- 用户和订阅每次都实时读取 AWS
- 单管理员环境变量登录，HMAC 签名 HttpOnly Cookie

## 功能

- 管理员登录/退出
- 用户与订阅概览
- Identity Center 用户列表和搜索
- 发送密码重置邮件
- 查看 Kiro 订阅（含 ACTIVE/PENDING）
- 分配、变更和取消订阅

## 配置

```bash
cp .env.example .env
chmod 600 .env
```

必须设置：

- `ADMIN_PASSWORD`：至少 12 位随机密码
- `SESSION_SECRET`：至少 32 位随机值
- `SSO_REGION`
- `KIRO_REGION`
- `IDENTITY_STORE_ID`
- `INSTANCE_ARN`

AWS 使用 boto3 默认凭证链（环境变量、实例角色、共享凭证文件等）；仅当 `AWS_PROFILE` 非空时才强制指定 profile。Compose 默认只读挂载 `/home/ec2-user/.aws`。生产环境建议给 EC2 绑定最小权限 IAM Role，删除节点上的长期 Access Key，尤其不要使用 Root Access Key。

## 启动

```bash
docker compose --env-file .env up -d --build
```

服务仅绑定本机：`http://127.0.0.1:8080`。远程访问建议使用 SSH 隧道：

```bash
ssh -L 8080:127.0.0.1:8080 ec2-user@<EC2_IP>
```

然后打开 `http://127.0.0.1:8080`。

## 测试

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
```

测试使用 Fake AWS Gateway，不会修改真实用户或订阅。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/login` | 管理员登录 |
| POST | `/api/logout` | 退出 |
| GET | `/api/session` | 当前会话 |
| GET | `/api/overview` | 实时概览 |
| GET | `/api/users?q=` | 用户列表/搜索 |
| POST | `/api/users/{id}/reset-password` | 发送重置邮件 |
| GET | `/api/subscriptions` | 实时订阅列表 |
| POST | `/api/subscriptions` | 分配订阅 |
| PATCH | `/api/subscriptions/{principal_id}` | 变更套餐 |
| DELETE | `/api/subscriptions/{principal_id}` | 取消订阅 |

OpenAPI：`/api/docs`。

## 安全说明

- 默认 `COOKIE_SECURE=false` 仅适用于本机 HTTP/SSH 隧道；HTTPS 部署必须设为 `true`。
- Cookie 为 `HttpOnly + SameSite=Strict`。
- 应用不保存 AWS Access Key、用户、订阅或密码。
- 分配、变更和取消订阅会立即调用 AWS，可能产生费用。
