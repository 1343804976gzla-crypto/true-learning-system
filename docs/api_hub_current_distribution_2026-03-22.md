# API Hub 当前分发与接口模型可调用情况（2026-03-22）

## 1. 查询口径

- 查询时间：2026-03-22 12:23:42 +08:00
- 统计窗口：近 24 小时
- 查询来源：
  - 运行配置：`.env`
  - API Hub 实现：`services/api_hub/*.py`
  - 业务调用点：`services/*.py`、`routers/*.py`
  - 运行库：`data/learning_runtime.db`（`api_hub_usage`、`api_hub_health_log`、`api_hub_price`）
  - 审计日志：`data/llm_audit.jsonl`
  - Agent 会话配置：`data/agent.db`

## 2. 当前 Provider 注册情况

| Provider | Provider 默认模型 | 是否启用 | 当前池内是否使用 | 备注 |
| --- | --- | --- | --- | --- |
| `deepseek` | `deepseek-chat` | 是 | `Light`、`Fast` | 可直接偏好调用，也在轻量/快速链路里做兜底 |
| `gemini` | `gemini-3.1-pro-preview` | 是 | `Heavy`、`Light`、`Vision` | 当前主承载 Provider；`Light` 池里实际用的是 `gemini-3.1-flash-lite-preview` |
| `siliconflow` | 无默认模型 | 是 | `Heavy`、`Light`、`Vision` | 只能依赖池里显式列出的模型，或直连时显式传 `preferred_model` |
| `openrouter` | 无默认模型 | 是 | `Heavy`、`Fast`、`Vision` | 只能依赖池里显式列出的模型，或直连时显式传 `preferred_model` |
| `qingyun` | `claude-sonnet-4-6` | 是 | 否 | 当前未进入任何标准池，只能走“直指定 preferred_provider/model” |

补充说明：

- `STRICT_HEAVY_MODEL=true`，意味着如果只走默认 Heavy 构造逻辑，DeepSeek 不会被放进 Heavy。
- 但当前 `.env` 已显式配置 `POOL_HEAVY`，所以 Heavy 实际以环境变量为准。
- `qingyun` 会在未配置 `QINGYUN_API_KEY` 时回退复用 `GEMINI_API_KEY` 注册；当前运行态已注册成功。
- `siliconflow` 和 `openrouter` 的 provider 默认模型为空，所以它们不适合只传 provider、不传 model 的直连偏好调用。

## 3. 当前模型池

| 池名 | 当前模型顺序 |
| --- | --- |
| `Heavy` | `gemini/gemini-3.1-pro-preview` → `openrouter/google/gemini-2.5-pro` → `siliconflow/Pro/zai-org/GLM-5` → `siliconflow/Pro/moonshotai/Kimi-K2.5` |
| `Light` | `gemini/gemini-3.1-flash-lite-preview` → `deepseek/deepseek-chat` → `siliconflow/Pro/moonshotai/Kimi-K2.5` |
| `Fast` | `openrouter/google/gemini-3.1-flash-lite-preview` → `openrouter/google/gemini-3-flash-preview` → `deepseek/deepseek-chat` |
| `Vision` | 当前未单独配置 `POOL_VISION`，因此与 `Heavy` 相同 |

## 4. API Hub 当前路由规则

### 4.1 文本调用

- `generate_content(use_heavy=False)`：走 `Light`
- `generate_content(use_heavy=True)`：走 `Heavy`
- `generate_content_stream(...)`：先走对应池的流式调用；如果整个流式过程一个字符都没产出，再退回同池的非流式 `generate_content()`；不会改去 `Fast`

### 4.2 JSON 调用

- `generate_json(use_heavy=False)`：
  - 主调用走 `Light`
  - 如果返回文本不是合法 JSON，会再走一次 `Light` 做 `json_repair`
- `generate_json(use_heavy=True)`：
  - 主调用走 `Heavy`
  - 每轮 JSON 解析失败后，会走一次 `Light` 做 `json_repair`
  - 两轮 Heavy JSON 都失败后，还会启动 `Fast` 做 `JSON 兜底`

