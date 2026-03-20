# True Learning System 数据库拆分架构文档

状态：执行前架构基线
版本：v1
日期：2026-03-20

## 1. 文档目的

这份文档定义数据库拆分前必须先达成一致的架构约束。

它回答 6 个问题：

1. 当前系统为什么不能继续长期维持单库混写。
2. 未来应该拆成哪些数据库。
3. 每个数据库拥有哪些表，拥有哪些写入权。
4. 数据库之间如何交互，哪些行为被允许，哪些行为被禁止。
5. 审计、备份、恢复、巡检应该怎么设计。
6. 后续真正动手拆库时应按什么顺序执行。

本文件是后续数据库拆分的上位约束文档。

## 2. 当前架构问题

当前系统物理上主要只有一个数据库文件：

- `C:\Users\35456\true-learning-system\data\learning.db`

但业务上已经包含多个明显不同的数据域：

- 内容知识底座
- 做题运行态
- 错题复习闭环
- Agent 会话
- 统计与报表

当前问题不是“库太大”这么简单，而是“多个业务域在同一个写模型里混合演化”。

主要风险：

- 一个接口会同时跨多个业务域写表，导致 Bug 定位困难。
- 统计页、错题页、上传页之间的耦合远高于必要水平。
- 运维动作难以收敛，任意脚本一跑就可能误伤整站。
- 难以做分域备份与分域恢复。
- 难以设计清晰的审计日志。

结论：

数据库拆分不是优化项，而是可维护性和可恢复性的基础设施升级。

## 3. 架构目标

目标不是“把一个 SQLite 拆成多个 SQLite”本身。

目标是建立以下能力：

1. 每个业务域有明确的数据所有权。
2. 每张表只有一个业务域拥有最终写入解释权。
3. 跨域读取可以存在，但跨域写入必须经过明确的应用服务。
4. 每个域都能单独备份、单独恢复、单独巡检。
5. 所有关键写操作都能追踪到是谁、在何时、通过哪个请求修改了什么。

## 3.1 运行时隔离目标

除了业务边界清晰，本架构还要求运行时隔离清晰。

后续数据库拆分，不只是为了逻辑整洁，也是为了降低 SQLite 单文件混合负载带来的运行时风险。

需要默认隔离的三类负载：

- 写热点负载
- 增长热点负载
- 分析热点负载

当前系统使用 SQLite，并启用了 `WAL`。

这意味着：

- 读写不是完全互斥
- 但同一时刻写事务仍然高度受限
- 高频写入、长事务、重统计查询会互相放大延迟

因此拆库时不能只看“业务语义”，还必须同时看：

- 哪些表会被频繁写
- 哪些表会快速膨胀
- 哪些查询会执行长时间扫描和聚合

本项目中的运行时隔离要求：

- `learning_runtime.db` 不应长期承受首页、历史、看板类重查询
- `wrong_answer_review.db` 不应与考试运行态混成一个高频写热点库
- `content_knowledge.db` 应尽量保持稳定和低噪音
- `analytics_read_model.db` 应承担统计、聚合、缓存和重查询

## 4. 目标数据库拓扑

目标上拆成 4 个主数据库，加 1 个读模型层。

| 数据库 | 角色 | 写入频率 | 用户价值 | 是否优先拆分 |
|---|---|---|---|---|
| `content_knowledge.db` | 知识底座 | 中 | 高 | 中 |
| `learning_runtime.db` | 做题运行态 | 高 | 高 | 中高 |
| `wrong_answer_review.db` | 错题与复习资产 | 高 | 最高 | 高 |
| `agent.db` | Agent 会话与动作 | 中 | 中 | 最高 |
| `analytics_read_model.db` | 聚合报表与缓存 | 中 | 中 | 最后 |

说明：

- 前 4 个库是“业务事实库”。
- `analytics_read_model.db` 不是事实源，只是聚合层。

## 5. 有界上下文与数据库所有权

### 内容知识底座上下文

数据库：`content_knowledge.db`

职责：

- 保存上传原文和知识抽取结果。
- 保存章节目录。
- 保存知识点定义与掌握基线。
- 保存上传工作台中的知识点笔记和来源追踪。

拥有写权的表：

- `daily_uploads`
- `chapters`
- `concept_mastery`
- `knowledge_upload_records`
- `knowledge_point_notes`
- `knowledge_point_sources`
- `knowledge_pending_classifications`
- `knowledge_daily_reports`
- `concept_links`

### 做题运行态上下文

数据库：`learning_runtime.db`

拥有写权的表：

- `learning_sessions`
- `learning_activities`
- `question_records`
- `daily_learning_logs`
- `learning_insights`
- `batch_exam_states`
- `quiz_sessions`
- `test_records`
- `feynman_sessions`
- `variations`

### 错题复习闭环上下文

数据库：`wrong_answer_review.db`

拥有写权的表：

