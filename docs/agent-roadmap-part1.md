# Agent Roadmap Part 1

## 文档定位

本文是 `true-learning-system` Agent 演进方案的第一部分，目标是让研发先评估三件事：

1. 当前实现到底处于什么阶段
2. 从“大模型助手”演进到“可执行 Agent”还缺什么
3. 第一阶段应该怎么落地，才能在现有系统上低风险推进

本文不展开详细数据库表结构、接口协议和任务调度实现细节，这部分留到下一部分。


## 评审意见摘要（2026-03-16 补充）

本次评审基于代码审读（`agent_models.py`、`agent_tools.py`、`agent_runtime.py`）和三个主流 Agent 框架的横向对比，对原文做了以下增补：

1. **新增框架选型参考**（§4.3）：引入 PydanticAI、LangGraph、Strands Agents 作为各阶段的候选技术参照，建议 PoC 验证后决策
2. **新增参数幻觉防护**（§4.4）：LLM 可能编造写工具参数，必须在工具层做 Pydantic 强校验——当前读工具已有此模式，写工具必须延续
3. **新增可观测性要求**（§1.3 补充行）：审计日志只是一半，还需要 OpenTelemetry tracing 和 metrics
4. **增强阶段 1 实现方案**（§五）：给出写工具的具体分层模式和 human-in-the-loop 审批的落地方案
5. **增强第一阶段交付物**（§六）：从"3-5 个写动作"细化为具体的 5 个写工具定义和验收标准
6. **新增开源框架参考**（§九）：三个框架各取所长的具体借鉴清单


## 一、当前基线评估

### 1.1 当前已经具备的能力

基于现有代码，当前 Agent 已经不是单纯的聊天页，而是一个“带数据上下文和工具调用能力的学习助手”。

已有能力包括：

- 会话层
  - 有独立会话、消息、工具调用、turn 状态、摘要记忆、工具缓存
  - 参考实现：`agent_models.py`、`services/agent_runtime.py`、`services/agent_memory.py`

- 数据读取层
  - 能从学习系统数据库中读取进度、知识点掌握、错题、学习会话、学习历史、复习压力
  - 参考实现：`services/agent_tools.py`

- 外部知识检索层
  - 已接入 OpenViking，可以检索本地知识库和外部上下文
  - 参考实现：`services/openviking_service.py`

- 轻量规划层
  - 能根据用户问题自动选择工具
  - 能进行有限次数的 follow-up 补读和重规划
  - 能生成本轮计划、执行轨迹和响应策略
  - 参考实现：`services/agent_runtime.py`

- 记忆与持久化层
  - 会话可沉淀摘要记忆
  - 工具结果有 TTL 缓存
  - 参考实现：`services/agent_memory.py`

- 数据沉淀层
  - 学习数据、题目、会话等已经可以同步写入 OpenViking
  - 这让 Agent 后续具备长期知识库基础
  - 参考实现：`services/openviking_sync.py`

- 工具参数校验层（评审补充）
  - 读工具已使用 Pydantic BaseModel 做参数校验（`_ToolArgsModel` + `ConfigDict(extra="forbid")`）
  - 这是一个重要的安全基础，写工具必须延续并强化此模式
  - 参考实现：`services/agent_tools.py:21-56`


### 1.2 当前系统的本质定位

如果用一句话定义当前实现：

> 当前系统是一个“基于学习数据和知识库检索的多工具问答助手”，还不是完整意义上的 Agent。

原因不是模型不够强，而是系统能力边界还停留在：

- 读取信息
- 分析信息
- 组织回答

而没有进入真正 Agent 的关键阶段：

- 执行动作
- 持续跟踪目标
- 校验执行结果
- 在无人工逐轮指令下持续推进任务


### 1.3 当前与“真正 Agent”的差距