### 4.3 直指定模型

- 如果业务层传了 `preferred_provider`/`preferred_model`，API Hub 会先把这个模型插到最前面。
- 之后仍然会继续接原来的池作为 fallback。
- 这意味着：
  - Agent 之类的接口可以先打指定模型，再回退到 `Light`
  - `qingyun/claude-sonnet-4-6` 这种未进池模型，当前也能被“直指定”调用
  - `openrouter`、`siliconflow` 因为 provider 默认模型为空，直指定时必须连 `preferred_model` 一起传

## 5. 近 24 小时真实分发情况

### 5.1 按池统计

按 `api_hub_usage` 的 provider 聚合口径，近 24 小时共记录 200 次调用。

| 池 | 调用次数 | 占比 | 说明 |
| --- | ---: | ---: | --- |
| `Light` | 146 | 73.0% | 当前绝大多数流量都落在轻量链路 |
| `Heavy` | 54 | 27.0% | 主要来自历史复习、整卷生成、变式题生成 |

### 5.2 按 Provider 统计

| Provider | 调用次数 | 占比 | 观察 |
| --- | ---: | ---: | --- |
| `gemini` | 185 | 92.5% | 当前绝对主承载 |
| `openrouter` | 8 | 4.0% | 只在 Heavy/Fast fallback 里出现 |
| `siliconflow` | 6 | 3.0% | 只在 Heavy fallback 里出现 |
| `deepseek` | 1 | 0.5% | 当前仅出现 1 次实际成功兜底 |

### 5.3 按模型统计

| 模型 | 24h 次数 | 成功/失败 | 结论 |
| --- | ---: | --- | --- |
| `gemini/gemini-3.1-flash-lite-preview` | 145 | 144 成功 / 1 失败 | 当前 Light 主模型 |
| `gemini/gemini-3.1-pro-preview` | 40 | 32 成功 / 8 失败 | 当前 Heavy 主模型 |
| `openrouter/google/gemini-2.5-pro` | 8 | 0 成功 / 8 失败 | 当前配置存在，但近 24h 实际不可用 |
| `siliconflow/Pro/zai-org/GLM-5` | 3 | 0 成功 / 3 失败 | 当前只在深层 fallback 出现，且超时 |
| `siliconflow/Pro/moonshotai/Kimi-K2.5` | 3 | 0 成功 / 3 失败 | 当前只在深层 fallback 出现，且超时 |
| `deepseek/deepseek-chat` | 1 | 1 成功 / 0 失败 | 近 24h 有 1 次真实成功兜底 |

### 5.4 按实际路径统计

下表是运行时记录到的具体路径，保留了真实 ID；对应的接口模板见第 6 节。

| 实际路径 | 24h 次数 | 主要落点 |
| --- | ---: | --- |
| `/api/quiz/start/internal_ch01` | 52 | 51 次 `gemini/gemini-3.1-flash-lite-preview`，1 次 `deepseek/deepseek-chat` |
| `/api/history/review-pdf` | 41 | 30 次 `gemini flash-lite`，其余分布到 `gemini pro` / `openrouter` / `siliconflow` |
| `/api/upload` | 24 | 24 次 `gemini/gemini-3.1-flash-lite-preview` |
| `/api/history/review-task/1` | 21 | 14 次 `gemini flash-lite`，其余进入 Heavy fallback |
| `/api/quiz/batch/generate/0` | 14 | 14 次 `gemini/gemini-3.1-pro-preview` |
| `/api/history/review-task/2` | 14 | `gemini pro` / `gemini flash-lite` / `openrouter` 混合 |
| `/api/history/task/1/regenerate-questions` | 13 | 13 次 `gemini/gemini-3.1-flash-lite-preview` |
| `/api/challenge/variant` | 9 | 8 次 `gemini/gemini-3.1-pro-preview`，1 次 `gemini flash-lite` |

## 6. 接口模型允许调用情况

这里的“允许调用”指当前代码路径下，API Hub 实际可能触达的模型范围，不只看主池，还把 JSON 修复和 fallback 算进去。

