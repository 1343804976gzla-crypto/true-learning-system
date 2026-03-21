# API Hub 使用说明与架构文档

> 本文档面向所有需要接入 LLM API 的功能模组开发者。
> 任何新增的 service / router 如果需要调用大模型，**必须通过 API Hub**，不得自行创建 OpenAI 客户端。

---

## 1. 架构总览

### 1.1 重构前后对比

```
重构前:
  services/ai_client.py (1086 行单体)
    ├── Provider 注册
    ├── 池解析 + 默认池构建
    ├── 重试逻辑 + 瞬态错误检测
    ├── 池遍历调用
    ├── 流式生成
    ├── JSON 生成 + 修复管线
    └── 审计日志

重构后:
  services/ai_client.py          ← 4 行向后兼容垫片
  services/api_hub/
    ├── __init__.py               ← 包入口，re-export AIClient / get_ai_client
    ├── _types.py                 ← 共享类型定义
    ├── provider_registry.py      ← Provider 注册与凭证管理
    ├── pool_manager.py           ← 池路由、默认池、动态切换
    ├── retry_engine.py           ← 重试逻辑、池遍历、瞬态错误检测
    ├── stream_handler.py         ← 流式生成（线程桥接 SSE）
    ├── json_handler.py           ← JSON 生成 + 修复管线
    ├── facade.py                 ← AIClient 薄门面 + get_ai_client() 单例
    ├── health_monitor.py         ← 滑动窗口健康追踪
    ├── cost_tracker.py           ← Token 成本计算与预算告警
    └── models.py                 ← SQLAlchemy 模型 (api_hub_usage 等)
  routers/api_hub.py              ← 管理 REST API (12 个端点)
  templates/api_hub.html          ← Jinja2 管理仪表盘
```

### 1.2 核心设计原则

1. **零消费者变更**: 所有 15+ 个 service 的 `from services.ai_client import get_ai_client` 继续工作
2. **单例模式**: `get_ai_client()` 返回全局唯一的 `AIClient` 实例，线程安全
3. **池化路由**: 按任务类型分配模型池 (Heavy / Light / Fast)，池内有序 fallback
4. **运行时可管理**: 通过 REST API 动态启停 Provider、重配池、查看用量

---

## 2. 快速上手：如何在新模组中调用 LLM

### 2.1 最简调用（推荐）

```python
from services.ai_client import get_ai_client

async def my_feature():
    client = get_ai_client()

    # 文本生成（走 Light 池，适合评估/分类等轻量任务）
    result = await client.generate_content("请总结以下内容：...")

    # 文本生成（走 Heavy 池，适合出题/变式等需要创造力的任务）
    result = await client.generate_content("请出5道选择题：...", use_heavy=True)

    # JSON 生成（自动两轮重试 + Fast 池兜底）
    data = await client.generate_json(
        prompt="请生成一道选择题",
        schema={"question": "", "options": [], "answer": ""},
        use_heavy=True,
    )

    # 流式生成（用于 SSE 推送给前端）
    async for chunk in client.generate_content_stream("请详细解释..."):
        yield chunk  # 逐块推送
```

### 2.2 三个公开方法的完整签名

```python
# ① 文本生成
async def generate_content(
    self,
    prompt: str,                                    # 用户 prompt
    max_tokens: int = 4000,                         # 最大输出 token
    temperature: float = 0.3,                       # 温度
    timeout: int = 120,                             # 池级总超时（秒）
    use_heavy: bool = False,                        # True → Heavy池, False → Light池
    preferred_provider: Optional[str] = None,       # 指定 provider（如 "deepseek"）
    preferred_model: Optional[str] = None,          # 指定 model（如 "deepseek-chat"）
    audit_context: Optional[Dict] = None,           # 审计上下文（通常不需要手动传）
) -> str

# ② 流式生成
async def generate_content_stream(
    self,
    prompt: str,
    max_tokens: int = 4000,
    temperature: float = 0.3,
    timeout: int = 120,
    use_heavy: bool = False,
    preferred_provider: Optional[str] = None,
    preferred_model: Optional[str] = None,
    audit_context: Optional[Dict] = None,
) -> AsyncIterator[str]

# ③ JSON 生成
async def generate_json(
    self,
    prompt: str,
    schema: Dict,                                   # 期望的 JSON schema 示例
    max_tokens: int = 4000,
    temperature: float = 0.2,                       # JSON 默认更低温度
    timeout: int = 150,                             # JSON 默认更长超时
    use_heavy: bool = False,
    preferred_provider: Optional[str] = None,
    preferred_model: Optional[str] = None,
    audit_context: Optional[Dict] = None,
) -> Dict
```

