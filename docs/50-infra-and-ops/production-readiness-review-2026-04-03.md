# True Learning System 上线评估（2026-04-03）

## 结论

当前项目不适合直接作为“公网可访问、多用户正式使用”的生产系统上线。

它目前更接近：

- 单用户或弱隔离的内网学习系统
- 基于 `user_id/device_id` 的逻辑分桶，而不是正式认证系统
- 适合局域网 / Tailscale 私网试运行

如果目标是正式上线给不同用户使用，至少需要先完成：

1. 正式登录认证与服务端身份解析
2. 全量多用户数据隔离改造
3. 核心表主键/唯一键重构
4. 从 SQLite 迁移到 PostgreSQL
5. 生产安全与运维治理

## 核心发现

### P0. 目前没有真正的登录认证系统

代码中的“身份”主要来自：

- 请求头 `x-tls-user-id` / `x-tls-device-id`
- 前端 `localStorage` 中保存的 `tls_user_id` / `tls_device_id`
- 部分接口直接接收 `user_id/device_id` 查询参数或请求体字段

这意味着：

- 用户身份由客户端自己声明
- 任意人都可以伪造 `user_id`
- 当前并不存在服务端可信的“当前登录用户”
- 这不满足正式上线的认证与授权要求

直接证据：

- `services/data_identity.py`
- `lib/agent/identity.ts`
- `lib/agent/api-client.ts`
- `utils/agent_contracts.py`
- `routers/dashboard.py`
- `routers/agent.py`

### P0. 多个核心路由仍然是全局读写，不按用户隔离

虽然新表里已经开始引入 `user_id/device_id/actor_key`，但不少页面和接口仍直接查询全表数据。

典型问题：

- 首页 `/` 直接统计全部 `LearningSession`、`DailyUpload`、`WrongAnswerV2`
- `challenge` 路由没有 actor scope，队列、统计、提交都按全局错题执行
- `graph` 路由按全局 `ConceptMastery` / `ConceptLink` 返回图谱
- `feynman`、`fusion` 路由也没有完整用户隔离

这会导致：

- 不同用户之间的数据串读
- 首页和统计页展示全站混合数据
- 题库、错题、挑战队列可能跨用户污染

直接证据：

- `main.py`
- `routers/challenge.py`
- `routers/graph.py`
- `routers/feynman.py`
- `routers/fusion.py`

### P0. 数据库表结构里存在“伪多用户”设计，主键和唯一键会冲突

这是当前最关键的数据库问题。

#### `concept_mastery`

表里有 `user_id/device_id`，但主键仍是单列 `concept_id`。

后果：

- 不同用户不能对同一个 `concept_id` 各自保存独立掌握度
- 第二个用户的数据会覆盖、冲突或根本无法写入正确结构

#### `wrong_answers_v2`

表里有 `user_id/device_id`，但 `question_fingerprint` 是全局唯一。

后果：

- 多个用户做错同一题时会命中同一条错题记录
- `error_count`、`retry_count`、`mastery_status` 会被不同用户混写

#### 其他仍未完成多用户化的表

- `feynman_sessions` 没有用户字段
- `quiz_sessions` 没有用户字段
- `concept_links` 主键没有用户维度
- `variations` 没有用户字段
- `wrong_answers` 旧表没有用户字段

直接证据：

- `models.py`
- `learning_tracking_models.py`

### P1. 当前生产存储仍基于多个 SQLite 文件，不适合正式多用户服务

现状：

- 核心存储拆成多个 SQLite 文件
- 启动时直接 `create_all`
- 迁移依赖脚本和运行期 schema 补齐

SQLite 对单机、小流量、轻并发是可行的，但正式多用户上线会遇到这些问题：

- 写并发能力有限
- 锁竞争容易放大
- 备份、迁移、回滚、审计不够标准化
- 无法像 PostgreSQL 一样自然支持约束、迁移、连接管理和运维治理

当前数据体量仍然不大，迁移窗口很好：

- `content_knowledge.db` 约 38.9 MB
- `learning_runtime.db` 约 18.0 MB
- `wrong_answer_review.db` 约 19.6 MB
- `agent.db` 约 5.2 MB

这说明现在改造成本比后期上线后再迁移低很多。

### P1. 部署配置本身明确是单用户 / 私网假设

仓库里的部署文档已经明确写了：

- Docker 配置启用 `SINGLE_USER_MODE=true`
- 推荐 LAN / Tailscale 私网访问
- 明确提示不要在未加认证前暴露到公网

也就是说，当前作者自己也把它定位成“私网单用户部署方案”，不是正式 SaaS 或公网多用户方案。

直接证据：

- `docker-compose.yml`
- `DEPLOY-DOCKER.md`

### P1. Web 安全基线不足

目前能直接看到的问题：

- CORS 为 `allow_origins=["*"]`
- 同时 `allow_credentials=True`
- 未看到正式登录态中间件
- 未看到标准 CSRF 防护
- 未看到反向代理层安全头治理
- 未看到可信 Host / HTTPS 跳转 / 统一限流

这套配置在内网调试阶段可以接受，但不适合公网生产。

### P1. 当前基线还存在回归与测试问题

我实际运行了：