| 接口模板 | 主要代码入口 | 当前调用模式 | 当前允许触达的模型 |
| --- | --- | --- | --- |
| `/api/quiz/generate/{concept_id}`、`/api/quiz/start/{chapter_id}`、`/api/quiz/submit` | `services.quiz_service` | `generate_json(use_heavy=False)` | `Light`：`gemini/gemini-3.1-flash-lite-preview`、`deepseek/deepseek-chat`、`siliconflow/Pro/moonshotai/Kimi-K2.5` |
| `/api/upload` | `services.content_parser`、`services.content_parser_v2` | `generate_json(use_heavy=False)` | `Light`：`gemini flash-lite`、`deepseek chat`、`siliconflow Kimi-K2.5` |
| `/api/upload/knowledge-preview` | `services.knowledge_upload_service._extract_structured_knowledge` | `generate_json(use_heavy=False)`，失败时再回退本地规则解析 | `Light`：`gemini flash-lite`、`deepseek chat`、`siliconflow Kimi-K2.5` |
| `/api/upload` 当 `source_mode=image_ocr` | `KnowledgeUploadService.extract_text_from_image` | 直接轮询 `Vision` | `Vision`：`gemini/gemini-3.1-pro-preview`、`openrouter/google/gemini-2.5-pro`、`siliconflow/Pro/zai-org/GLM-5`、`siliconflow/Pro/moonshotai/Kimi-K2.5` |
| `/api/upload/knowledge-points/{note_id}` | `KnowledgeUploadService._merge_note_content` | `generate_json(use_heavy=False)` | `Light`：`gemini flash-lite`、`deepseek chat`、`siliconflow Kimi-K2.5` |
| `/api/upload/knowledge-points/{note_id}/practice` | `KnowledgeUploadService.start_practice` → `QuizService.generate_quiz` | `generate_json(use_heavy=False)` | `Light`：`gemini flash-lite`、`deepseek chat`、`siliconflow Kimi-K2.5` |
| `/api/history/review-task/{task_id}`、`/api/history/task/{task_id}/regenerate-questions`、`/api/history/review-pdf` | `services.chapter_review_service` | 混合使用 Heavy JSON 与 Light JSON | 可能触达 `Heavy` + `Light` + `Fast`：`Heavy` 为 `gemini pro`、`openrouter gemini-2.5-pro`、`siliconflow GLM-5`、`siliconflow Kimi-K2.5`；`Light` 为 `gemini flash-lite`、`deepseek chat`、`siliconflow Kimi-K2.5`；`Fast` 为 `openrouter gemini-3.1-flash-lite-preview`、`openrouter/google/gemini-3-flash-preview`、`deepseek/deepseek-chat` |
| `/api/history/review-task/{task_id}/grade` | `services.chapter_review_service.grade_task_answers` | `generate_json(use_heavy=False)` | `Light`：`gemini flash-lite`、`deepseek chat`、`siliconflow Kimi-K2.5` |
| `/api/quiz/batch/generate/{chapter_id}`、`/api/quiz/batch/generate-variations`、`/api/quiz/variations`、`/api/challenge/variant`、`/api/wrong-answers/{wrong_id}/variant/generate` | `services.quiz_service_v2`、`services.variant_surgery_service.generate_variant` | `generate_json(use_heavy=True)` | 可能触达 `Heavy` + `Light` + `Fast`；范围同上 |
| `/api/challenge/evaluate-rationale`、`/api/wrong-answers/{wrong_id}/variant/judge` | `services.variant_surgery_service.evaluate_rationale` | `generate_json(use_heavy=False)` | `Light`：`gemini flash-lite`、`deepseek chat`、`siliconflow Kimi-K2.5` |
| `/api/fusion/{id}/socratic-hint`、`/api/fusion/create`、`/api/fusion/{id}/judge`、`/api/fusion/{id}/diagnose` | `services.fusion_service` | `generate_content(use_heavy=True)` | 仅 `Heavy`：`gemini pro`、`openrouter gemini-2.5-pro`、`siliconflow GLM-5`、`siliconflow Kimi-K2.5` |
| `/api/feynman/start/{concept_id}` | `services.feynman_service.start_session` | `generate_content(use_heavy=False)` | 仅 `Light`：`gemini flash-lite`、`deepseek chat`、`siliconflow Kimi-K2.5` |
| `/api/feynman/respond/{session_id}` | `services.feynman_service.process_response` | `generate_json(use_heavy=False)` | `Light`：`gemini flash-lite`、`deepseek chat`、`siliconflow Kimi-K2.5` |
| `/api/agent/chat`、`/api/agent/chat/stream` | `services.agent_runtime` | 先走 `preferred_provider/model`，再回退 `Light`；规划阶段是 `generate_json(use_heavy=False)`，正文阶段是 `generate_content(_stream)` | 当前允许的“直指定”模型取决于 session 存的 provider/model；当前已注册、可直接偏好的默认模型是 `deepseek/deepseek-chat`、`gemini/gemini-3.1-pro-preview`、`qingyun/claude-sonnet-4-6`；如果直指定的是 `openrouter` 或 `siliconflow`，必须同时带 model；所有 Agent 请求最终仍可回退到 `Light`：`gemini flash-lite`、`deepseek chat`、`siliconflow Kimi-K2.5` |