### 2.3 参数使用指南

| 参数 | 何时使用 | 示例 |
|------|---------|------|
| `use_heavy=True` | 出题、变式、长文本创作 | `generate_content(..., use_heavy=True)` |
| `use_heavy=False` | 评估、分类、摘要、轻量任务 | 默认值，无需显式传 |
| `preferred_provider` | Agent 聊天中用户选择了特定模型 | `preferred_provider="deepseek"` |
| `preferred_model` | 配合 preferred_provider 指定具体模型 | `preferred_model="deepseek-chat"` |
| `timeout` | 任务特别重或特别轻时调整 | 出题: `timeout=180`, 分类: `timeout=30` |
| `temperature` | 需要更多创造力或更确定性时 | 出题: `0.7`, JSON: `0.1` |

<!-- PLACEHOLDER_SECTION_3 -->

---

## 3. 模块详解

### 3.1 `_types.py` — 共享类型

所有模块共用的数据结构定义。新模组如果需要类型提示，从这里导入。

```python
from services.api_hub._types import PoolEntry, ProviderInfo, CallResult, HealthStatus
```

| 类型 | 说明 |
|------|------|
| `PoolEntry` | `Tuple[openai.OpenAI, str, str]` — (客户端, 模型名, 显示名) |
| `ProviderInfo` | Provider 元数据 dataclass: name, client, model, base_url, enabled |
| `CallResult` | 单次调用结果: text, provider, model, elapsed_ms, token 统计 |
| `HealthStatus` | 健康快照: healthy, success_rate, avg_latency_ms, sample_count |

### 3.2 `provider_registry.py` — Provider 注册中心

管理所有 LLM Provider 的注册、查询、启停、密钥轮换。

```python
client = get_ai_client()
registry = client.registry

# 查询
registry.list_names()                    # → ['deepseek', 'gemini', 'siliconflow', ...]
registry.get("deepseek")                 # → ProviderInfo 或 None
registry.is_available("deepseek")        # → True (已注册且 enabled)

# 运行时管理（通常通过 REST API 操作，也可代码直接调用）
registry.enable("deepseek")
registry.disable("deepseek")
registry.update_model("deepseek", "deepseek-reasoner")
registry.update_key("deepseek", "sk-new-key-xxx")

# 非 LLM 服务的凭证获取
creds = registry.get_credential("openviking")
# → {"api_key": "...", "base_url": "...", "model": "..."}
```

**环境变量约定**（每个 Provider 三个变量）:

```env
{PREFIX}_API_KEY=sk-xxx
{PREFIX}_BASE_URL=https://api.xxx.com/v1
{PREFIX}_MODEL=model-name
```

当前已注册的 PREFIX: `DEEPSEEK`, `GEMINI`, `SILICONFLOW`, `OPENROUTER`, `QINGYUN`

**向后兼容别名**:
- `QINGYUN_*` 未配置时自动回退到 `GEMINI_*`
- `FAST_FALLBACK_*` 自动映射为 `openrouter`

### 3.3 `pool_manager.py` — 池路由与动态切换

三个任务池的管理、组合、运行时重配。

```python
client = get_ai_client()
pools = client.pools

# 查询
pools.get_pool("Heavy")                  # → List[PoolEntry]
pools.get_pool("Light")
pools.get_pool("Fast")
pools.get_all_pools()                    # → {"Heavy": [...], "Light": [...], "Fast": [...]}

# 池组合（内部调用，理解即可）
pool, name = pools.compose_pool(
    use_heavy=True,
    preferred_provider="deepseek",
    preferred_model="deepseek-chat",
)
# → pool = [preferred_entry, ...fallback_entries], name = "Preferred(deepseek/deepseek-chat) -> Heavy"

# 运行时重配
pools.add_model("Heavy", "siliconflow", "Qwen/Qwen3-Coder", priority=0)  # 插入到最高优先级
pools.remove_model("Heavy", "siliconflow", "Qwen/Qwen3-Coder")
pools.reconfigure_pool("Heavy", new_entries_list)
```

