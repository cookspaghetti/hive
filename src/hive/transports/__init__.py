"""Telegram transports (fyp.txt Platform, dual-channel).

Two transports behind one agent (reference-mapping.md Platform):
    userbot.py     — Telethon MTProto DATA plane (takes over the user's account)
    control_bot.py — Bot API CONTROL plane (operator config/trigger/summary)
"""