- `wrong_answers_v2`
- `wrong_answer_retries`
- `daily_review_papers`
- `daily_review_paper_items`
- `chapter_review_chapters`
- `chapter_review_units`
- `chapter_review_tasks`
- `chapter_review_task_questions`
- `wrong_answers`

### Agent 上下文

数据库：`agent.db`

拥有写权的表：

- `agent_sessions`
- `agent_messages`
- `agent_memories`
- `agent_tool_calls`
- `agent_turn_states`
- `agent_tool_cache`
- `agent_action_logs`
- `agent_tasks`
- `agent_task_events`

### 统计与报表上下文

数据库：`analytics_read_model.db`

职责：

- 面向首页、历史页、学习轨迹页、看板页提供聚合数据
- 缓存高成本统计结果

## 6. 数据库之间的关系

跨数据库允许共享的键：

- `user_id`
- `device_id`
- `actor_key`
- `chapter_id`
- `concept_id`
- `session_id`
- `wrong_answer_id`
- `request_id`
- `trace_id`

这些键必须在全系统保持语义稳定。

后续数据库拆分采用三层标识体系：

1. 语义自然键
2. `public_id TEXT UNIQUE`
3. `trace_id`

约束：

- 前缀表达业务实体类型
- 不表达物理数据库文件归属

不推荐把“数据库归属”编码到主 ID 中，因为数据库是实现细节，不是领域语义。

## 7. 单写者原则

每张表只能有一个“最终解释者”。

例子：

- `chapters` 的解释者只能是内容知识底座域
- `question_records` 的解释者只能是做题运行态域
- `wrong_answers_v2` 的解释者只能是错题复习域
- `agent_sessions` 的解释者只能是 Agent 域

别的模块即使能读，也不应该直接写。

推荐迁移策略：

1. 保留现有 `INTEGER PRIMARY KEY`
2. 为关键表新增 `public_id TEXT UNIQUE`
3. 新 API 对外返回 `public_id`
4. 审计、日志、跨库关联优先使用 `public_id`

## 8. 跨域交互方式

同步服务调用适用于一个用户动作需要立刻看到另一个域的结果。

建议事件：

- `UploadSaved`
- `ConceptCatalogUpdated`
- `ExamSubmitted`
- `QuestionRecorded`
- `WrongAnswerCreated`
- `WrongAnswerRetried`
- `DailyReviewPaperGenerated`
- `AgentActionExecuted`

旧系统表仍在使用时，必须通过反腐层访问：

- `wrong_answers`
- `quiz_sessions`
- `test_records`
- `feynman_sessions`
- `variations`

## 9. 审计、备份与巡检

建议每个主库都加统一审计表 `audit_change_log`，至少包含：

- `domain_name`
- `entity_type`
- `entity_id`
- `action`
- `actor_key`
- `user_id`
- `device_id`
- `request_id`
- `trace_id`
- `before_json`
- `after_json`
- `created_at`

备份原则：

1. 所有写模型库独立备份
2. 所有批量脚本执行前强制快照
3. 所有结构迁移前强制快照
4. 备份必须标明来源、时间、版本、原因

推荐恢复顺序：

1. `content_knowledge.db`
2. `wrong_answer_review.db`
3. `learning_runtime.db`
4. `agent.db`
5. `analytics_read_model.db`

## 10. 代码结构要求

数据库拆分后，代码结构必须调整：

- Router 不应直接跨多个业务域做复杂写入
- 应用服务负责事务边界、跨域编排、失败补偿、审计记录
- Repository 负责某一域内的表访问和查询封装
- 统计、历史、首页这类功能，应逐步迁到读模型服务

## 11. 数据迁移路线

阶段 0：冻结基线

- 清点现有表和现有脚本
- 统一备份
- 建立迁移台账
- 为关键表梳理现有主键类型与未来 `public_id` 策略

阶段 1：先拆 `agent.db`

- 独立性最高
- 风险最低
- 最容易先获得收益

阶段 2：拆 `wrong_answer_review.db`

阶段 3：拆 `learning_runtime.db`

阶段 4：拆 `content_knowledge.db`

阶段 5：建立 `analytics_read_model.db`

## 12. 明确禁止的反模式

- 按页面拆表或拆库
- 为了方便在多个域里重复维护同一状态字段
- Router 直接跨域多表混写
- 批量脚本跳过备份直接改生产库
- 统计页直接承担主业务写入
- Agent 直接绕过业务服务改核心表
- 没有 `request_id`、`actor_key`、`trace_id` 的关键写入
- 把数据库编号硬编码进主业务 ID
- 把链路追踪 ID 当成实体主身份使用

## 13. 最终原则总结

这套架构的核心不是“分库”，而是：

1. 业务边界先于技术实现。
2. 每个域拥有自己的写模型。
3. 跨域交互必须可追踪、可恢复、可审计。
4. 最重要的学习资产必须能独立备份、独立恢复。
