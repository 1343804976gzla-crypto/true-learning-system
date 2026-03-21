"""Backward compatibility — all logic moved to services.api_hub"""
from services.api_hub import AIClient, get_ai_client

__all__ = ["AIClient", "get_ai_client"]