| 维度 | 当前状态 | 距离目标的缺口 |
| --- | --- | --- |
| 数据读取 | 已具备 | 基本够用 |
| 工具调用 | 已具备读工具 | 缺少写工具、动作工具 |
| 多轮规划 | 已具备轻量规划 | 缺少长期任务状态机 |
| 执行能力 | 基本没有 | 不能真正操作系统内对象 |
| 结果验证 | 基本没有 | 缺少 action 后校验与修复 |
| 异步运行 | 基本没有 | 缺少后台执行器和调度器 |
| 主动性 | 基本没有 | 不能事件驱动、定时驱动 |
| 长期用户模型 | 弱 | 只有会话摘要，没有稳定偏好模型 |
| 安全与审计 | 初步 | 缺少动作审批、回滚、操作日志闭环 |
| 参数安全（评审补充） | 读工具有 Pydantic 校验 | 写工具缺少同等强度的参数校验和幻觉防护 |
| 可观测性（评审补充） | 基本没有 | 缺少 tracing、metrics、告警，审计日志只是一半 |
| 框架复用（评审补充） | 全部自研 | 未借力成熟框架（PydanticAI/LangGraph），重复造轮子风险 |


## 二、目标定义

### 2.1 本项目里“Agent”的定义

在这个学习系统里，Agent 不应被定义为“更会说话的模型”，而应被定义为：

> 一个能够围绕学习目标，调用读写工具，持续推进任务，并对结果负责的学习代理。

它至少要具备以下能力：

- 能理解目标
  - 例如“帮我把今天最该复习的内容整理好”

- 能拆解任务
  - 例如拆成“识别待复习项 -> 排优先级 -> 生成计划 -> 写入任务 -> 校验结果”

- 能执行动作
  - 不是只给建议，而是实际创建记录、更新状态、写入知识库、生成题目

- 能自我校验
  - 动作执行后主动确认是否成功，失败时重试或回退

- 能跨轮持续
  - 用户离开后，任务状态仍存在；下次回来可以续跑，而不是重新问一遍

- 能在特定条件下主动运行
  - 例如新错题出现、复习到期、资料上传完成后自动触发


### 2.2 本项目 Agent 的业务目标

Agent 的业务目标不是“回答更像人”，而是提高学习系统的执行效率和闭环能力。

核心目标包括：

- 从“用户自己管理学习”升级为“系统协助管理学习”
- 从“被动问答”升级为“主动推进任务”
- 从“数据展示”升级为“数据驱动行动”
- 从“单轮建议”升级为“长期学习管理”


### 2.3 可量化的目标指标

建议用下面几类指标评估 Agent 是否真的进化，而不是只看主观聊天体验。

产品指标：

- 用户发出目标后，系统自动完成的动作比例
- 计划生成后被实际执行的比例
- 错题进入复习闭环的覆盖率
- 新上传内容转化为题目、知识点、复习任务的自动化比例

系统指标：

- 任务执行成功率
- 动作后校验通过率
- 重试成功率
- 平均任务完成时长
- Agent 写操作的可追踪率和可审计率

体验指标：

- 用户为完成同一学习任务所需手动步骤数
- 用户二次追问率
- 用户对“系统有在替我推进事情”的感知强度


## 三、非目标与边界

为了避免第一阶段做成大而空的“全能 AI 平台”，需要先明确边界。

第一阶段不做：

- 不做多 Agent 编排
- 不做通用电脑操作 Agent
- 不做跨多个外部 SaaS 的复杂自动化
- 不做自主无限循环执行
- 不做高风险无确认写操作
- 不追求“完全拟人化”的对话体验

第一阶段只聚焦：

- 学习系统内部对象
  - 题目
  - 错题
  - 学习会话
  - 复习计划
  - OpenViking 知识沉淀

- 有明确审计边界的动作
  - 创建
  - 更新
  - 归档
  - 生成
  - 同步


## 四、技术路线原则

### 4.1 总原则

建议技术路线遵循四个优先级：

1. 先补“动作能力”，不要先堆提示词
2. 先补“状态机”，不要先做复杂多 Agent
3. 先补“校验和审计”，不要先放开自治
4. 先做“系统内闭环”，再做“外部自动化”


### 4.2 架构原则

建议路线是：

- 单 Agent 核心
  - 先保持单个 Agent Runtime，不引入多 Agent 协同复杂度

- 工具优先
  - 所有动作都通过显式工具实现，而不是把副作用藏在 prompt 里

- 状态优先
  - 所有任务都必须有持久化状态，不能只存在于上下文窗口

- 审计优先
  - 所有写操作都必须记录“谁触发、为什么触发、写了什么、是否成功”

