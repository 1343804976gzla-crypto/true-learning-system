from __future__ import annotations

import asyncio
from types import MethodType

from services.ai_client import AIClient


def _build_client() -> tuple[AIClient, str]:
    client = AIClient.__new__(AIClient)
    preferred_client = object()
    fallback_client = object()
    fallback_display = "gemini/gemini-3.1-flash-lite-preview"

    client._providers = {
        "qingyun": (preferred_client, "claude-sonnet-4-6"),
    }
    client._heavy_pool = []
    client._light_pool = [
        (fallback_client, "gemini-3.1-flash-lite-preview", fallback_display),
    ]
    client._fast_pool = []
    return client, fallback_display


def test_generate_content_falls_back_after_preferred_model_failure():
    client, fallback_display = _build_client()
    attempts: list[str] = []

    async def fake_call_model_with_retries(
        self,
        client,
        model: str,
        provider_name: str,
        messages: list,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        attempts.append(provider_name)
        if provider_name == "qingyun/claude-sonnet-4-6":
            raise RuntimeError("Error code: 503 - No available channels")
        return f"ok:{provider_name}"

    client._call_model_with_retries = MethodType(fake_call_model_with_retries, client)

    result = asyncio.run(
        client.generate_content(
            prompt="fallback test",
            preferred_provider="qingyun",
            preferred_model="claude-sonnet-4-6",
        )
    )

    assert result == f"ok:{fallback_display}"
    assert attempts == ["qingyun/claude-sonnet-4-6", fallback_display]


def test_generate_content_stream_falls_back_after_preferred_model_failure():
    client, fallback_display = _build_client()
    attempts: list[str] = []

    async def fake_call_model_stream(
        self,
        client,
        model: str,
        provider_name: str,
        messages: list,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ):
        attempts.append(provider_name)
        if provider_name == "qingyun/claude-sonnet-4-6":
            raise RuntimeError("Error code: 503 - No available channels")
        for chunk in ("fallback", " stream"):
            yield chunk

    client._call_model_stream = MethodType(fake_call_model_stream, client)

    async def collect_stream() -> str:
        chunks: list[str] = []
        async for chunk in client.generate_content_stream(
            prompt="stream fallback test",
            preferred_provider="qingyun",
            preferred_model="claude-sonnet-4-6",
        ):
            chunks.append(chunk)
        return "".join(chunks)

    result = asyncio.run(collect_stream())

    assert result == "fallback stream"
    assert attempts == ["qingyun/claude-sonnet-4-6", fallback_display]