## 7. Agent 接口的当前实际模型选择情况

`data/agent.db` 里当前 session 配置分布如下：

| Session 配置 | 数量 | 说明 |
| --- | ---: | --- |
| `deepseek / deepseek-chat` | 28 | 当前主配置 |
| `auto / auto` | 8 | 运行时会解析成 `deepseek / deepseek-chat` |
| `qingyun / claude-sonnet-4-6` | 4 | 会先直连 `qingyun/claude-sonnet-4-6`，再回退 `Light` |

这意味着：

- Agent 当前是唯一明确支持“直指定未进池模型”的业务接口。
- `qingyun/claude-sonnet-4-6` 虽然没进任何标准池，但 Agent 会话里已经在实际使用这个配置。

## 8. 当前可用性结论

### 8.1 当前“理论允许”和“实际有效”不是一回事

- `Heavy` 理论上有 4 个模型，但近 24 小时真正稳定承载的是 `gemini/gemini-3.1-pro-preview`
- `Light` 理论上有 3 个模型，但近 24 小时真正主承载的是 `gemini/gemini-3.1-flash-lite-preview`
- `Fast` 理论上可用 3 个模型，但当前 OpenRouter 额度问题会直接影响前两跳

### 8.2 近 24 小时的关键问题

- `openrouter/google/gemini-2.5-pro`：最近错误为 `402 Insufficient credits`
- `gemini/gemini-3.1-pro-preview`：最近错误为超时，最新样本出现在 `/api/history/review-task/2`
- `siliconflow/Pro/zai-org/GLM-5`：最近错误为约 40 秒超时
- `siliconflow/Pro/moonshotai/Kimi-K2.5`：最近错误为约 39 秒超时
- `qingyun/claude-sonnet-4-6`：最近样本报 `503 - No available channels`

### 8.3 当前最新健康日志

以 `api_hub_health_log` 的最新记录看：

- `openrouter`：`down`
- `gemini`：`degraded`
- 最近一条 `gemini` 样本：成功率约 `0.5`，平均延迟约 `7387ms`

### 8.4 现阶段的实际结论

- 当前系统已经把大多数真实流量压在 Gemini 上了。
- `Light` 链路现在基本等价于“Gemini Flash Lite 为主，DeepSeek 极少量兜底”。
- `Heavy` 链路现在基本等价于“Gemini Pro 为主；后面的 OpenRouter 和 SiliconFlow 在近 24 小时里没有提供有效承载能力”。
- 如果要提升 Heavy 链路稳定性，优先级应该是：
  1. 恢复 OpenRouter 额度
  2. 排查 SiliconFlow 超时
  3. 再评估是否需要把 `qingyun/claude-sonnet-4-6` 纳入标准池