- 异步优先
  - 只要任务可能超过单次 HTTP 请求时间，就要进入后台执行模型

- 可回退优先
  - Agent 的动作必须具备幂等、重试和失败补偿设计


### 4.3 框架选型参考（评审补充）

原文完全基于自研路线，但当前 Agent 框架生态已经成熟，值得在技术评审阶段纳入对比评估。以下三个框架在各自擅长的维度上可以作为候选参考：

| 框架 | 核心能力 | 可能适用阶段 | 评估方向 |
| --- | --- | --- | --- |
| PydanticAI | 结构化输出校验、human-in-the-loop 审批、依赖注入 | 阶段 1 候选 | 评估其 tool decorator + approval 模式是否可复用 |
| LangGraph | 图状态机、checkpoint 持久化、中断/恢复、长期记忆 | 阶段 2 候选 | 评估其 StateGraph 模式对任务状态机的适用性 |
| Strands Agents | 极简 tool decorator、模型无关、MCP 原生支持 | 工具层参考 | 评估其 `@tool` 装饰器模式能否简化写工具注册 |

候选评估要点：

- PydanticAI 作为阶段 1 候选的理由：
  - 项目已在用 Pydantic 做工具参数校验，如果集成，迁移成本相对最低
  - 内置 human-in-the-loop tool approval（deferred tools），与"动作审批"需求方向一致
  - 结构化输出校验可用于防止 LLM 编造参数
  - 依赖注入模式（`RunContext[Deps]`）适合传递 DB Session
  - 注意：其 durable execution 能力依赖 Temporal/DBOS/Prefect 等外部编排引擎集成，不是开箱即用的内置能力，引入成本需要单独评估
  - 参考文档：https://ai.pydantic.dev/deferred-tools/ 、https://ai.pydantic.dev/durable_execution/overview/

- LangGraph 作为阶段 2 候选的理由：
  - 任务状态机本质是有向图，LangGraph 的 StateGraph 是目前社区采用最广的实现之一
  - 内置 checkpoint 持久化，方向上匹配"任务暂停/恢复"需求
  - 但其文档质量和 API 稳定性在社区中有争议，需要实际 PoC 验证

- 总体原则：
  - 不建议直接全量引入任何框架替换现有 runtime
  - 建议在阶段 1 启动前做一次轻量 PoC（1-2 天），验证 PydanticAI 的 tool approval 模式是否能在现有架构上跑通
  - 如果 PoC 不通过，退回自研路线，参考其设计模式即可
  - 保持现有 `agent_runtime.py` 的 orchestration 主控权


### 4.4 参数幻觉防护（评审补充）

这是原文完全遗漏的关键风险。LLM 在调用写工具时可能：

- 编造不存在的 ID（如虚构的错题 ID、章节 ID）
- 生成格式正确但语义错误的参数（如把"生理学"写成"生理"导致匹配失败）
- 在多轮对话中混淆不同轮次的数据

防护方案（必须在阶段 1 落地）：

1. Pydantic 强校验
   - 所有写工具参数必须继承 `_ToolArgsModel`（已有模式）
   - 关键 ID 字段加 `Field(pattern=...)` 正则约束
   - 枚举字段用 `Literal` 类型限定取值范围

2. 引用校验（Reference Validation）
   - 写工具接收的 ID 必须先查库确认存在
   - 不存在时返回明确错误，而不是静默创建

3. 参数来源追踪
   - 写工具的参数应尽量从读工具的返回值中提取，而不是让 LLM 自由生成
   - 在 `AgentToolCall` 表中记录参数来源（用户输入 / 读工具返回 / LLM 生成）


## 五、演进路线总览

### 阶段 0：当前阶段

定位：

- 多工具问答助手
- 有会话、计划、记忆、知识检索
- 主要还是“读数据并回答”

这部分已经基本具备，不建议再把主要时间投入在 prompt 微调上。


### 阶段 1：Action-Capable Agent

目标：

- 让 Agent 具备系统内”可执行动作”能力

必须补的能力：

- 写工具
  - 创建复习任务
  - 写入题目集
  - 更新错题状态
  - 写入学习计划
  - 写入 Agent 决策记录

- 动作审批机制
  - 区分只读动作与写动作
  - 高风险动作默认需要确认

