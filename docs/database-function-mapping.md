# True Learning System 数据库功能映射与解耦备份说明

更新时间：2026-03-20

## 1. 文档目的

这份文档用于回答 5 个问题：

1. 当前项目实际在用哪个数据库文件。
2. 现有功能分别依赖哪些数据表。
3. 每张表存了什么，为什么要存，对应什么功能。
4. 如果要做数据库解耦，应该如何拆库。
5. 如果要做备份，哪些数据必须优先备份，哪些数据可以降级处理。

## 2. 当前实际数据库现状

截至 2026-03-20 当前代码已经进入“分库过渡期”，不再是纯单库。

当前 `localhost:8000` 实际使用的数据库文件是：

- `C:\Users\35456\true-learning-system\data\learning.db`
- `C:\Users\35456\true-learning-system\data\agent.db`
- `C:\Users\35456\true-learning-system\data\wrong_answer_review.db`

当前实际分布：

- `learning.db`
  - 上传数据
  - 章节与知识点
  - 做题记录
  - 旧兼容表
- `agent.db`
  - `agent_*`
- `wrong_answer_review.db`
  - `wrong_answers_v2`
  - `wrong_answer_retries`
  - `daily_review_papers`
  - `daily_review_paper_items`
  - `chapter_review_*`

也就是说，当前物理层已经完成两步拆分：

1. Agent 域已拆出
2. Modern Review 域已拆出

尚未拆出的仍主要是：

- 内容知识底座
- 做题运行态
- 旧兼容错题表 `wrong_answers`

## 3. 功能总览与建议拆库总表

| 功能域 | 当前页面/API | 当前主要表 | 建议归属数据库 | 说明 |
|---|---|---|---|---|
| 内容上传与课程知识底座 | `/upload` `/chapter/{id}` `/graph` `/api/chapters*` `/api/concept/{id}` | `daily_uploads`, `chapters`, `concept_mastery`, `knowledge_*` | `content_knowledge.db` | 全站知识基础层 |
| 做题运行态 | `/quiz/batch/*` `/quiz/detail` `/quiz/practice/*` `/quiz/fast/*` `/quiz/super/*` `/api/tracking/session/*` | `learning_sessions`, `learning_activities`, `question_records`, `batch_exam_states`, `quiz_sessions`, `test_records`, `feynman_sessions`, `variations` | `learning_runtime.db` | 做题、交卷、会话恢复的运行数据 |
| 错题与复习闭环 | `/wrong-answers` `/api/wrong-answers/*` `/api/challenge/*` `/api/fusion/*` `/knowledge-archive` | `wrong_answers_v2`, `wrong_answer_retries`, `daily_review_papers`, `daily_review_paper_items`, `chapter_review_*`, `wrong_answers` | `wrong_answer_review.db` | 其中 modern review 表已实际迁出，`wrong_answers` 旧表暂未迁出 |
| 历史统计与看板 | `/` `/history` `/learning-tracking` | 读取上面多个域的表，另有 `daily_learning_logs`, `learning_insights`, `knowledge_daily_reports` | 聚合层，不建议单独建库 | 本质是跨库读取或预聚合 |
| Agent | `/agent` `/api/agent/*` | `agent_sessions`, `agent_messages`, `agent_memories`, `agent_tool_calls`, `agent_turn_states`, `agent_tool_cache`, `agent_action_logs`, `agent_tasks`, `agent_task_events` | `agent.db` | 天然适合独立拆库 |
| 兼容旧功能 | `/api/quiz/*` 旧接口 | `wrong_answers`, `quiz_sessions`, `test_records`, `feynman_sessions`, `variations`, `concept_links` | 过渡期可放 `legacy_compat.db` 或暂放 `learning_runtime.db` | 当前仍被部分旧路由使用 |

## 4. 当前功能与表的详细映射

### 4.1 内容上传与知识底座

对应页面与接口：

- 页面：`/upload`、`/chapter/{chapter_id}`、`/graph`
- 接口：`/api/upload*`、`/api/chapters*`、`/api/chapter/{id}`、`/api/concept/{id}`

对应表说明：

