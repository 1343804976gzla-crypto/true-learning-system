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
