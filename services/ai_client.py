"""
AI客户端模块 - 多模型池化路由
按任务类型分配模型池，每个池有有序 fallback 链：
  Heavy池（出题/变式）  → 需要创造力 + 长输出
  Light池（评估/分类）  → 需要速度
  Fast池 （JSON修复/兜底）→ 最快响应

池内容错：按时间预算均分，尝试第1个 → 超时? → 切换第2个 → ... → 全部失败才抛异常
每个模型最多2次重试，所有模型共享池级时间预算
"""

import os
import json
import asyncio
import time as _time
import logging
import threading
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from pathlib import Path
import openai
from dotenv import load_dotenv
from services.llm_audit import (
    create_llm_call_context,
    derive_llm_call_context,
    extract_response_usage,
    log_llm_attempt,
)

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Type alias: (client, model, display_name)
PoolEntry = Tuple[openai.OpenAI, str, str]


class AIClient:
    """AI客户端 - 多模型池化路由"""

    # Provider 定义: name -> (env_prefix, default_base_url)
    _PROVIDER_DEFS = {
        "deepseek":    ("DEEPSEEK",    "https://api.deepseek.com/v1"),
        "gemini":      ("GEMINI",      "https://api.qingyuntop.top/v1"),
        "siliconflow": ("SILICONFLOW", "https://api.siliconflow.cn/v1"),
        "openrouter":  ("OPENROUTER",  "https://openrouter.ai/api/v1"),
        "qingyun":     ("QINGYUN",     "https://api.qingyuntop.top/v1"),
    }

    def __init__(self):
        self.strict_heavy = (
            os.getenv("STRICT_HEAVY_MODEL") or ""
        ).strip().lower() in ("1", "true", "yes")

        # 1. 注册所有可用 Provider
        self._providers: Dict[str, Tuple[openai.OpenAI, str]] = {}
        self._register_providers()

        # 2. 解析三个任务池
        self._heavy_pool: List[PoolEntry] = self._parse_pool(
            "POOL_HEAVY", self._default_heavy_pool()
        )
        self._light_pool: List[PoolEntry] = self._parse_pool(
            "POOL_LIGHT", self._default_light_pool()
        )
        self._fast_pool: List[PoolEntry] = self._parse_pool(
            "POOL_FAST", self._default_fast_pool()
        )

        # 3. 向后兼容属性（供外部直接引用 ds_client / gm_client 的代码）
        ds = self._providers.get("deepseek")
        self.ds_client = ds[0] if ds else None
        self.ds_model = ds[1] if ds else "deepseek-chat"
        gm = self._providers.get("gemini")
        self.gm_client = gm[0] if gm else None
        self.gm_model = gm[1] if gm else "gemini-3-flash-preview"

        self._log_init()

    # ──────────────────── Provider 注册 ────────────────────

    def _register_providers(self):
        """扫描环境变量，注册所有已配置的 Provider。"""
        for name, (prefix, default_url) in self._PROVIDER_DEFS.items():
            api_key = (os.getenv(f"{prefix}_API_KEY") or "").strip()
            base_url = (os.getenv(f"{prefix}_BASE_URL") or default_url).strip()
            model = (os.getenv(f"{prefix}_MODEL") or "").strip()

            if api_key:
                client = openai.OpenAI(api_key=api_key, base_url=base_url)
                self._providers[name] = (client, model)

        # 若未显式配置 QINGYUN_*，则复用现有 GEMINI_* 的青云中转配置
        if "qingyun" not in self._providers:
            qingyun_key = (os.getenv("QINGYUN_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
            qingyun_base_url = (
                os.getenv("QINGYUN_BASE_URL")
                or os.getenv("GEMINI_BASE_URL")
                or self._PROVIDER_DEFS["qingyun"][1]
            ).strip()
            qingyun_model = (os.getenv("QINGYUN_MODEL") or "").strip()
            if qingyun_key:
                client = openai.OpenAI(api_key=qingyun_key, base_url=qingyun_base_url)
                self._providers["qingyun"] = (client, qingyun_model)

        # 向后兼容: FAST_FALLBACK_* 作为 openrouter 的别名
        if "openrouter" not in self._providers:
            fast_key = (os.getenv("FAST_FALLBACK_API_KEY") or "").strip()
            fast_url = (
                os.getenv("FAST_FALLBACK_BASE_URL") or ""
            ).strip()
            fast_model = (os.getenv("FAST_FALLBACK_MODEL") or "").strip()
            if fast_key and fast_url:
                client = openai.OpenAI(api_key=fast_key, base_url=fast_url)
                self._providers["openrouter"] = (client, fast_model)

    # ──────────────────── 池解析 ────────────────────

    def _parse_pool(
        self, env_key: str, default: List[PoolEntry]
    ) -> List[PoolEntry]:
        """
        从 .env 解析池配置。
        格式: provider:model,provider:model,...（逗号分隔，按优先级排列）
        未配置时返回 default。
        """
        raw = (os.getenv(env_key) or "").strip()
        if not raw:
            return default

        pool: List[PoolEntry] = []
        for entry in raw.split(","):
            entry = entry.strip()
            if ":" not in entry:
                print(
                    f"[AIClient] 警告: 池 {env_key} 中的 '{entry}' "
                    f"格式不正确（需要 provider:model），已跳过"
                )
                continue
            provider_name, model = entry.split(":", 1)
            provider_name = provider_name.strip()
            model = model.strip()
            if provider_name in self._providers:
                client = self._providers[provider_name][0]
                display = f"{provider_name}/{model}"
                pool.append((client, model, display))
            else:
                print(
                    f"[AIClient] 警告: 池 {env_key} 引用了未注册的 "
                    f"provider '{provider_name}'，已跳过"
                )

        return pool if pool else default

    def _default_heavy_pool(self) -> List[PoolEntry]:
        """未配置 POOL_HEAVY 时的默认池（向后兼容旧3槽位逻辑）。"""
        pool: List[PoolEntry] = []
        gm = self._providers.get("gemini")
        if gm:
            model = gm[1] or "gemini-3-flash-preview"
            pool.append((gm[0], model, f"gemini/{model}"))
        ds = self._providers.get("deepseek")
        if ds and not self.strict_heavy:
            model = ds[1] or "deepseek-chat"
            pool.append((ds[0], model, f"deepseek/{model}(Heavy回退)"))
        return pool

    def _default_light_pool(self) -> List[PoolEntry]:
        """未配置 POOL_LIGHT 时的默认池。"""
        pool: List[PoolEntry] = []
        ds = self._providers.get("deepseek")
        if ds:
            model = ds[1] or "deepseek-chat"
            pool.append((ds[0], model, f"deepseek/{model}"))
        return pool

    def _default_fast_pool(self) -> List[PoolEntry]:
        """未配置 POOL_FAST 时的默认池。"""
        pool: List[PoolEntry] = []
        orr = self._providers.get("openrouter")
        if orr:
            model = orr[1] or "deepseek-chat"
            pool.append((orr[0], model, f"openrouter/{model}"))
        ds = self._providers.get("deepseek")
        if ds:
            model = ds[1] or "deepseek-chat"
            pool.append((ds[0], model, f"deepseek/{model}(Fast兜底)"))
        return pool

    # ──────────────────── 日志 ────────────────────

    def _log_init(self):
        print("[AIClient] 多模型池化初始化完成")
        providers = list(self._providers.keys())
        print(f"[AIClient] 已注册 Provider ({len(providers)}): {providers}")
        for pool_name, pool in [
            ("Heavy", self._heavy_pool),
            ("Light", self._light_pool),
            ("Fast", self._fast_pool),
        ]:
            names = [e[2] for e in pool]
            print(f"[AIClient] {pool_name}池 ({len(names)}): {names}")

    # ──────────────────── 核心调用 ────────────────────

    def _is_transient_error(self, exc: Exception) -> bool:
        """判断是否为可重试的临时错误（限流/超时/上游拥塞/网络抖动）。"""
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return True
        status = getattr(exc, "status_code", None)
        if status in {408, 409, 429, 500, 502, 503, 504}:
            return True
        msg = str(exc).lower()
        keywords = (
            "429", "rate limit", "timeout", "timed out", "temporarily",
            "overload", "upstream", "connection", "try again",
            "稍后再试", "负载", "超时",
        )
        return any(k in msg for k in keywords)

    def _response_text_content(self, response: Any) -> str:
        try:
            choices = getattr(response, "choices", None) or []
            if not choices:
                return ""
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None) if message is not None else None
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: List[str] = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                    else:
                        text = getattr(item, "text", None)
                    if text:
                        parts.append(str(text))
                return "".join(parts)
        except Exception:
            return ""
        return ""

    def _client_base_url(self, client: Any) -> str | None:
        base_url = getattr(client, "base_url", None)
        return str(base_url) if base_url is not None else None

    async def _call_model_with_retries(
        self,
        client,
        model: str,
        provider_name: str,
        messages: list,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        """调用指定模型，带有限重试（最多2次，仅重试临时错误）。

        timeout: 此模型的总时间预算（秒），所有重试共享此预算。
        """
        max_attempts = 2
        model_deadline = _time.time() + timeout
        last_error: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            remaining = model_deadline - _time.time()
            if remaining < 5:
                print(
                    f"[AIClient] {provider_name} 时间不足"
                    f"({remaining:.0f}s)，停止重试"
                )
                break
            attempt_timeout = max(10, int(remaining))
            try:
                def _call():
                    return client.chat.completions.create(
                        model=model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )

                loop = asyncio.get_event_loop()
                response = await asyncio.wait_for(
                    loop.run_in_executor(None, _call),
                    timeout=attempt_timeout,
                )
                return response.choices[0].message.content
            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"{provider_name}请求超时({attempt_timeout}s)"
                )
            except Exception as e:
                last_error = e

            if (
                attempt < max_attempts
                and last_error
                and self._is_transient_error(last_error)
            ):
                wait_s = attempt  # 1s
                print(
                    f"[AIClient] {provider_name} 临时错误，"
                    f"第{attempt}次重试，{wait_s}s后继续: {last_error}"
                )
                await asyncio.sleep(wait_s)
                continue
            break

        if last_error is None:
            raise RuntimeError(f"{provider_name}调用失败（未知错误）")
        raise last_error

    async def _call_model_with_audit(
        self,
        client,
        model: str,
        provider_name: str,
        messages: list,
        max_tokens: int,
        temperature: float,
        timeout: int,
        pool_name: str,
        pool_index: int,
        pool_size: int,
        audit_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        provider_key = provider_name.split("/", 1)[0]
        max_attempts = 2
        model_deadline = _time.time() + timeout
        last_error: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            remaining = model_deadline - _time.time()
            if remaining < 5:
                print(
                    f"[AIClient] {provider_name} 鏃堕棿涓嶈冻"
                    f"({remaining:.0f}s)锛屽仠姝㈤噸璇?"
                )
                break

            attempt_timeout = max(10, int(remaining))
            started = _time.time()
            try:
                def _call():
                    return client.chat.completions.create(
                        model=model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )

                loop = asyncio.get_event_loop()
                response = await asyncio.wait_for(
                    loop.run_in_executor(None, _call),
                    timeout=attempt_timeout,
                )
                response_text = self._response_text_content(response)
                finish_reason = None
                try:
                    choices = getattr(response, "choices", None) or []
                    if choices:
                        finish_reason = getattr(choices[0], "finish_reason", None)
                except Exception:
                    finish_reason = None
                log_llm_attempt(
                    provider=provider_key,
                    model=model,
                    provider_display=provider_name,
                    base_url=self._client_base_url(client),
                    pool_name=pool_name,
                    pool_index=pool_index,
                    pool_size=pool_size,
                    attempt=attempt,
                    status="success",
                    elapsed_ms=int((_time.time() - started) * 1000),
                    output_chars=len(response_text),
                    finish_reason=str(finish_reason) if finish_reason is not None else None,
                    usage=extract_response_usage(response),
                    response_id=getattr(response, "id", None),
                    audit_context=audit_context,
                )
                return response_text
            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"{provider_name}璇锋眰瓒呮椂({attempt_timeout}s)"
                )
            except Exception as e:
                last_error = e

            log_llm_attempt(
                provider=provider_key,
                model=model,
                provider_display=provider_name,
                base_url=self._client_base_url(client),
                pool_name=pool_name,
                pool_index=pool_index,
                pool_size=pool_size,
                attempt=attempt,
                status="error",
                elapsed_ms=int((_time.time() - started) * 1000),
                output_chars=0,
                error=last_error,
                audit_context=audit_context,
            )

            if (
                attempt < max_attempts
                and last_error
                and self._is_transient_error(last_error)
            ):
                wait_s = attempt
                print(
                    f"[AIClient] {provider_name} 临时错误，"
                    f"第{attempt}次重试，{wait_s}s后继续: {last_error}"
                )
                await asyncio.sleep(wait_s)
                continue
            break

        if last_error is None:
            raise RuntimeError(f"{provider_name}璋冪敤澶辫触锛堟湭鐭ラ敊璇級")
        raise last_error

    async def _call_pool(
        self,
        pool: List[PoolEntry],
        pool_name: str,
        messages: list,
        max_tokens: int,
        temperature: float,
        timeout: int,
        audit_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        遍历模型池，依次尝试每个模型。
        timeout: 整个池的总时间预算（秒），按剩余模型数均分。
        单个模型内由 _call_model_with_retries 处理重试（最多2次）。
        若该模型最终失败，切换到池中下一个模型。
        全部失败才抛出最后一个异常。
        """
        if not pool:
            raise RuntimeError(f"{pool_name}池为空，无可用模型")

        deadline = _time.time() + timeout
        last_error: Optional[Exception] = None

        logger.info(f"=== {pool_name}池开始调用 ===")
        logger.info(f"池内模型数: {len(pool)}, 总超时: {timeout}s")

        for i, (client, model, display) in enumerate(pool):
            remaining = deadline - _time.time()
            if remaining < 10:
                logger.warning(
                    f"{pool_name}池: 时间预算耗尽 ({remaining:.0f}s)，"
                    f"跳过剩余 {len(pool) - i} 个模型"
                )
                break

            # 均分剩余时间给剩余模型
            models_left = len(pool) - i
            per_model_time = max(15, int(remaining / models_left))

            logger.info(f"尝试模型 {i+1}/{len(pool)}: {display}, 分配超时: {per_model_time}s")

            try:
                start = _time.time()
                result = await self._call_model_with_audit(
                    client=client,
                    model=model,
                    provider_name=display,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=per_model_time,
                    pool_name=pool_name,
                    pool_index=i + 1,
                    pool_size=len(pool),
                    audit_context=audit_context,
                )
                elapsed = _time.time() - start
                logger.info(f"✅ {display} 成功，耗时: {elapsed:.1f}s, 输出长度: {len(result)} 字符")
                if i > 0:
                    logger.info(f"{pool_name}池: 第{i + 1}个模型 {display} 成功接管")
                return result
            except Exception as e:
                last_error = e
                elapsed = _time.time() - start
                logger.error(f"❌ {display} 失败 (耗时{elapsed:.1f}s): {type(e).__name__}: {str(e)[:100]}")
                if i < len(pool) - 1:
                    logger.info(f"切换到下一个模型...")
                    continue
                break

        if last_error is not None:
            logger.error(f"{pool_name}池全部失败，最后错误: {type(last_error).__name__}")
            raise last_error
        raise RuntimeError(f"{pool_name}池全部失败（未知错误）")

    # ──────────────────── 公开接口（向后兼容） ────────────────────

    async def generate_content(
        self,
        prompt: str,
        max_tokens: int = 4000,
        temperature: float = 0.3,
        timeout: int = 120,
        use_heavy: bool = False,
        preferred_provider: Optional[str] = None,
        preferred_model: Optional[str] = None,
        audit_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """生成文本内容。use_heavy=True 走 Heavy池，否则走 Light池。

        timeout: 池级总预算（秒），会按模型数均分。
        """
        pool, pool_name = self._compose_text_pool(
            use_heavy=use_heavy,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
        )
        messages = [{"role": "user", "content": prompt}]
        call_context = audit_context or create_llm_call_context(
            call_kind="content",
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            use_heavy=use_heavy,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
        )

        return await self._call_pool(
            pool=pool,
            pool_name=pool_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            audit_context=call_context,
        )

    def _resolve_text_pool(self, use_heavy: bool) -> Tuple[List[PoolEntry], str]:
        pool = self._heavy_pool if use_heavy else self._light_pool
        pool_name = "Heavy" if use_heavy else "Light"
        if pool:
            return pool, pool_name

        if use_heavy:
            raise RuntimeError(
                "Heavy池为空：未配置任何重量级模型"
                "（需要 GEMINI_API_KEY 或 POOL_HEAVY）"
            )
        raise RuntimeError(
            "Light池为空：未配置任何轻量级模型"
            "（需要 DEEPSEEK_API_KEY 或 POOL_LIGHT）"
        )

    def _resolve_direct_entry(
        self,
        preferred_provider: Optional[str],
        preferred_model: Optional[str],
    ) -> Optional[PoolEntry]:
        provider_name = (preferred_provider or "").strip()
        if not provider_name or provider_name == "auto":
            return None

        provider = self._providers.get(provider_name)
        if provider is None:
            raise RuntimeError(f"未注册的 provider: {provider_name}")

        client, registered_model = provider
        model = (preferred_model or registered_model or "").strip()
        if not model or model == "auto":
            raise RuntimeError(f"provider {provider_name} 未配置默认模型，且本次请求也未指定 model")

        return (client, model, f"{provider_name}/{model}")

    def _pool_entry_key(self, entry: PoolEntry) -> Tuple[int, str]:
        client, model, _ = entry
        return (id(client), model)

    def _compose_text_pool(
        self,
        use_heavy: bool,
        preferred_provider: Optional[str],
        preferred_model: Optional[str],
    ) -> Tuple[List[PoolEntry], str]:
        direct_entry = self._resolve_direct_entry(preferred_provider, preferred_model)
        if direct_entry is None:
            return self._resolve_text_pool(use_heavy)

        pool = [direct_entry]
        pool_name = f"Preferred({direct_entry[2]})"

        try:
            fallback_pool, fallback_name = self._resolve_text_pool(use_heavy)
        except Exception as exc:
            logger.warning(
                "Preferred model %s has no fallback pool configured: %s",
                direct_entry[2],
                exc,
            )
            return pool, pool_name

        seen = {self._pool_entry_key(direct_entry)}
        for entry in fallback_pool:
            key = self._pool_entry_key(entry)
            if key in seen:
                continue
            seen.add(key)
            pool.append(entry)

        if len(pool) > 1:
            pool_name = f"{pool_name} -> {fallback_name}"
        return pool, pool_name

    def _extract_stream_delta(self, chunk) -> str:
        try:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                return ""
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None) if delta is not None else None
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                    else:
                        text = getattr(item, "text", None)
                    if text:
                        parts.append(str(text))
                return "".join(parts)
        except Exception:
            return ""
        return ""

    async def _call_model_stream(
        self,
        client,
        model: str,
        provider_name: str,
        messages: list,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Tuple[str, object | None]] = asyncio.Queue()

        def _push(kind: str, payload: object | None = None) -> None:
            asyncio.run_coroutine_threadsafe(queue.put((kind, payload)), loop)

        def _worker() -> None:
            try:
                stream = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                )
                for chunk in stream:
                    delta_text = self._extract_stream_delta(chunk)
                    if delta_text:
                        _push("delta", delta_text)
            except Exception as exc:
                _push("error", exc)
            finally:
                _push("done")

        threading.Thread(
            target=_worker,
            name=f"ai-stream-{provider_name}",
            daemon=True,
        ).start()

        deadline = loop.time() + timeout
        while True:
            remaining = max(1.0, deadline - loop.time())
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"{provider_name}流式请求超时({timeout}s)") from exc

            if kind == "delta":
                yield str(payload or "")
                continue
            if kind == "error":
                if isinstance(payload, Exception):
                    raise payload
                raise RuntimeError(f"{provider_name}流式调用失败")
            break

    async def generate_content_stream(
        self,
        prompt: str,
        max_tokens: int = 4000,
        temperature: float = 0.3,
        timeout: int = 120,
        use_heavy: bool = False,
        preferred_provider: Optional[str] = None,
        preferred_model: Optional[str] = None,
        audit_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        pool, pool_name = self._compose_text_pool(
            use_heavy=use_heavy,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
        )
        messages = [{"role": "user", "content": prompt}]
        call_context = audit_context or create_llm_call_context(
            call_kind="stream",
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            use_heavy=use_heavy,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
        )
        deadline = _time.time() + timeout
        last_error: Optional[Exception] = None

        for index, (client, model, display) in enumerate(pool):
            remaining = deadline - _time.time()
            if remaining < 10:
                break

            per_model_time = max(15, int(remaining / max(1, len(pool) - index)))
            emitted = False
            output_chars = 0
            started = _time.time()
            logger.info(f"=== {pool_name}池开始流式调用: {display}, 分配超时 {per_model_time}s ===")

            try:
                async for chunk in self._call_model_stream(
                    client=client,
                    model=model,
                    provider_name=display,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=per_model_time,
                ):
                    if chunk:
                        emitted = True
                        output_chars += len(chunk)
                        yield chunk
                log_llm_attempt(
                    provider=display.split("/", 1)[0],
                    model=model,
                    provider_display=display,
                    base_url=self._client_base_url(client),
                    pool_name=pool_name,
                    pool_index=index + 1,
                    pool_size=len(pool),
                    attempt=1,
                    status="success",
                    elapsed_ms=int((_time.time() - started) * 1000),
                    output_chars=output_chars,
                    audit_context=call_context,
                )
                return
            except Exception as exc:
                last_error = exc
                log_llm_attempt(
                    provider=display.split("/", 1)[0],
                    model=model,
                    provider_display=display,
                    base_url=self._client_base_url(client),
                    pool_name=pool_name,
                    pool_index=index + 1,
                    pool_size=len(pool),
                    attempt=1,
                    status="error",
                    elapsed_ms=int((_time.time() - started) * 1000),
                    output_chars=output_chars,
                    error=exc,
                    audit_context=call_context,
                )
                logger.error(f"❌ {display} 流式失败: {type(exc).__name__}: {str(exc)[:120]}")
                if emitted:
                    raise
                continue

        if last_error is not None:
            logger.warning(f"{pool_name}池流式全部失败，回退到普通生成")
            fallback_text = await self.generate_content(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=max(15, int(deadline - _time.time())),
                use_heavy=use_heavy,
                preferred_provider=preferred_provider,
                preferred_model=preferred_model,
                audit_context=derive_llm_call_context(
                    call_context,
                    call_kind="content",
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=max(15, int(deadline - _time.time())),
                    use_heavy=use_heavy,
                    preferred_provider=preferred_provider,
                    preferred_model=preferred_model,
                    phase="stream_fallback_text",
                ),
            )
            if fallback_text:
                yield fallback_text
            return

        raise RuntimeError(f"{pool_name}池流式全部失败（未知错误）")

    def _strip_code_fence(self, text: str) -> str:
        """移除 markdown 代码块包裹，减少 JSON 解析噪声。"""
        cleaned = (text or "").strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return cleaned.strip()

    async def _parse_json_with_repair(
        self,
        text: str,
        schema: Dict,
        max_tokens: int,
        timeout: int,
        preferred_provider: Optional[str] = None,
        preferred_model: Optional[str] = None,
        audit_context: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """解析 JSON，失败时尝试提取/修复。"""
        cleaned = self._strip_code_fence(text)
        first_error: Optional[json.JSONDecodeError] = None

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            first_error = e

        # 尝试从混杂文本中提取首尾 JSON 对象
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first != -1 and last != -1 and last > first:
            candidate = cleaned[first : last + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        # 最后尝试：交给 Light池 做一次 JSON 修复
        repair_prompt = (
            "你是 JSON 修复器。请将下面文本修复为合法 JSON，"
            "并严格匹配给定 schema，不要输出任何解释。\n\n"
            f"Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
            f"Raw:\n{cleaned[:6000]}"
        )
        repaired = await self.generate_content(
            repair_prompt,
            max_tokens=min(max_tokens, 2400),
            temperature=0.0,
            timeout=min(timeout, 120),
            use_heavy=False,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            audit_context=derive_llm_call_context(
                audit_context,
                call_kind="json_repair",
                messages=[{"role": "user", "content": repair_prompt}],
                max_tokens=min(max_tokens, 2400),
                temperature=0.0,
                timeout=min(timeout, 120),
                use_heavy=False,
                preferred_provider=preferred_provider,
                preferred_model=preferred_model,
                phase="json_repair",
                metadata={
                    "schema_keys": sorted(str(key) for key in schema.keys())[:40],
                },
            ),
        )
        repaired = self._strip_code_fence(repaired)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            if first_error is None:
                raise
            raise first_error

    async def generate_json(
        self,
        prompt: str,
        schema: Dict,
        max_tokens: int = 4000,
        temperature: float = 0.2,
        timeout: int = 150,
        use_heavy: bool = False,
        preferred_provider: Optional[str] = None,
        preferred_model: Optional[str] = None,
        audit_context: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """生成 JSON。use_heavy=True 走 Heavy池，否则走 Light池。

        timeout: generate_json 的总时间预算（秒），内部所有重试共享此预算。
        """
        deadline = _time.time() + timeout
        root_messages = [{"role": "user", "content": prompt}]
        call_context = audit_context or create_llm_call_context(
            call_kind="json",
            messages=root_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            use_heavy=use_heavy,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            metadata={
                "schema_keys": sorted(str(key) for key in schema.keys())[:40],
            },
        )

        json_prompt = (
            f"{prompt}\n\n请返回JSON格式：\n"
            f"{json.dumps(schema, indent=2, ensure_ascii=False)}\n只返回JSON："
        )

        retry_prompt = (
            f"{json_prompt}\n\n"
            "上一次输出 JSON 不合法。请严格遵守：\n"
            "1) 必须是单个完整 JSON 对象\n"
            "2) 必须闭合所有括号与引号\n"
            "3) 不要 markdown，不要解释，不要省略号"
        )

        last_error: Optional[Exception] = None
        prompts = [json_prompt, retry_prompt]
        for i, current_prompt in enumerate(prompts, 1):
            remaining = deadline - _time.time()
            if remaining < 15:
                print(
                    f"[AIClient] generate_json 时间预算不足"
                    f"({remaining:.0f}s)，跳过第{i}次尝试"
                )
                break
            text = await self.generate_content(
                current_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=int(remaining),
                use_heavy=use_heavy,
                preferred_provider=preferred_provider,
                preferred_model=preferred_model,
                audit_context=derive_llm_call_context(
                    call_context,
                    call_kind="json_prompt",
                    messages=[{"role": "user", "content": current_prompt}],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=int(remaining),
                    use_heavy=use_heavy,
                    preferred_provider=preferred_provider,
                    preferred_model=preferred_model,
                    phase=f"json_prompt_{i}",
                ),
            )
            try:
                repair_remaining = max(15, int(deadline - _time.time()))
                return await self._parse_json_with_repair(
                    text,
                    schema,
                    max_tokens,
                    repair_remaining,
                    preferred_provider=preferred_provider,
                    preferred_model=preferred_model,
                    audit_context=derive_llm_call_context(
                        call_context,
                        call_kind="json_parse",
                        messages=[{"role": "assistant", "content": text}],
                        max_tokens=max_tokens,
                        temperature=temperature,
                        timeout=repair_remaining,
                        use_heavy=use_heavy,
                        preferred_provider=preferred_provider,
                        preferred_model=preferred_model,
                        phase=f"json_parse_{i}",
                    ),
                )
            except Exception as e:
                last_error = e
                print(f"[AIClient] JSON解析尝试{i}/{len(prompts)}失败: {e}")
                print(
                    f"[AIClient] 原始响应前500字符: "
                    f"{self._strip_code_fence(text)[:500]}"
                )

        # Heavy 任务 JSON 失败后，尝试 Fast池 兜底
        remaining = deadline - _time.time()
        if remaining > 15 and use_heavy and self._fast_pool:
            fast_prompt = (
                f"{retry_prompt}\n\n"
                "你现在处于快速兜底模式：\n"
                "1) 优先保证 JSON 完整合法\n"
                "2) 输出精简但字段必须完整\n"
                "3) 若题干较长可适度压缩表述，但不得缺字段"
            )
            try:
                fast_timeout = min(int(remaining), 90)
                print(
                    f"[AIClient] Heavy JSON 失败，启动 Fast池 兜底"
                    f"（剩余{remaining:.0f}s，分配{fast_timeout}s）"
                )
                text = await self._call_pool(
                    pool=self._fast_pool,
                    pool_name="Fast(JSON兜底)",
                    messages=[{"role": "user", "content": fast_prompt}],
                    max_tokens=min(max_tokens, 3200),
                    temperature=min(temperature, 0.2),
                    timeout=fast_timeout,
                    audit_context=derive_llm_call_context(
                        call_context,
                        call_kind="json_fast_fallback",
                        messages=[{"role": "user", "content": fast_prompt}],
                        max_tokens=min(max_tokens, 3200),
                        temperature=min(temperature, 0.2),
                        timeout=fast_timeout,
                        use_heavy=False,
                        preferred_provider=preferred_provider,
                        preferred_model=preferred_model,
                        phase="json_fast_fallback",
                    ),
                )
                repair_remaining = max(15, int(deadline - _time.time()))
                return await self._parse_json_with_repair(
                    text=text,
                    schema=schema,
                    max_tokens=min(max_tokens, 3200),
                    timeout=repair_remaining,
                    preferred_provider=preferred_provider,
                    preferred_model=preferred_model,
                    audit_context=derive_llm_call_context(
                        call_context,
                        call_kind="json_parse",
                        messages=[{"role": "assistant", "content": text}],
                        max_tokens=min(max_tokens, 3200),
                        temperature=min(temperature, 0.2),
                        timeout=repair_remaining,
                        use_heavy=False,
                        preferred_provider=preferred_provider,
                        preferred_model=preferred_model,
                        phase="json_fast_parse",
                    ),
                )
            except Exception as e:
                last_error = e
                print(f"[AIClient] Fast池兜底失败: {e}")

        if last_error is not None:
            raise last_error
        raise RuntimeError("JSON解析失败（未知错误）")


_ai_client = None


def get_ai_client():
    global _ai_client
    if _ai_client is None:
        _ai_client = AIClient()
    return _ai_client