- 动作结果校验
  - 写完后自动回读校验
  - 校验失败时返回失败状态而不是假装成功

- Action Log
  - 每次动作都持久化日志

阶段 1 完成后，系统才算从”助手”进入”初级 Agent”。


#### 阶段 1 实现方案细化（评审补充）

写工具分层架构：

```
用户请求 → Agent Runtime（orchestration）
    → 读工具（现有，不变）
    → 写工具（新增）
        → 参数校验层（Pydantic BaseModel，强制）
        → 权限检查层（tool_risk_level: low/medium/high）
        → 引用校验层（ID 存在性检查）
        → 执行层（实际 DB 写入）
        → 回读校验层（写后读，确认一致）
        → 审计日志层（写入 AgentActionLog）
```

写工具注册模式（参考 PydanticAI + Strands）：

```python
# 建议的写工具定义模式（以 W1 为例）
class CreateDailyReviewPaperArgs(_ToolArgsModel):
    wrong_answer_ids: List[int] = Field(min_length=1, max_length=20)
    concept_ids: List[int] = Field(default_factory=list, max_length=10)
    priority: Literal["urgent", "normal", "low"] = "normal"

class WriteToolResult(BaseModel):
    success: bool
    action_id: str          # 审计追踪 ID
    affected_ids: List[int] # 被操作的对象 ID
    verification: str       # 回读校验结果: "verified" | "mismatch" | "failed"
    rollback_hint: str      # 回滚提示（如何撤销此操作）
```

Human-in-the-loop 审批模式（参考 PydanticAI deferred tools）：

```
工具风险等级定义：
- low:    直接执行（如：写入 Agent 决策记录）
- medium: 展示预览 + 用户确认（如：创建复习任务、更新错题状态）
- high:   展示预览 + 用户确认 + 二次确认（如：批量写入题目集、修改学习计划）

UI 交互流程：
1. Agent 生成写动作提案 → 前端展示"建议动作"卡片
2. 用户点击"确认执行" → 后端执行写工具
3. 执行结果回显 → 前端展示"已执行动作"卡片（含回滚按钮）
```

新增数据模型（`agent_models.py` 扩展）：

```python
class AgentActionLog(Base):
    __tablename__ = "agent_action_logs"

    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("agent_sessions.id"))
    tool_name = Column(String, nullable=False, index=True)
    tool_args = Column(JSON, nullable=False)
    risk_level = Column(String, nullable=False)  # low/medium/high
    approval_status = Column(String, nullable=False)  # auto/pending/approved/rejected
    execution_status = Column(String, nullable=False)  # pending/success/failed/rolled_back
    result = Column(JSON, nullable=True)
    verification_status = Column(String, nullable=True)  # verified/mismatch/skipped
    error_message = Column(Text, nullable=True)
    triggered_by = Column(String, nullable=False)  # user_request/agent_plan/event
    created_at = Column(DateTime, default=datetime.now)
```


### 阶段 2：Task-Oriented Agent

目标：

- 让 Agent 不再依赖单轮请求，而是围绕“任务”持续运行

新增能力：

- 任务表和任务状态机
- 子任务编排
- 任务暂停 / 恢复
- 后台执行器
- 幂等重试


### 阶段 3：Event-Driven Agent

目标：

- 从“被问才动”升级为“在事件触发下主动推进”

新增能力：

- 上传完成触发
- 新错题触发
- 复习到期触发
- 定时生成学习计划
- 索引完成后自动补摘要或整理


### 阶段 4：Personalized Learning Agent

目标：

- 从“通用执行器”升级为“真正面向个人的学习代理”

新增能力：

- 长期偏好模型
- 个体学习节奏建模
- 动态任务负载控制
- 风格自适应
- 长期策略优化


## 六、第一阶段建议范围

对于当前项目，建议第一阶段只做到“Action-Capable Agent”，不要直接跳 Task Engine 和 Scheduler。

原因：

- 当前代码已经有会话、turn、工具、缓存、记忆和 OpenViking 集成
- 现阶段最缺的是“写能力”，不是“再多一个规划层”
- 如果先上任务调度，但没有可靠写工具和校验闭环，只会把错误批量自动化

第一阶段的最小可用目标建议定义为：