**池选择逻辑**:

```
用户调用 generate_content(use_heavy=True, preferred_provider="deepseek")
  │
  ├─ preferred_provider 有值且非 "auto"?
  │   ├─ 是 → 构建 [preferred_entry] + fallback_pool（去重）
  │   └─ 否 → 直接使用 Heavy/Light 池
  │
  └─ 池内遍历:
      模型1 (分配 timeout/N) → 失败 → 模型2 → 失败 → ... → 全部失败抛异常
```

**环境变量配置池**:

```env
POOL_HEAVY=gemini:gemini-3.1-pro-preview,openrouter:google/gemini-2.5-pro,deepseek:deepseek-chat
POOL_LIGHT=deepseek:deepseek-chat,gemini:gemini-3.1-flash-lite-preview
POOL_FAST=openrouter:google/gemini-3.1-flash-lite-preview,deepseek:deepseek-chat
```

格式: `provider:model,provider:model,...`（逗号分隔，按优先级排列）

### 3.4 `retry_engine.py` — 重试引擎

核心调用基础设施。新模组**不需要直接调用**，通过 `AIClient` 的三个公开方法间接使用。

关键函数:

| 函数 | 说明 |
|------|------|
| `is_transient_error(exc)` | 判断异常是否可重试 (429/timeout/overload 等) |
| `extract_text_content(response)` | 从 OpenAI 响应中提取文本 |
| `get_client_base_url(client)` | 获取客户端 base_url |
| `call_model_with_audit(...)` | 调用单个模型，最多 2 次重试，带审计日志 |
| `call_pool(pool, pool_name, messages, ...)` | 遍历池，按时间预算均分，逐个尝试 |

**重试策略**:
- 每个模型最多 2 次尝试
- 仅重试瞬态错误 (429, timeout, 502, 503, overload 等)
- 所有重试共享该模型的时间预算
- 重试间隔: attempt 秒 (第1次重试等1秒)

### 3.5 `stream_handler.py` — 流式生成

线程桥接方案: 在后台线程中同步调用 OpenAI stream API，通过 `asyncio.Queue` 桥接到异步生成器。

**fallback 机制**: 池内所有模型流式失败且未输出任何数据 → 自动回退到 `generate_content()` 非流式调用。

### 3.6 `json_handler.py` — JSON 生成管线

```
用户 prompt
  │
  ├─ 第1轮: prompt + "请返回JSON格式" + schema → 解析
  │   ├─ 成功 → 返回
  │   └─ 失败 → 第2轮
  │
  ├─ 第2轮: 加强约束 prompt → 解析
  │   ├─ 成功 → 返回
  │   └─ 失败 → Fast 兜底（仅 use_heavy=True 时）
  │
  └─ Fast 兜底: 用 Fast 池重新生成 → 解析
      ├─ 成功 → 返回
      └─ 失败 → 抛异常
```

**JSON 解析修复链**:
1. 直接 `json.loads()`
2. 去除 markdown 代码块后重试
3. 提取首尾 `{...}` 子串重试
4. 交给 Light 池做 LLM 修复

### 3.7 `health_monitor.py` — 健康监控

滑动窗口 (默认 120 秒) 内统计每个 Provider 的成功率。

```python
client = get_ai_client()

# 查询健康状态
status = client.health.get_status("deepseek")
# → HealthStatus(provider='deepseek', healthy=True, success_rate=0.95, avg_latency_ms=230, sample_count=20)

client.health.get_all_status()
# → {"deepseek": HealthStatus(...), "gemini": HealthStatus(...), ...}
```

**自动集成**: 每次 `call_pool()` / `generate_content_stream()` 调用后自动记录成功/失败，无需手动操作。

**健康判定**: 窗口内失败次数 >= `failure_threshold` (默认 5) → 标记为不健康。

