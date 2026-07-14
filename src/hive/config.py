"""Central configuration, loaded from environment / .env.

See .env.example for the full list of variables.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HIVE_", env_file=".env", extra="ignore")

    # LLM (Ollama Cloud, OpenAI-compatible endpoint)
    llm_api_key: str = ""
    llm_base_url: str = "https://ollama.com/v1"
    # Cost-tiered agent models (see llm/router.py):
    #   cheap  -> default persona chatter (bulk of turns)
    #   strong -> injection defence, HVI elicitation, consistency risk
    #   light  -> soft-signal verdict classification (runs every turn)
    llm_model_cheap: str = "glm-5.1:cloud"
    llm_model_strong: str = "glm-5.2:cloud"
    llm_model_light: str = "glm-5.1:cloud"
    # Vision model for L3 media fallback (separate track)
    vision_model: str = "qwen3.5:cloud"
    # Logging
    log_level: str = "INFO"

    # Telegram data plane (Telethon userbot)
    tg_api_id: int = 0
    tg_api_hash: str = ""
    tg_phone: str = ""
    tg_session_path: str = "./secrets/user.session"
    session_passphrase: str = ""

    # Telegram control plane (Bot API)
    control_bot_token: str = ""
    operator_id: int = 0

    # Stores
    qdrant_url: str = "http://localhost:6333"
    # L2 memory: False = offline KeywordMemory; True = semantic mem0+Qdrant
    use_semantic_memory: bool = False

    # Web control panel (localhost only). Empty token disables the panel.
    panel_token: str = ""
    panel_host: str = "127.0.0.1"
    panel_port: int = 9130

    # Signing
    signing_key_path: str = "./secrets/signing_key.pem"

    # Behaviour
    default_persona: str = "confused_elderly"
    max_turns: int = 60
    max_session_minutes: int = 120


def load_settings() -> Settings:
    return Settings()
