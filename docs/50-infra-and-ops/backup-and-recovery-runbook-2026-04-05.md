# PostgreSQL 备份与恢复 Runbook

日期：2026-04-05

关联文档：

- `docs/50-infra-and-ops/100-student-rollout-plan-2026-04-05.md`
- `docs/50-infra-and-ops/postgresql-cutover-runbook-2026-04-05.md`
- `docs/50-infra-and-ops/https-reverse-proxy-runbook-2026-04-05.md`
- `docs/50-infra-and-ops/rollout-final-rehearsal-checklist-2026-04-06.md`

## 目标

为 rollout 环境提供一套可重复执行的 PostgreSQL 备份、恢复、探活与冒烟检查流程。

本轮新增脚本：

- `scripts/backup_postgres.ps1`
- `scripts/restore_postgres.ps1`
- `scripts/smoke_test_production.ps1`

应用新增入口：

- `GET /health`
- `GET /ready`

## 1. 前置条件

至少满足以下之一：

- 本机已安装 `pg_dump` 与 `pg_restore`
- 或运行中的 PostgreSQL Docker 容器可直接 `docker exec`

默认配置来源：

1. 命令行参数
2. 当前 shell 环境变量
3. 仓库根目录 `.env`
4. 脚本内默认值

默认使用的关键变量：

- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_PORT`
- `POSTGRES_BIND_HOST`
- `POSTGRES_CONTAINER_NAME`，未配置时默认 `true-learning-system-postgres`

## 2. 备份

### 2.1 本机 PostgreSQL 客户端模式

```powershell
.\scripts\backup_postgres.ps1
```

可选参数示例：

```powershell
.\scripts\backup_postgres.ps1 `
  -Host 127.0.0.1 `
  -Port 15432 `
  -Database true_learning_system `
  -Username tls `
  -Password change-me `
  -OutputDir .\data\backups\postgres
```

### 2.2 Docker 容器模式

```powershell
.\scripts\backup_postgres.ps1 -UseDockerExec
```

脚本行为：

- 容器内执行 `pg_dump -Fc`
- 将备份文件复制回宿主机
- 校验输出文件存在且非空

默认输出目录：

- `data/backups/postgres/`

## 3. 恢复

恢复前必须确认目标数据库允许覆盖。

### 3.1 本机 PostgreSQL 客户端模式

```powershell
.\scripts\restore_postgres.ps1 -InputFile .\data\backups\postgres\true_learning_system.pg-backup-20260405_220000.dump
```

### 3.2 Docker 容器模式

```powershell
.\scripts\restore_postgres.ps1 `
  -InputFile .\data\backups\postgres\true_learning_system.pg-backup-20260405_220000.dump `
  -UseDockerExec
```

脚本默认使用：

- `pg_restore --clean --if-exists --no-owner --no-privileges`

这意味着恢复会清理已存在对象，不适合误操作到错误环境。

## 4. 探活与冒烟

### 4.1 健康检查

```powershell
curl http://127.0.0.1:18000/health
curl http://127.0.0.1:18000/ready
```

约定：

- `/health` 只表示应用进程可响应
- `/ready` 表示启动流程已完成，且应用当前配置下涉及的数据库连接可通过 `SELECT 1`

如果 `/ready` 返回 `503`，禁止放量。

### 4.2 冒烟脚本

已知账号场景：

```powershell
.\scripts\smoke_test_production.ps1 `
  -BaseUrl https://your-domain.example.com `
  -Email student@example.com `
  -Password your-password
```

只测公开入口场景：

```powershell
.\scripts\smoke_test_production.ps1 -BaseUrl https://your-domain.example.com -SkipAuth
```

当前脚本覆盖：

- `/health`
- `/api/auth/me`
- 登录
- `/api/auth/sessions`
- `/api/stats`
- `/history`

## 5. 推荐执行顺序

部署或升级时建议固定成以下顺序：

1. 先跑 PostgreSQL 备份
2. 再执行迁移或恢复
3. 启动应用与代理
4. 观察 `/health` 与 `/ready`
5. 运行 `smoke_test_production.ps1`
6. 确认无误后再允许学生流量进入

如果是最终 rollout 主机的上线前演练，按这个顺序完成后，还应继续执行：

- `docs/50-infra-and-ops/rollout-final-rehearsal-checklist-2026-04-06.md`

这样可以把代理、鉴权配置校验、压测准入和 go/no-go 决策一起收口。

## 6. 回滚原则

出现以下任一情况时应停止放量并准备回滚：

- `/ready` 持续返回 `503`
- 登录或 session 接口异常
- 测验提交/学习轨迹页面无法访问
- 数据库恢复后行数明显异常

最小回滚动作：

1. 停止新流量
2. 保留故障环境供排查
3. 用最近一次确认可用的 PostgreSQL 备份恢复
4. 再次执行 `/ready` 与 `smoke_test_production.ps1`

## 7. 当前边界

这套 runbook 解决的是：

- PostgreSQL 备份
- PostgreSQL 恢复
- 应用 readiness 探活
- 基础 smoke test

它还没有覆盖：

- Neo4j / Graphiti 备份恢复
- 真实生产主机上的自动化定时备份
- 跨机备份留存策略
- 正式监控告警系统