### 3.8 `cost_tracker.py` — 成本追踪

```python
client = get_ai_client()
tracker = client.cost_tracker

# 计算单次调用成本
cost = tracker.calculate_cost("deepseek", "deepseek-chat", prompt_tokens=1000, completion_tokens=500)
# → 0.0028 (USD)

# 查询用量汇总
tracker.get_summary(period="24h", group_by="provider")
# → {"deepseek": {"calls": 150, "total_tokens": 50000, "total_cost": 0.12, ...}, ...}

tracker.get_daily_cost()       # → 0.35 (USD)

# 设置预算告警
tracker.set_budget_alert(daily_limit_usd=5.0)

# 更新价格
tracker.update_price("deepseek", "deepseek-chat", input_per_1k=0.0014, output_per_1k=0.0028)

# 查看价格表
tracker.get_prices()
# → {"deepseek/deepseek-chat": {"input": 0.0014, "output": 0.0028}, ...}
```

**内置价格表** (可通过 REST API 或代码更新):

| Provider/Model | Input $/1K | Output $/1K |
|----------------|-----------|------------|
| deepseek/deepseek-chat | 0.0014 | 0.0028 |
| deepseek/deepseek-reasoner | 0.0055 | 0.0219 |
| gemini/gemini-3-flash-preview | 0.00 | 0.00 |
| gemini/gemini-3.1-pro-preview | 0.00125 | 0.005 |

### 3.9 `models.py` — 数据库模型

三张表，挂在 `RuntimeBase` 下 (存储在 `data/learning_runtime.db`):

| 表名 | 用途 | 关键字段 |
|------|------|---------|
| `api_hub_usage` | 每次调用的结构化记录 | provider, model, pool_name, status, tokens, cost_usd, elapsed_ms, caller |
| `api_hub_health_log` | Provider 健康快照 (定期) | provider, status, success_rate, avg_latency_ms |
| `api_hub_price` | 自定义价格配置 | provider, model, input_per_1k, output_per_1k |

**与 `llm_audit.jsonl` 的关系**: `api_hub_usage` 是并行的结构化存储，优化了查询和仪表盘展示。原有的 JSONL 审计日志继续保留，不受影响。

<!-- PLACEHOLDER_SECTION_4 -->

---

## 4. 管理 REST API

访问仪表盘页面: `http://localhost:8000/api-hub`

### 4.1 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/hub/providers` | 列出所有 Provider（含健康状态、启用标志） |
| `PUT` | `/api/hub/providers/{name}` | 更新 Provider（启停、换模型） |
| `POST` | `/api/hub/providers/{name}/test` | 测试 Provider 连通性 |
| `GET` | `/api/hub/pools` | 列出所有池配置 |
| `PUT` | `/api/hub/pools/{name}` | 运行时重配池 |
| `GET` | `/api/hub/usage/summary` | 用量统计（按 provider/pool/时间段） |
| `GET` | `/api/hub/usage/costs` | 成本汇总（日/周/月） |
| `GET` | `/api/hub/usage/timeline` | 时间序列数据（用于图表） |
| `GET` | `/api/hub/health` | 所有 Provider 健康状态 |
| `GET` | `/api/hub/prices` | 当前价格表 |
| `PUT` | `/api/hub/prices/{provider}/{model}` | 更新某个模型的价格 |

### 4.2 常用操作示例

**禁用一个 Provider**:
```bash
curl -X PUT http://localhost:8000/api/hub/providers/deepseek \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

**测试 Provider 连通性**:
```bash
curl -X POST http://localhost:8000/api/hub/providers/deepseek/test
# → {"provider":"deepseek","success":true,"latency_ms":230}
```

**重配 Heavy 池**:
```bash
curl -X PUT http://localhost:8000/api/hub/pools/Heavy \
  -H "Content-Type: application/json" \
  -d '{"entries": [
    {"provider": "gemini", "model": "gemini-3.1-pro-preview"},
    {"provider": "deepseek", "model": "deepseek-chat"}
  ]}'
