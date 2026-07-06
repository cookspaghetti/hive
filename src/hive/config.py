"""Central configuration, loaded from environment / .env.

See .env.example for the full list of variables.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HIVE_", env_file=".env", extra="ignore")

    # LLM
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"

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

    # Signing
    signing_key_path: str = "./secrets/signing_key.pem"

    # Behaviour
    default_persona: str = "confused_elderly"
    max_turns: int = 60
    max_session_minutes: int = 120


def load_settings() -> Settings:
    return Settings()
