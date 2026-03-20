# True Learning System 数据库运维脚本

## 1. 当前活跃库

- `data/content_knowledge.db`
- `data/learning_runtime.db`
- `data/wrong_answer_review.db`
- `data/agent.db`
- `data/legacy_compat.db`

`data/learning.db` 现在视为 shadow 历史库，不应继续作为主运行库写入。

## 2. 统一备份

命令：

```powershell
python scripts/backup_all_databases.py
```

如果需要把 `learning.db` 也一起备份：

```powershell
python scripts/backup_all_databases.py --include-shadow
```

默认输出到每个数据库所在目录下的 `backups/`。

## 3. 统一巡检

命令：

```powershell
python scripts/inspect_database_health.py
```

如果要同时检查 shadow 库：

```powershell
python scripts/inspect_database_health.py --include-shadow
```

如果要机器可读输出：

```powershell
python scripts/inspect_database_health.py --include-shadow --json
```

巡检会报告：

- 数据库文件是否存在
- 主文件/WAL/SHM 大小
- 关键表计数
- `learning.db` shadow 表与活动库表的计数对比

## 4. 审计基础表

系统启动时会为所有活跃库创建统一的 `audit_change_log` 表。

当前阶段已完成：

- 审计表自动建表
- 巡检脚本可检查该表是否存在

当前阶段尚未完成：

- 把所有关键写操作逐步接入该表

## 5. Legacy 清理策略

当前已把 legacy 运行时表 `wrong_answers` 迁到：

- `data/legacy_compat.db`

命令：

```powershell
python scripts/migrate_legacy_compat_db.py --target data/legacy_compat.db
```

因此：

- 活跃运行流量不应再依赖 `learning.db`
- `learning.db` 只保留为 shadow 历史对照库

下一步若要继续物理清理，应先：

1. 运行统一备份
2. 运行巡检并确认 shadow 对照一致
3. 再决定是否对 `learning.db` 做归档或移出主数据目录

## 6. 审计落地状态（2026-03-20）

当前 `audit_change_log` 已不再只是建表，占用中的关键写入链路已经接入：

- `content_knowledge.db`
  - `daily_uploads`
  - `knowledge_upload_records`
  - `knowledge_pending_classifications`
  - `knowledge_point_notes`
  - `knowledge_daily_reports`
- `learning_runtime.db`
  - `learning_sessions`
  - `learning_activities`
  - `question_records`
  - `daily_learning_logs`
- `wrong_answer_review.db`
  - `wrong_answers_v2`
  - `wrong_answer_retries`
  - `daily_review_papers`
- `agent.db`
  - `agent_action_logs`

审计字段当前统一包含：

- `domain_name`
- `entity_type`
- `entity_id`
- `public_id`
- `action`
- `actor_key`
- `user_id`
- `device_id`
- `request_id`
- `trace_id`
- `source`
- `origin_event_type`
- `origin_public_id`
- `before_json`
- `after_json`
- `changed_fields`
- `created_at`

当前已验证的隔离环境审计落盘结果：

- `content`: 1
- `runtime`: 7
- `review`: 2
- `agent`: 1

说明：

- 上述计数来自临时 SQLite 验证环境，不会污染正式库。
- 审计快照会对超长文本做截断，避免把 OCR 原文或大段笔记完整复制进审计表。
- 后续如果继续补链路，优先补回滚、批量修复脚本、legacy 兼容写入。

## 7. 维修与恢复 SOP

推荐固定按下面顺序执行，避免“先修再看、越修越乱”：

1. 先做只读巡检

```powershell
python scripts/inspect_database_health.py --include-shadow
```

2. 立即做全量备份

```powershell
python scripts/backup_all_databases.py --include-shadow
```

3. 如果问题是章节目录、章节 ID 映射混乱，运行章节修复

```powershell
python scripts/repair_chapter_catalog.py
```

4. 如果问题是正式库混入测试数据，或需要从历史备份重建候选恢复库，运行恢复脚本

```powershell
python scripts/restore_learning_data.py
```

5. 再次巡检，确认计数、shadow 对照、关键表状态

```powershell
python scripts/inspect_database_health.py --include-shadow
```

6. 最后才决定是否切换、覆盖、归档旧库

约束：

- 第 3、4 步都必须在第 2 步之后执行。
- 修复脚本和恢复脚本现在会写入一条脚本级审计汇总，便于回看“哪天跑过、改了多少、输出到哪里”。
- 恢复脚本默认生成候选库，不应直接覆盖正式运行库。