```

**查看 24 小时用量**:
```bash
curl http://localhost:8000/api/hub/usage/summary?period=24h&group_by=provider
```

**查看成本**:
```bash
curl http://localhost:8000/api/hub/usage/costs
# → {"daily_usd": 0.35, "weekly_usd": 2.10, "monthly_usd": 8.40}
```

### 4.3 仪表盘功能

访问 `/api-hub` 页面，包含:

1. **Provider 卡片**: 名称、状态指示灯、模型、启停开关、连通性测试按钮
2. **池配置表**: 每个池的模型列表和优先级顺序
3. **成本摘要**: 今日 / 本周 / 本月的 USD 花费
4. **用量图表**: 按 Provider 分组的 Token 消耗和成本柱状图 (Chart.js)
5. **健康条**: 每个 Provider 的成功率进度条
6. **价格表**: 当前所有模型的输入/输出单价

---

## 5. 新模组接入指南

### 5.1 标准接入模式

```python
# my_new_service.py

from services.ai_client import get_ai_client

class MyNewService:
    def __init__(self):
        self._ai = get_ai_client()

    async def do_something(self, user_input: str) -> str:
        return await self._ai.generate_content(
            prompt=f"请处理以下内容：{user_input}",
            use_heavy=False,       # 轻量任务用 Light 池
            timeout=60,            # 根据任务复杂度调整
        )

    async def do_heavy_task(self, user_input: str) -> dict:
        return await self._ai.generate_json(
            prompt=f"请生成结构化数据：{user_input}",
            schema={"field1": "", "field2": []},
            use_heavy=True,        # 重量任务用 Heavy 池
            timeout=180,
        )
```

### 5.2 Agent 聊天场景（指定 Provider）

```python
# 用户在前端选择了特定模型
result = await client.generate_content(
    prompt=user_message,
    preferred_provider=user_selected_provider,   # "deepseek" / "gemini" / ...
    preferred_model=user_selected_model,         # "deepseek-chat" / ...
)
# 路由逻辑: [用户指定模型] → [对应池的 fallback 链]（去重）
```

### 5.3 流式 SSE 场景

```python
from fastapi.responses import StreamingResponse

@router.post("/api/my-stream")
async def my_stream_endpoint(request: MyRequest):
    client = get_ai_client()

    async def event_generator():
        async for chunk in client.generate_content_stream(
            prompt=request.prompt,
            use_heavy=True,
        ):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### 5.4 直接访问底层组件（高级用法）

```python
client = get_ai_client()

# 访问 Provider 注册中心
client.registry.list_names()
client.registry.get("deepseek")
client.registry.is_available("deepseek")

# 访问池管理器
client.pools.get_pool("Heavy")
client.pools.compose_pool(use_heavy=True, preferred_provider="gemini")

# 访问健康监控
client.health.get_status("deepseek")
client.health.get_all_status()

# 访问成本追踪
client.cost_tracker.get_daily_cost()
client.cost_tracker.get_summary(period="7d")

# 向后兼容属性（旧代码可能用到）
client.ds_client       # deepseek 的 OpenAI 客户端
client.ds_model        # deepseek 的模型名
client.gm_client       # gemini 的 OpenAI 客户端
client.gm_model        # gemini 的模型名
client._providers      # Dict[name, (client, model)] — 兼容旧格式
client._heavy_pool     # Heavy 池 PoolEntry 列表
client._light_pool     # Light 池 PoolEntry 列表
client._fast_pool      # Fast 池 PoolEntry 列表
```

### 5.5 非 LLM 服务的凭证获取

```python
client = get_ai_client()

# 统一获取任意服务的凭证（读取 {SERVICE}_API_KEY / _BASE_URL / _MODEL 环境变量）
creds = client.registry.get_credential("openviking")
# → {"api_key": "...", "base_url": "...", "model": "..."}

creds = client.registry.get_credential("siliconflow")
# → {"api_key": "...", "base_url": "...", "model": "..."}
```

---

## 6. 文件变更清单

