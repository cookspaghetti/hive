"""LLM access layer: Ollama Cloud client + cost-tiering router.

Reference: Hermes provider-abstraction pattern (reference-mapping.md L2).
The router is an original contribution for cost-aware scam-baiting.
"""

from hive.llm.client import ChatMessage, LLMClient, LLMResponse
from hive.llm.router import Tier, route

__all__ = ["LLMClient", "ChatMessage", "LLMResponse", "Tier", "route"]
