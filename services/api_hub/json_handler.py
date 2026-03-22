"""JSON generation pipeline with repair and Fast pool fallback."""

from __future__ import annotations

import json
import logging
import time as _time
from typing import Any, Callable, Dict, List, Optional

from services.api_hub._types import PoolEntry

logger = logging.getLogger(__name__)


def strip_code_fence(text: str) -> str:
    """Remove markdown code block wrappers to reduce JSON parse noise."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


async def parse_json_with_repair(
    text: str,
    schema: Dict,
    max_tokens: int,
    timeout: int,
    generate_content_fn: Callable,
    preferred_provider: Optional[str] = None,
    preferred_model: Optional[str] = None,
    fallback_to_pool: bool = True,
    audit_context: Optional[Dict[str, Any]] = None,
    derive_context_fn: Optional[Callable] = None,
) -> Dict:
    """Parse JSON from LLM output, with extraction heuristics and LLM repair fallback.

    generate_content_fn: async callable matching AIClient.generate_content signature.
    derive_context_fn: optional callable to derive audit context for repair call.
    """
    cleaned = strip_code_fence(text)
    first_error: Optional[json.JSONDecodeError] = None

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        first_error = e

    # Try extracting first/last JSON object from mixed text
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidate = cleaned[first : last + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Last resort: ask Light pool to repair the JSON
    repair_prompt = (
        "你是 JSON 修复器。请将下面文本修复为合法 JSON，"
        "并严格匹配给定 schema，不要输出任何解释。\n\n"
        f"Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Raw:\n{cleaned[:6000]}"
    )

    repair_audit = None
    if derive_context_fn and audit_context:
        repair_audit = derive_context_fn(
            audit_context,
            call_kind="json_repair",
            messages=[{"role": "user", "content": repair_prompt}],
            max_tokens=min(max_tokens, 2400),
            temperature=0.0,
            timeout=min(timeout, 120),
            use_heavy=False,
            preferred_provider=preferred_provider if not fallback_to_pool else None,
            preferred_model=preferred_model if not fallback_to_pool else None,
            phase="json_repair",
            metadata={
                "schema_keys": sorted(str(key) for key in schema.keys())[:40],
            },
        )

    repaired = await generate_content_fn(
        repair_prompt,
        max_tokens=min(max_tokens, 2400),
        temperature=0.0,
        timeout=min(timeout, 120),
        use_heavy=False,
        preferred_provider=preferred_provider if not fallback_to_pool else None,
        preferred_model=preferred_model if not fallback_to_pool else None,
        fallback_to_pool=fallback_to_pool,
        audit_context=repair_audit,
    )
    repaired = strip_code_fence(repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        if first_error is None:
            raise
        raise first_error


async def generate_json(
    prompt: str,
    schema: Dict,
    generate_content_fn: Callable,
    call_pool_fn: Callable,
    fast_pool: List[PoolEntry],
    max_tokens: int = 4000,
    temperature: float = 0.2,
    timeout: int = 150,
    use_heavy: bool = False,
    preferred_provider: Optional[str] = None,
    preferred_model: Optional[str] = None,
    fallback_to_pool: bool = True,
    audit_context: Optional[Dict[str, Any]] = None,
    create_context_fn: Optional[Callable] = None,
    derive_context_fn: Optional[Callable] = None,
) -> Dict:
    """Generate JSON with two-prompt retry strategy and Fast pool fallback.

    generate_content_fn: async callable for text generation.
    call_pool_fn: async callable for direct pool calls (Fast fallback).
    fast_pool: the Fast pool entries for JSON fallback.
    """
    deadline = _time.time() + timeout
    root_messages = [{"role": "user", "content": prompt}]

    call_context = audit_context
    if call_context is None and create_context_fn:
        call_context = create_context_fn(
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
                f"[AIClient] generate_json time budget insufficient "
                f"({remaining:.0f}s), skipping attempt {i}"
            )
            break

        attempt_preferred_provider = preferred_provider if i == 1 else None
        attempt_preferred_model = preferred_model if i == 1 else None

        prompt_audit = None
        if derive_context_fn and call_context:
            prompt_audit = derive_context_fn(
                call_context,
                call_kind="json_prompt",
                messages=[{"role": "user", "content": current_prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=int(remaining),
                use_heavy=use_heavy,
                preferred_provider=attempt_preferred_provider,
                preferred_model=attempt_preferred_model,
                phase=f"json_prompt_{i}",
            )

        text = await generate_content_fn(
            current_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=int(remaining),
            use_heavy=use_heavy,
            preferred_provider=attempt_preferred_provider,
            preferred_model=attempt_preferred_model,
            fallback_to_pool=fallback_to_pool,
            audit_context=prompt_audit,
        )

        try:
            repair_remaining = max(15, int(deadline - _time.time()))
            parse_audit = None
            if derive_context_fn and call_context:
                parse_audit = derive_context_fn(
                    call_context,
                    call_kind="json_parse",
                    messages=[{"role": "assistant", "content": text}],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=repair_remaining,
                    use_heavy=use_heavy,
                    preferred_provider=attempt_preferred_provider,
                    preferred_model=attempt_preferred_model,
                    phase=f"json_parse_{i}",
                )
            return await parse_json_with_repair(
                text,
                schema,
                max_tokens,
                repair_remaining,
                generate_content_fn=generate_content_fn,
                preferred_provider=attempt_preferred_provider,
                preferred_model=attempt_preferred_model,
                fallback_to_pool=fallback_to_pool,
                audit_context=parse_audit,
                derive_context_fn=derive_context_fn,
            )
        except Exception as e:
            last_error = e
            print(f"[AIClient] JSON parse attempt {i}/{len(prompts)} failed: {e}")
            print(
                f"[AIClient] Raw response first 500 chars: "
                f"{strip_code_fence(text)[:500]}"
            )

    # Heavy task JSON failure → try Fast pool fallback
    remaining = deadline - _time.time()
    if remaining > 15 and use_heavy and fallback_to_pool and fast_pool:
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
                f"[AIClient] Heavy JSON failed, starting Fast pool fallback "
                f"(remaining {remaining:.0f}s, allocated {fast_timeout}s)"
            )

            fast_audit = None
            if derive_context_fn and call_context:
                fast_audit = derive_context_fn(
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
                )

            text = await call_pool_fn(
                pool=fast_pool,
                pool_name="Fast(JSON兜底)",
                messages=[{"role": "user", "content": fast_prompt}],
                max_tokens=min(max_tokens, 3200),
                temperature=min(temperature, 0.2),
                timeout=fast_timeout,
                audit_context=fast_audit,
            )

            repair_remaining = max(15, int(deadline - _time.time()))
            fast_parse_audit = None
            if derive_context_fn and call_context:
                fast_parse_audit = derive_context_fn(
                    call_context,
                    call_kind="json_parse",
                    messages=[{"role": "assistant", "content": text}],
                    max_tokens=min(max_tokens, 3200),
                    temperature=min(temperature, 0.2),
                    timeout=repair_remaining,
                    use_heavy=False,
                    preferred_provider=None,
                    preferred_model=None,
                    phase="json_fast_parse",
                )

            return await parse_json_with_repair(
                text=text,
                schema=schema,
                max_tokens=min(max_tokens, 3200),
                timeout=repair_remaining,
                generate_content_fn=generate_content_fn,
                preferred_provider=None,
                preferred_model=None,
                fallback_to_pool=True,
                audit_context=fast_parse_audit,
                derive_context_fn=derive_context_fn,
            )
        except Exception as e:
            last_error = e
            print(f"[AIClient] Fast pool fallback failed: {e}")

    if last_error is not None:
        raise last_error
    raise RuntimeError("JSON generation failed (unknown error)")