- Agent 可以基于用户目标执行 3 到 5 个明确的系统内写动作
- 每个动作都可审计
- 每个动作都有校验
- 写失败不会污染主流程
- UI 能向用户清楚展示”建议”和”已执行动作”的区别


### 第一阶段具体交付物（评审补充）

5 个写工具（按优先级排序）：

| 序号 | 写工具 | 风险等级 | 目标模型 | 验收标准 |
| --- | --- | --- | --- | --- |
| W1 | `create_daily_review_paper` 生成每日复习卷 | medium | `DailyReviewPaper` + `DailyReviewPaperItem`（已有） | 根据错题/到期知识点自动组卷，写入后可在复习卷列表查到 |
| W2 | `update_wrong_answer_status` 更新错题状态 | medium | `WrongAnswerV2`（已有） | 支持 active→archived/mastered 状态流转，回读校验状态一致 |
| W3 | `update_concept_mastery` 更新知识点掌握度 | medium | `ConceptMastery`（已有） | 根据做题数据批量更新掌握等级，回读校验等级一致 |
| W4 | `generate_quiz_set` 生成题目集 | high | `QuizSession` + `QuestionRecord`（已有） | 根据知识点范围自动出题并写入题库，题目格式校验通过 |
| W5 | `log_agent_decision` 写入决策记录 | low | `AgentActionLog`（新增，仅审计表） | 记录 Agent 每次规划和执行的决策依据，自动执行无需审批 |

注意：阶段 1 的写工具全部操作已有业务模型，不引入新的业务对象（如"任务表"或"计划表"）。新业务对象的引入属于阶段 2（Task-Oriented Agent）的范畴。唯一新增的表是 `AgentActionLog`，它是纯审计基础设施，不是业务对象。

基础设施交付物：

| 序号 | 组件 | 说明 |
| --- | --- | --- |
| I1 | `AgentActionLog` 数据模型 | 动作审计表，记录每次写操作的完整生命周期 |
| I2 | 写工具注册中心 | 扩展现有 `TOOL_DEFINITIONS`，增加 `tool_type: read/write` 和 `risk_level` 字段 |
| I3 | 参数校验层 | 每个写工具的 Pydantic Args 模型 + 引用校验 |
| I4 | 回读校验器 | 写后自动回读，比对预期结果 |
| I5 | 前端动作卡片 | UI 展示”建议动作”和”已执行动作”的区分 |

验收 checklist：

- [ ] 5 个写工具全部可通过 Agent 对话触发
- [ ] medium/high 风险工具在 UI 展示预览并等待用户确认
- [ ] 每次写操作在 `agent_action_logs` 表有完整记录
- [ ] 写失败时返回明确错误，不影响会话继续
- [ ] 回读校验覆盖率 100%（每个写工具都有）
- [ ] 参数校验拦截率：编造 ID 100% 被拦截


## 七、程序员评估关注点

研发在评估本方案时，建议重点看下面几个问题。

架构问题：

- 现有 `agent_runtime.py` 是否继续承担 orchestration，还是拆出 `agent_executor.py`
- 写工具是否与读工具放在同一注册中心
- 任务状态和动作日志是否复用现有 agent 表，还是单独建表

数据问题：

- 哪些业务对象允许 Agent 写入
- 哪些对象需要审批
- 哪些动作必须幂等

运行时问题：

- 第一阶段是否仍可同步执行
- 哪些动作必须转入后台 worker
- 如何定义超时、重试和补偿

安全问题：

- 如何防止模型直接拼接参数误写数据
- 如何限制 Agent 可操作范围
- 如何记录每次动作的输入、输出、校验结果

测试问题：

- 如何给写工具建立集成测试
- 如何验证“执行成功”和“结果被正确写入”是两回事
- 如何在测试里覆盖失败重试与幂等


## 八、第一部分结论

结论很明确：

- 当前系统已经具备”数据型助手”的基础设施
- 现阶段的瓶颈不在模型，而在执行层
- 下一步最值得投入的不是继续调 prompt，而是补 Action 能力、状态能力和校验能力
- （评审补充）项目已有 Pydantic 校验基础，建议在阶段 1 启动前用 1-2 天做 PydanticAI 的轻量 PoC，评估其 tool approval 模式是否可复用；如不适用则退回自研路线

