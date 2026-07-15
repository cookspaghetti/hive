"""Telegram Bot API token validation for provisioning."""

from __future__ import annotations

import re

TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")


async def verify_control_bot_token(token: str) -> dict[str, object]:
    if not TOKEN_RE.fullmatch(token):
        raise ValueError("invalid Telegram bot token format")

    from telegram import Bot

    try:
        bot = await Bot(token).get_me()
    except Exception as exc:
        raise ValueError("Telegram rejected the bot token") from exc
    return {"id": bot.id, "username": bot.username or ""}
