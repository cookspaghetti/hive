"""Localhost web control panel (operator console).

A FastAPI app, bound to localhost and gated by an operator token, that runs
in-process with the HiveEngine + userbot so it reads live per-peer session
state directly. Mirrors Hermes's dashboard model: never network-exposed, auth
required. It is an additional control surface alongside the Bot API control
plane, not a replacement.
"""

from hive.webpanel.app import create_app

__all__ = ["create_app"]