### 6.1 新增文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `services/api_hub/__init__.py` | 10 | 包入口，re-export AIClient / get_ai_client |
| `services/api_hub/_types.py` | 66 | 共享类型: PoolEntry, ProviderInfo, CallResult, HealthStatus |
| `services/api_hub/provider_registry.py` | 154 | ProviderRegistry 类: 注册、查询、启停、密钥轮换 |
| `services/api_hub/retry_engine.py` | 280 | 重试逻辑、瞬态错误检测、池遍历、审计日志 |
| `services/api_hub/pool_manager.py` | 226 | PoolManager 类: 池解析、默认池、组合、运行时重配 |
| `services/api_hub/stream_handler.py` | 199 | 流式生成: 线程桥接 SSE、池级 fallback |
| `services/api_hub/json_handler.py` | 307 | JSON 生成管线: 两轮重试、修复链、Fast 兜底 |
| `services/api_hub/facade.py` | 240 | AIClient 薄门面 + get_ai_client() 线程安全单例 |
| `services/api_hub/health_monitor.py` | 88 | HealthMonitor: 滑动窗口健康追踪 |
| `services/api_hub/cost_tracker.py` | 231 | CostTracker: 成本计算、DB 持久化、预算告警 |
| `services/api_hub/models.py` | 66 | SQLAlchemy 模型: api_hub_usage, health_log, price |
| `routers/api_hub.py` | 264 | 管理 REST API: 12 个端点 |
| `templates/api_hub.html` | 171 | Jinja2 仪表盘页面 |

### 6.2 修改文件

| 文件 | 变更内容 |
|------|---------|
| `services/ai_client.py` | 1086 行 → 4 行向后兼容垫片 |
| `main.py` | +3 行: 挂载 api_hub_router, 添加 `/api-hub` 页面路由, 启动时创建 API Hub 表 |

### 6.3 未修改文件

| 文件 | 说明 |
|------|------|
| `services/__init__.py` | 无变更，`from services.ai_client import ...` 通过垫片正常工作 |
| `services/llm_audit.py` | 无变更，继续独立运行 |
| `database/domains.py` | 无变更，新表通过 `RuntimeBase.metadata.create_all()` 自动创建 |
| `.env` / `.env.example` | 无变更，环境变量格式不变 |
| 所有 15+ 个消费者 service | 零变更，导入路径和 API 签名完全兼容 |

---

## 7. 数据库变更

新增 3 张表到 `data/learning_runtime.db` (RuntimeBase):

```sql
-- 每次调用的结构化记录
CREATE TABLE api_hub_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    logical_call_id TEXT DEFAULT '',
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    pool_name       TEXT DEFAULT '',
    status          TEXT NOT NULL,          -- 'success' | 'error'
    prompt_tokens   INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens    INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0,
    elapsed_ms      INTEGER DEFAULT 0,
    caller          TEXT DEFAULT '',
    request_path    TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_usage_created ON api_hub_usage(created_at);
CREATE INDEX ix_usage_provider ON api_hub_usage(provider);

-- Provider 健康快照
CREATE TABLE api_hub_health_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider        TEXT NOT NULL,
    status          TEXT NOT NULL,          -- 'healthy' | 'degraded' | 'down'
    success_rate    REAL,
    avg_latency_ms  INTEGER,
    sample_count    INTEGER,
    checked_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 价格配置
CREATE TABLE api_hub_price (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    input_per_1k    REAL NOT NULL,
    output_per_1k   REAL NOT NULL,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, model)
);
```

表在应用启动时自动创建 (`main.py` 的 `startup` 事件中)。

---

## 8. 调用流程图

### 8.1 文本生成完整流程

```
consumer service
  │
  └─ get_ai_client().generate_content(prompt, use_heavy=True, preferred_provider="deepseek")
       │
       ├─ facade.py: compose_pool()
       │   └─ pool_manager.py: [deepseek/deepseek-chat] + [Heavy池去重fallback]
       │
       ├─ facade.py: create_llm_call_context() — 审计上下文
       │
       └─ retry_engine.call_pool(pool, "Preferred(deepseek/deepseek-chat) -> Heavy", ...)
            │
            ├─ 模型1: call_model_with_audit()
            │   ├─ 尝试1: client.chat.completions.create() → 成功 → 返回
            │   │   └─ log_llm_attempt() → llm_audit.jsonl
            │   │   └─ health_callback() → health_monitor 记录
            │   └─ 尝试1失败 + 瞬态错误 → 等1秒 → 尝试2
            │
            ├─ 模型1全部失败 → 切换模型2
            │
            └─ 模型2: call_model_with_audit() → 成功 → 返回
```

