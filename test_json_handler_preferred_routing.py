from __future__ import annotations

import pytest

from services.api_hub.json_handler import generate_json, parse_json_with_repair


@pytest.mark.asyncio
async def test_parse_json_with_repair_clears_preferred_provider_for_repair_call():
    calls = []

    async def fake_generate_content(prompt, **kwargs):
        calls.append(kwargs)
        return '{"ok": true}'

    result = await parse_json_with_repair(
        text="not json",
        schema={"ok": True},
        max_tokens=100,
        timeout=30,
        generate_content_fn=fake_generate_content,
        preferred_provider="gemini",
        preferred_model="gemini-3.1-pro-preview",
    )

    assert result == {"ok": True}
    assert len(calls) == 1
    assert calls[0]["preferred_provider"] is None
    assert calls[0]["preferred_model"] is None


@pytest.mark.asyncio
async def test_generate_json_only_uses_preferred_provider_on_first_prompt_attempt():
    calls = []

    async def fake_generate_content(prompt, **kwargs):
        calls.append(kwargs)
        return "not json"

    async def fake_call_pool(**kwargs):
        raise AssertionError("fast pool fallback should not run in this test")

    with pytest.raises(Exception):
        await generate_json(
            prompt="p",
            schema={"a": ""},
            generate_content_fn=fake_generate_content,
            call_pool_fn=fake_call_pool,
            fast_pool=[],
            timeout=31,
            use_heavy=False,
            preferred_provider="gemini",
            preferred_model="gm",
        )

    assert calls[0]["preferred_provider"] == "gemini"
    assert calls[0]["preferred_model"] == "gm"
    assert calls[1]["preferred_provider"] is None
    assert calls[1]["preferred_model"] is None
    assert calls[2]["preferred_provider"] is None
    assert calls[2]["preferred_model"] is None
    assert calls[3]["preferred_provider"] is None
    assert calls[3]["preferred_model"] is None
