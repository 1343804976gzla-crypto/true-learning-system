"""
API Hub — Modular AI client with centralized management.

Re-exports AIClient and get_ai_client for backward compatibility.
All logic lives in sub-modules; this package is the public entry point.
"""

from services.api_hub.facade import AIClient, get_ai_client

__all__ = ["AIClient", "get_ai_client"]
