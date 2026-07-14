"""HIVE runtime entry point.

Boots the dual-channel Telegram system:
  - decrypts the Telethon session (S7),
  - builds a fully-wired HiveEngine (LLM + sandbox + NER),
  - starts the Telethon userbot (data plane) and Bot API control bot
    (control plane) on one asyncio loop.

Run:  python -m hive   (after `cp .env.example .env` and filling in secrets)
"""

from __future__ import annotations

import asyncio

from hive.config import load_settings
from hive.logging_setup import configure_logging, get_logger
from hive.runtime import build_engine
from hive.security.session_store import load_session
from hive.transports.control_bot import ControlBot
from hive.transports.userbot import UserbotTransport

log = get_logger(__name__)


async def _run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    log.info("HIVE v0.1.0 starting; default persona=%s", settings.default_persona)

    session_str = load_session(settings.tg_session_path, settings.session_passphrase)
    engine = build_engine(settings)

    userbot = UserbotTransport(settings.tg_api_id, settings.tg_api_hash, session_str, engine)
    control = ControlBot(settings, engine, userbot)

    await userbot.start()
    await control.start()

    # Optional localhost web control panel (enabled when a token is set).
    if settings.panel_token:
        import uvicorn

        from hive.webpanel import create_app

        app = create_app(engine, userbot, settings)
        server = uvicorn.Server(
            uvicorn.Config(app, host=settings.panel_host, port=settings.panel_port, log_level="warning")
        )
        asyncio.create_task(server.serve())
        log.info("HIVE web panel on http://%s:%d", settings.panel_host, settings.panel_port)

    log.info("HIVE running: userbot + control bot up. Ctrl-C to stop.")
    await userbot.run_forever()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("HIVE stopped.")


if __name__ == "__main__":
    main()