| 表名 | 存什么 | 为什么存 | 对应功能 |
|---|---|---|---|
| `daily_uploads` | 每次原始上传内容、日期、AI 提取结果 | 保留上传源数据，便于追溯、重解析、历史统计 | 上传历史、首页上传统计、章节回溯 |
| `chapters` | 章节 ID、书名、章号、标题、摘要、概念列表 | 作为全系统共享的章节目录和知识挂载点 | 章节页、章节选择器、图谱、错题归章 |
| `concept_mastery` | 每个知识点的掌握度、理解度、应用度、下次复习时间 | 需要记录“知识点状态”而不是只记题目对错 | 概念页、学习追踪、后续选题与复习 |
| `knowledge_upload_records` | 新上传工作台的一次整理会话 | 新上传体系需要保留工作台上下文和中间态 | `/upload` 新工作台 |
| `knowledge_point_notes` | 保存后的知识点笔记正文 | 上传后不只是识别，还要形成可编辑知识卡片 | 上传工作台、笔记编辑、练习 |
| `knowledge_point_sources` | 知识点笔记对应的来源片段和来源上传记录 | 需要知道一条知识点从哪里来，便于追溯与解释 | 来源追踪、笔记可解释性 |
| `knowledge_pending_classifications` | 暂时无法自动归类的知识点条目 | 上传时经常会遇到无法确定章节的中间态，需要人工解决 | 上传工作台待归类列表 |
| `knowledge_daily_reports` | 某天知识整理工作的快照汇总 | 便于展示当天整理了哪些知识点 | `/api/upload/daily-report` |
| `concept_links` | 知识点之间的关系边 | 用于知识图谱、关联知识点 | 图谱、关系扩展 |

这块数据的本质是“教材与笔记知识层”，不是运行时缓存，而是全站最基础的知识源。

### 4.2 做题运行态

对应页面与接口：

- 页面：`/quiz/batch/{chapter_id}`、`/quiz/detail`、`/quiz/practice/{chapter_id}`、`/quiz/fast/{chapter_id}`、`/quiz/super/{chapter_id}`、`/exam`、`/session-review`、`/learning-tracking`
- 接口：`/api/quiz/batch/*`、`/api/tracking/*`、旧接口 `/api/quiz/*`

对应表说明：

| 表名 | 存什么 | 为什么存 | 对应功能 |
|---|---|---|---|
| `learning_sessions` | 一次完整学习会话，含标题、类型、总题数、正确数、时长、状态 | 需要有“会话”概念，承载多道题的完整学习记录 | 学习轨迹、会话详情、历史统计 |
| `learning_activities` | 会话内的细粒度行为日志 | 支持以后做回放、行为分析、埋点统计 | 会话分析、事件记录 |
| `question_records` | 每一题的题干、选项、标准答案、用户答案、信心、耗时、考点 | 这是“真实做题明细”的核心表 | 会话详情、知识归档、统计分析、错题收录 |
| `batch_exam_states` | 分段整卷生成过程中的中间状态和缓存 | 整卷生成是多步流程，需要断点恢复和前后端协同 | 批量考试、整卷恢复 |
| `daily_learning_logs` | 按天聚合的学习日志 | 提高日视图和统计查询效率 | 学习轨迹、日历日志 |
| `learning_insights` | AI 生成的学习建议或异常提醒 | 为后续智能分析预留 | 学习洞察 |
| `quiz_sessions` | 旧版练习/考试会话 | 旧路由仍然在写，不能直接删除 | 旧版 `/api/quiz/*` |
| `test_records` | 旧版单题 AI 出题和批改记录 | 兼容旧页、概念详情页和旧接口 | 旧版单题练习、概念页历史 |
| `feynman_sessions` | 费曼讲解多轮对话记录 | 保存讲解会话上下文和轮次 | `/feynman/{concept_id}` |
| `variations` | 旧版变式题库存 | 历史兼容 | 旧变式题功能 |

这块数据的本质是“做题过程层”，特点是写入频繁、体量增长快、统计查询多、断点恢复需求强，非常适合单独拆库。

### 4.3 错题与复习闭环

对应页面与接口：

- 页面：`/wrong-answers`、`/knowledge-archive`、`/history`
- 接口：`/api/wrong-answers/*`、`/api/challenge/*`、`/api/fusion/*`、`/api/history/review-*`

对应表说明：