### 8.2 JSON 生成完整流程

```
consumer service
  │
  └─ get_ai_client().generate_json(prompt, schema, use_heavy=True)
       │
       └─ json_handler.generate_json()
            │
            ├─ 第1轮: generate_content(json_prompt) → parse_json_with_repair()
            │   ├─ json.loads() 成功 → 返回
            │   ├─ 提取 {...} 子串 → 成功 → 返回
            │   └─ LLM 修复 (Light池) → 成功 → 返回
            │
            ├─ 第1轮失败 → 第2轮: generate_content(retry_prompt) → parse_json_with_repair()
            │
            └─ 第2轮失败 + use_heavy=True → Fast池兜底:
                call_pool(fast_pool, fast_prompt) → parse_json_with_repair()
```

---

## 9. 开发规范

### 9.1 禁止事项

1. **禁止自行创建 `openai.OpenAI` 客户端** — 所有 LLM 调用必须通过 `get_ai_client()`
2. **禁止直接读取 `*_API_KEY` 环境变量** — 使用 `registry.get_credential()` 或 `registry.get()`
3. **禁止在 service 中硬编码模型名** — 使用 `use_heavy` / `preferred_provider` 参数
4. **禁止绕过池路由直接调用** — 池提供了 fallback、重试、审计、健康追踪

### 9.2 推荐实践

1. 轻量任务 (评估/分类/摘要) → `use_heavy=False` (默认)
2. 重量任务 (出题/变式/长文本) → `use_heavy=True`
3. 需要 JSON 输出 → 用 `generate_json()`，不要自己 parse
4. 需要流式输出 → 用 `generate_content_stream()`
5. 超时根据任务调整: 分类 30s, 普通生成 120s, 出题 180s, JSON 150s
6. 不要缓存 `get_ai_client()` 的返回值到模块级变量 — 它本身就是单例

### 9.3 新增 Provider 的步骤

1. 在 `.env` 中添加三个变量:
   ```env
   NEWPROVIDER_API_KEY=sk-xxx
   NEWPROVIDER_BASE_URL=https://api.newprovider.com/v1
   NEWPROVIDER_MODEL=model-name
   ```

2. 在 `provider_registry.py` 的 `DEFAULT_PROVIDER_DEFS` 中添加:
   ```python
   "newprovider": ("NEWPROVIDER", "https://api.newprovider.com/v1"),
   ```

3. 在 `.env` 的池配置中引用:
   ```env
   POOL_HEAVY=gemini:...,newprovider:model-name,deepseek:...
   ```

4. 在 `cost_tracker.py` 的 `DEFAULT_PRICES` 中添加价格:
   ```python
   ("newprovider", "model-name"): {"input": 0.001, "output": 0.002},
   ```

5. 重启应用，访问 `/api-hub` 验证新 Provider 出现在仪表盘中。

---

## 10. 故障排查

| 症状 | 排查步骤 |
|------|---------|
| "Heavy pool is empty" | 检查 `GEMINI_API_KEY` 或 `POOL_HEAVY` 环境变量 |
| "Light pool is empty" | 检查 `DEEPSEEK_API_KEY` 或 `POOL_LIGHT` 环境变量 |
| "Unregistered provider: xxx" | `preferred_provider` 传了未注册的名称，检查 `.env` |
| 所有模型超时 | 增大 `timeout` 参数，或检查网络/Provider 状态 |
| JSON 解析反复失败 | 检查 prompt 是否清晰要求 JSON，schema 是否合理 |
| 仪表盘无数据 | 确认 `data/learning_runtime.db` 存在且 `api_hub_usage` 表已创建 |
| 成本显示为 0 | 检查 `DEFAULT_PRICES` 中是否有对应 provider/model 的价格 |

**查看运行时状态**:
```bash
# 检查 Provider 状态
curl http://localhost:8000/api/hub/providers

# 检查健康状态
curl http://localhost:8000/api/hub/health

# 测试特定 Provider
curl -X POST http://localhost:8000/api/hub/providers/deepseek/test
```