因此建议将下一阶段目标正式定义为：

> 在当前单 Agent 架构上，先把系统升级为一个可执行、可审计、可校验的学习 Agent。框架选型（是否引入 PydanticAI）作为阶段 1 的前置评估项，不作为本文的预设结论。

如果这一结论通过技术评审，下一部分再展开：

- 模块拆分方案
- 数据模型设计
- 写工具分层方案
- 任务状态机设计
- 后台执行架构
- 测试与上线策略


## 九、开源框架参考与借鉴（评审补充）

### 9.1 PydanticAI（阶段 1 候选参考）

项目地址：https://github.com/pydantic/pydantic-ai

借鉴清单（需 PoC 验证后确认适用性）：

- `@agent.tool` 装饰器模式
  - 当前项目的 `TOOL_DEFINITIONS` 列表式注册可以保留，但写工具可考虑用装饰器模式简化注册
  - PydanticAI 的 tool 自动从 docstring 提取参数描述，减少重复定义

- `RunContext[Deps]` 依赖注入
  - 当前 `execute_agent_tool` 直接接收 `db: Session`，可以改为依赖注入模式
  - 好处：测试时可以注入 mock DB，生产时注入真实连接

- Human-in-the-loop tool approval（deferred tools）
  - PydanticAI 支持标记某些工具需要审批后才能执行
  - 方向上映射到本项目的 medium/high 风险写工具
  - 参考文档：https://ai.pydantic.dev/deferred-tools/

- Structured output validation
  - 强制 LLM 返回符合 Pydantic 模型的结构化数据
  - 校验失败时自动重试，而不是把错误数据写入数据库

- OpenTelemetry 集成
  - 通过 Pydantic Logfire 或任何 OTel backend 实现 tracing
  - 每次工具调用自动生成 span，包含参数、耗时、结果
  - 参考文档：https://ai.pydantic.dev/logfire/

- Durable execution（阶段 2+ 再评估）
  - 注意：此能力依赖 Temporal / DBOS / Prefect 等外部编排引擎，不是 PydanticAI 内置能力
  - 引入成本较高，不建议在阶段 1 考虑
  - 参考文档：https://ai.pydantic.dev/durable_execution/overview/


### 9.2 LangGraph（阶段 2 候选参考）

项目地址：https://github.com/langchain-ai/langgraph

借鉴清单：

- StateGraph 状态机
  - 任务状态流转（pending → running → verifying → completed/failed）用图定义
  - 每个节点是一个处理步骤，边是条件转移

- Checkpoint 持久化
  - 任务执行到任意步骤都可以持久化状态
  - 用户离开后下次回来可以从断点恢复
  - 方向上匹配”跨轮持续”需求，需 PoC 验证实际集成成本

- Interrupt 机制
  - 在图的任意节点插入中断点
  - 等待人工审批后继续执行
  - 如果验证可行，可能比自研审批队列更省开发量

- Durable execution
  - 任务在 API 超时、进程重启后自动恢复
  - 阶段 2 的后台执行器可以评估是否借用此能力


### 9.3 Strands Agents（工具模式参考）

项目地址：https://github.com/strands-agents/sdk-python

借鉴清单：

- `@tool` 装饰器极简模式
  - 函数签名 + docstring 自动生成工具 schema
  - 当前项目的 `AgentToolDefinition` 手动定义可以简化

- 模型无关设计
  - 当前项目绑定 DeepSeek，但应保留切换能力
  - Strands 的 provider 抽象层值得参考

- 工具热重载
  - 从目录自动加载工具，修改后无需重启
  - 适合写工具的快速迭代阶段


### 9.4 不建议做的事

- 不要直接把 PydanticAI 或 LangGraph 作为项目的 runtime 替换现有 `agent_runtime.py`
  - 现有 runtime 已经有会话管理、记忆、缓存等定制逻辑
  - 全量替换的迁移成本远大于选择性借鉴

- 不要在阶段 1 引入 LangGraph
  - 阶段 1 不需要状态机，只需要写工具 + 审批 + 校验
  - 过早引入图编排会增加不必要的复杂度

- 不要同时引入多个框架
  - 阶段 1 只参考 PydanticAI 的模式
  - 阶段 2 再评估是否需要 LangGraph