| 表名 | 存什么 | 为什么存 | 对应功能 |
|---|---|---|---|
| `wrong_answers_v2` | 新版错题主表，按题目指纹聚合，含严重度、SM-2、章节、变式、融合字段 | 这是目前错题系统主数据 | 错题本、挑战、融合、每日复习、导出 |
| `wrong_answer_retries` | 每次错题重做的结果、信心、用时、是否变式、AI 评价 | 错题主表只能存汇总，细节需要历史轨迹 | 重做记录、准确率、SM-2 驱动 |
| `daily_review_papers` | 某天为某个 actor 生成的每日复习卷 | 需要让每日复习卷稳定可复现 | 每日复习 PDF、每日计划 |
| `daily_review_paper_items` | 每日复习卷里的具体错题快照和顺序 | 生成 PDF、避重、复现时需要 | 每日复习卷详情 |
| `chapter_review_chapters` | 按章节聚合的复习主表 | 上传内容后形成章节级复习对象 | 章节复习 |
| `chapter_review_units` | 章节拆分出来的复习单元 | 长章节必须拆块，不然无法每天复习 | 章节复习切片 |
| `chapter_review_tasks` | 每日章节复习任务 | 需要排期、打分、完成状态 | 历史复习计划、每日复习任务 |
| `chapter_review_task_questions` | 章节复习任务生成的问答题与作答内容 | 要支持继续作答、AI 批改、PDF 导出 | 章节复习题目 |
| `wrong_answers` | 旧版错题表 | 部分旧路由仍在读写 | 旧版 `/api/quiz/wrong-answers/*` |

`wrong_answers_v2` 同时承担：

1. 错题列表主记录
2. 复习调度对象
3. 变式题缓存对象
4. 融合题升级对象

这也是它最值得单独拆库的原因。

### 4.4 历史、统计、归档、看板

这块多数不是“原始事实表”，而是从别的域聚合出来的。

它高度依赖：

- `daily_uploads`
- `chapters`
- `concept_mastery`
- `learning_sessions`
- `question_records`
- `wrong_answers_v2`
- `chapter_review_*`
- `daily_learning_logs`

建议做法：

- 保留少量预聚合表，例如 `daily_learning_logs`
- 但从架构上把它看作“统计聚合层”，不是主业务源库

### 4.5 Agent 数据域

对应页面与接口：

- 页面：`/agent`
- 接口：`/api/agent/*`

对应表说明：

| 表名 | 存什么 | 为什么存 | 对应功能 |
|---|---|---|---|
| `agent_sessions` | Agent 会话主记录 | 需要保存会话和会话状态 | 会话列表、恢复会话 |
| `agent_messages` | 对话消息历史 | 聊天页面最核心的数据 | 聊天历史 |
| `agent_memories` | 会话摘要和记忆片段 | 支持长会话压缩和记忆 | Agent 记忆 |
| `agent_tool_calls` | Agent 工具调用记录 | 便于审计和回放 | 工具使用历史 |
| `agent_turn_states` | 每轮对话的计划与执行状态 | 支持流式执行与调试 | 回合状态 |
| `agent_tool_cache` | 工具缓存 | 降低重复调用成本 | Agent 性能优化 |
| `agent_action_logs` | 高风险动作执行记录 | 用于审批和可追踪性 | Agent 动作面板 |
| `agent_tasks` | Agent 任务列表 | 支持任务式工作流 | Agent 任务 |
| `agent_task_events` | 任务状态流转日志 | 任务需要事件历史 | Agent 任务历史 |

这块数据的本质是独立性最高的一块，已经天然符合单独拆库条件。

## 5. 关键主键、外键与跨域关联

关键标识符：

- `chapter_id`
- `concept_id`
- `session_id`
- `wrong_answer_id`
- `actor_key`
- `device_id`
- `user_id`

当前最重要的关系链：

1. 上传到知识底座：`daily_uploads -> chapters -> concept_mastery -> knowledge_* -> chapter_review_chapters`
2. 考试到错题：`learning_sessions -> question_records -> wrong_answers_v2 -> wrong_answer_retries -> daily_review_papers/items`
3. 章节复习任务：`daily_uploads + chapters -> chapter_review_chapters -> chapter_review_units -> chapter_review_tasks -> chapter_review_task_questions`
4. Agent 与业务数据：`agent_*` 是 Agent 自己的会话域，但 Agent 会跨域读取 `learning_sessions`、`question_records`、`wrong_answers_v2`、`daily_review_papers`

所以 Agent 拆库后，仍需要通过服务层访问其他业务库。

## 6. 建议的拆库方案

推荐拆成 4 个主数据库：

### `content_knowledge.db`

建议放：

