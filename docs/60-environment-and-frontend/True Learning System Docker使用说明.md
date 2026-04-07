# True Learning System Docker 使用说明

更新时间：2026-04-07

当前仓库里的 Docker 部署需要分成两种模式理解，不能再按一套命令混用：

- 共享主机更新模式：继续使用当前 `.env` 指向的 SQLite 数据文件，只把最新代码重建进 Docker 容器
- 正式 rollout 模式：切换到 PostgreSQL，跑 Alembic、做数据导入校验，并按需挂 Caddy HTTPS 代理

## 1. 共享主机更新模式

适用场景：

- 当前这台 Windows 主机还在跑旧的共享实例
- `.env` 里仍然保留这些 SQLite 路径：
  - `DATABASE_PATH`
  - `CONTENT_DATABASE_PATH`
  - `LEGACY_DATABASE_PATH`
  - `AGENT_DATABASE_PATH`
  - `RUNTIME_DATABASE_PATH`
  - `REVIEW_DATABASE_PATH`
- 你只是要把最新代码重新构建进现有 Docker 实例

启动或更新：

```powershell
cd C:\Users\35456\true-learning-system
.\scripts\start_docker_host.ps1
```

手动等价命令：

```powershell
cd C:\Users\35456\true-learning-system
docker compose up -d --build app
```

查看日志：

```powershell
cd C:\Users\35456\true-learning-system
docker compose logs -f app
```

健康检查：

```text
http://localhost:18000/health
```

说明：

- `app` 服务现在会跟随 `.env` 里的数据库配置，不再强制切到 PostgreSQL
- 如果 Docker 数据卷 `true-learning-system_tls_app_data` 还是空的，`start_docker_host.ps1` 会先从项目 `data/` 目录做一次初始化
- Docker 容器内固定关闭 `TELEGRAM_POLLING_ENABLED`，Telegram 轮询应继续放在宿主机侧进程
- 这个模式适合局域网 / Tailscale 共享，不等于公网正式发布

## 2. 正式 rollout 模式

适用场景：

- 你明确要把运行环境切到 PostgreSQL
- 你准备做 SQLite -> PostgreSQL 的一次完整 cutover
- 你要为真实学生流量做最终部署演练或正式上线

切换前必须先看：

- `docs/50-infra-and-ops/postgresql-docker-bootstrap-2026-04-04.md`
- `docs/50-infra-and-ops/postgresql-cutover-runbook-2026-04-05.md`
- `docs/50-infra-and-ops/https-reverse-proxy-runbook-2026-04-05.md`
- `docs/50-infra-and-ops/rollout-final-rehearsal-checklist-2026-04-06.md`

最小推荐环境变量：

```env
DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
ALEMBIC_DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
CONTENT_DATABASE_PATH=
LEGACY_DATABASE_PATH=
AGENT_DATABASE_PATH=
RUNTIME_DATABASE_PATH=
REVIEW_DATABASE_PATH=
CONTENT_DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
LEGACY_DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
AGENT_DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
RUNTIME_DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
REVIEW_DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
DB_AUTO_CREATE_SCHEMA=false
SINGLE_USER_MODE=false
AUTH_REQUIRE_LOGIN_FOR_STUDENT_ROUTES=true
AUTH_COOKIE_SECURE=true
```

标准顺序：

1. 先备份当前 SQLite 数据
2. 跑一次 dry-run 导入报告
3. 启 PostgreSQL 并执行 Alembic
4. 执行 SQLite -> PostgreSQL 导入
5. 校验导入后的表行数
6. 再启动正式 rollout 栈
7. 跑 `ready` 和 smoke test，确认通过后再放量

关键命令：

```powershell
cd C:\Users\35456\true-learning-system
python scripts\backup_all_databases.py --include-shadow --output-dir data\backups\cutover-YYYY-MM-DD
python scripts\migrate_sqlite_to_postgres.py --dry-run --merge-audit --report-out data\logs\cutover-dry-run.json
docker compose up -d postgres
docker compose up migrate
python scripts\migrate_sqlite_to_postgres.py --target-url "postgresql+psycopg://tls:change-me@localhost:15432/true_learning_system" --truncate-target --merge-audit --report-out data\logs\cutover-import.json
python scripts\verify_postgres_cutover.py --target-url "postgresql+psycopg://tls:change-me@localhost:15432/true_learning_system" --merge-audit --allow-filtered-report data\logs\cutover-import.json --report-out data\logs\cutover-verify.json
docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d
python scripts\verify_rollout_auth_config.py --json
```

## 3. HTTPS 反向代理

如果是正式域名上线，推荐叠加 `docker-compose.proxy.yml`：

```powershell
cd C:\Users\35456\true-learning-system
docker compose -f docker-compose.yml -f docker-compose.proxy.yml build
docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d
```

最小代理相关变量：

```env
TLS_APP_BIND_HOST=127.0.0.1
TLS_PROXY_BIND_HOST=0.0.0.0
TLS_PUBLIC_HOSTNAME=your-domain.example.com
TLS_PROXY_HTTP_PORT=80
TLS_PROXY_HTTPS_PORT=443
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
UVICORN_FORWARDED_ALLOW_IPS=*
```

## 4. 访问地址

共享主机模式常用地址：

- 本机：`http://localhost:18000`
- 局域网：`http://<主机IP>:18000`
- Tailscale：`http://<tailnet-ip-or-dns>:18000`

正式 rollout 验证：

```powershell
curl http://127.0.0.1:18000/health
curl http://127.0.0.1:18000/ready
pwsh scripts\smoke_test_production.ps1 -BaseUrl https://your-domain.example.com -Email student@example.com -Password 'change-me'
```

## 5. 重要提醒

- 没有完成 cutover 导入和校验前，不要把正式环境直接重建到 PostgreSQL
- 如果 `.env` 仍然保留 split SQLite 路径，那么 `docker compose up -d --build app` 只是共享主机更新，不是 PostgreSQL 切库
- 不要在 Docker `app` 容器里开启 Telegram polling
- 没有 HTTPS 代理、登录验证、cookie 配置校验前，不要把容器直接暴露到公网