```powershell
pytest -q test_challenge_endpoints.py test_learning_tracking_regressions.py tests/test_quiz_batch_audit.py
```

结果：

- 6 个测试通过
- 8 个测试失败

明确失败点包括：

- `learning_tracking` 相关测试因运行时表初始化绑定全局 `runtime_engine` 而失败
- `routers/quiz_batch.py` 使用了 `INVALID_CHAPTER_IDS`，但当前文件内未正确引入，触发 `NameError`
- `tests/test_quiz_batch_audit.py` 在 Windows 下还遇到 `resource` 模块不可用问题

这说明当前项目还处于“功能持续演进、尚未收敛到稳定生产基线”的阶段。

## 目前值得保留的优点

项目不是从零开始，已经有一些对上线有帮助的基础：

- 已经有领域拆库思路：`content / runtime / review / agent`
- 很多新表已引入 `user_id/device_id/actor_key`
- 有数据库备份脚本
- 有 Docker 化部署基础
- 有一定测试集
- 有审计日志、脚本审计、数据修复脚本

这意味着：项目“有改造成生产系统的基础”，但不是“现在就能直接上线”。

## 建议的上线改造路线

### 阶段 1：先把身份系统做对

建议新增：

- `users` 表
- `user_profiles` 表
- `refresh_tokens` / `sessions` 表
- 可选 `roles` / `organizations`

认证方案建议：

- 邮箱 + 密码登录
- 密码哈希使用 `argon2` 或 `bcrypt`
- Access Token + Refresh Token
- Refresh Token 放在 `HttpOnly + Secure + SameSite` Cookie
- 服务端统一解析 `current_user`

改造原则：

- `user_id` 不再由前端自由传入作为可信身份
- `device_id` 只保留为设备标识或风控辅助字段
- 所有业务查询统一从 `current_user.id` 做隔离

### 阶段 2：数据库正式多用户化

建议优先迁移到 PostgreSQL，并引入 Alembic。

重点改造表：

- `concept_mastery`
  - 改为自增/UUID 主键
  - 唯一键建议：`(user_id, chapter_id, concept_id)`
- `wrong_answers_v2`
  - 唯一键改为：`(user_id, question_fingerprint)` 或 `(actor_key, question_fingerprint)`
- `concept_links`
  - 唯一键改为：`(user_id, from_concept, to_concept)`
- `feynman_sessions`
  - 增加 `user_id`
- `quiz_sessions`
  - 增加 `user_id`
- `variations`
  - 如果是用户级生成缓存，需要增加 `user_id`

同时做结构分层：

- 全局内容库：章节、知识目录、公共题库
- 用户运行库：学习会话、答题记录、错题、挑战、Agent 会话

### 阶段 3：把所有路由改成“服务端用户隔离”

要做的不是只改一两个接口，而是全量审计所有 router/page：

- 首页 `/`
- dashboard
- challenge
- graph
- fusion
- feynman
- upload workspace
- learning tracking
- wrong answers
- agent

建议新增统一依赖：

- `get_current_user()`
- `require_actor_scope()`
- `scoped_query(...)`

目标是保证：

- 页面级接口只返回当前用户数据
- 写操作只能写当前用户数据
- 不再允许前端随意指定别人的 `user_id`

### 阶段 4：生产安全与网关治理

上线前至少补齐：

- CORS 白名单
- Nginx / Caddy 反向代理
- HTTPS
- Trusted Host 校验
- 安全响应头
- 限流
- 请求体大小限制
- 管理接口和 Telegram 接口的环境隔离
- 密钥从 `.env` 本地文件管理升级到服务器 Secret 管理

### 阶段 5：稳定性与交付

建议上线前完成：

- 修复当前失败测试
- 增补多用户隔离测试
- 增补权限绕过测试
- 增补迁移回滚测试
- CI 中加入：
  - 单元测试
  - 迁移检查
  - 基础安全扫描

## 我建议的最小可上线版本

如果你的目标是“尽快上线给不同用户试用”，建议按这个最小路径做：

1. 先不上公网，先做一版私有测试环境
2. 接入 PostgreSQL
3. 加 `users + JWT/Cookie 登录`
4. 修正 `concept_mastery`、`wrong_answers_v2`、`quiz_sessions`、`feynman_sessions`
5. 把首页、challenge、graph、fusion、feynman、dashboard 全部改成服务端用户隔离
6. 关闭或限制高风险接口
7. 修复当前回归测试
8. 再挂到正式服务器

## 结论性建议

如果只是你自己或内网少量设备使用：

- 现有项目可以继续按当前 Docker/Tailscale 模式运行

如果是正式给多个用户上线：

- 不建议直接部署当前版本到公网
- 正确做法是先完成“认证 + 数据模型 + 路由隔离 + PostgreSQL + 安全治理”这五件事

## 推荐下一步执行项

如果继续推进开发，建议按下面顺序落地：

1. 设计用户表、登录表和 token 表
2. 接入 PostgreSQL + Alembic
3. 重构 `concept_mastery` 和 `wrong_answers_v2` 约束
4. 改造首页、challenge、graph、fusion、feynman 的用户隔离
5. 再做服务器部署和反向代理