- `daily_uploads`
- `chapters`
- `concept_mastery`
- `knowledge_upload_records`
- `knowledge_point_notes`
- `knowledge_point_sources`
- `knowledge_pending_classifications`
- `knowledge_daily_reports`
- `concept_links`

### `learning_runtime.db`

建议放：

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

### `wrong_answer_review.db`

建议放：

- `wrong_answers_v2`
- `wrong_answer_retries`
- `daily_review_papers`
- `daily_review_paper_items`
- `chapter_review_chapters`
- `chapter_review_units`
- `chapter_review_tasks`
- `chapter_review_task_questions`
- `wrong_answers`

### `agent.db`

建议放：

- 全部 `agent_*`

过渡期可选 `legacy_compat.db`，临时承接旧表：

- `quiz_sessions`
- `test_records`
- `wrong_answers`
- `feynman_sessions`
- `variations`

## 7. 为什么这些表要存在

为了保留事实：

- `daily_uploads`
- `learning_sessions`
- `learning_activities`
- `question_records`
- `wrong_answers_v2`
- `wrong_answer_retries`
- `agent_messages`
- `agent_action_logs`

为了保留结构化知识：

- `chapters`
- `concept_mastery`
- `knowledge_point_notes`
- `knowledge_point_sources`
- `concept_links`

为了保留计划与调度状态：

- `batch_exam_states`
- `daily_review_papers`
- `daily_review_paper_items`
- `chapter_review_chapters`
- `chapter_review_units`
- `chapter_review_tasks`
- `chapter_review_task_questions`

为了保留聚合结果：

- `daily_learning_logs`
- `knowledge_daily_reports`
- `learning_insights`
- `agent_tool_cache`

## 8. 备份优先级建议

必须优先备份：

- `daily_uploads`
- `chapters`
- `concept_mastery`
- `learning_sessions`
- `question_records`
- `wrong_answers_v2`
- `wrong_answer_retries`
- `daily_review_papers`
- `daily_review_paper_items`
- `chapter_review_chapters`
- `chapter_review_units`
- `chapter_review_tasks`
- `chapter_review_task_questions`
- `knowledge_upload_records`
- `knowledge_point_notes`
- `knowledge_point_sources`
- `knowledge_pending_classifications`
- `agent_sessions`
- `agent_messages`
- `agent_action_logs`
- `agent_tasks`
- `agent_task_events`

建议备份但可降级：

- `learning_activities`
- `daily_learning_logs`
- `knowledge_daily_reports`
- `learning_insights`
- `agent_memories`
- `agent_turn_states`

可重建或可临时忽略：

- `agent_tool_cache`
- 某些纯缓存态 `batch_exam_states`

## 9. 实际备份策略建议

如果继续维持单文件 SQLite：

1. 每天自动备份一次全库
2. 每次进行批量修复、迁移、清洗、切库前，强制做一次时间戳备份
3. 保留最近 7 天每日快照、最近 4 周每周快照、所有重大操作前手动快照

如果按建议拆库，建议备份频率：

| 数据库 | 建议频率 | 原因 |
|---|---|---|
| `wrong_answer_review.db` | 每日 + 重大操作前立即备份 | 用户价值最高 |
| `learning_runtime.db` | 每日 | 增长快，易被误写 |
| `content_knowledge.db` | 每日或每两日 | 变动相对少一些 |
| `agent.db` | 每日 | 独立性强，备份成本低 |

恢复优先顺序建议：

1. `content_knowledge.db`
2. `wrong_answer_review.db`
3. `learning_runtime.db`
4. `agent.db`

## 10. 解耦实施优先级建议

1. 先把 Agent 独立
2. 再把错题与复习域独立
3. 然后把做题运行态独立
4. 最后把内容与知识底座独立

## 11. 最终建议总结

当前你最应该认定的 3 个事实：

1. 现在物理上还是一个库：`learning.db`
2. 真正最重要的数据不是考试缓存，而是知识底座、做题事实、错题闭环
3. 如果要解耦，正确顺序不是先全拆，而是先按业务域拆

我给你的推荐目标形态：

- `content_knowledge.db`
- `learning_runtime.db`
- `wrong_answer_review.db`
- `agent.db`

你现在最需要优先保护的资产：

- `wrong_answers_v2`
- `wrong_answer_retries`
- `question_records`
- `learning_sessions`
- `daily_uploads`
- `chapters`
- `concept_mastery`

这些决定了：

- 你学过什么
- 你做过什么
- 你错过什么
- 你该复习什么
