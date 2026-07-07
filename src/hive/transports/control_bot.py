"""Bot API control plane (fyp.txt Platform).

A separate @Bot the operator chats with to drive the system. Every command is
gated to HIVE_OPERATOR_ID. Commands:
    /takeover <peer_id> [persona]  begin a takeover on the userbot
    /persona <name>                set the default persona for new takeovers
    /stop <peer_id>                kill switch: reclaim, seal evidence, report
    /status <peer_id>              current verdict + summary

Requires a live Bot API connection, so not unit-tested.
"""

from __future__ import annotations

import os

from hive.agent.personas import PERSONAS
from hive.config import Settings
from hive.logging_setup import get_logger
from hive.runtime import HiveEngine
from hive.transports.userbot import UserbotTransport

log = get_logger(__name__)

VALID_PERSONAS = set(PERSONAS)


class ControlBot:
    def __init__(
        self,
        settings: Settings,
        engine: HiveEngine,
        userbot: UserbotTransport,
    ) -> None:
        self.settings = settings
        self.operator_id = settings.operator_id
        self.engine = engine
        self.userbot = userbot
        self.default_persona = settings.default_persona
        self._app = None
        # Get notified when the userbot hands a benign conversation back.
        self.userbot.on_handback = self._on_handback

    def _authorised(self, user_id: int | None) -> bool:
        return user_id == self.operator_id

    async def _on_handback(self, peer_id, session) -> None:  # pragma: no cover
        """Fired by the userbot when a benign conversation is handed back.

        No evidence bundle is sealed (the verdict is benign); we just inform the
        operator that control has returned to them for this chat.
        """
        text = (
            f"↩️ Handed back chat {peer_id} — assessed as {session.verdict} "
            f"after {session.turn_count} turn(s). You are back in control.\n\n"
            f"{self.engine.summary(session)}"
        )
        if self._app is not None:
            await self._app.bot.send_message(chat_id=self.operator_id, text=text)
        log.info("control bot: notified operator of hand-back peer=%s", peer_id)

    async def start(self) -> None:  # pragma: no cover - needs live telegram
        from telegram.ext import Application, CommandHandler

        self._app = Application.builder().token(self.settings.control_bot_token).build()
        self._app.add_handler(CommandHandler("takeover", self._cmd_takeover))
        self._app.add_handler(CommandHandler("persona", self._cmd_persona))
        self._app.add_handler(CommandHandler("stop", self._cmd_stop))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        log.info("control bot: polling started")

    # --- command handlers (thin wrappers around testable logic) ---

    async def _guard(self, update) -> bool:  # pragma: no cover
        uid = update.effective_user.id if update.effective_user else None
        if not self._authorised(uid):
            await update.message.reply_text("Unauthorised.")
            log.warning("control bot: rejected unauthorised user=%s", uid)
            return False
        return True

    async def _peer_arg(self, update, context, usage: str) -> int | None:
        """Parse args[0] as an int peer id, or reply usage and return None."""
        if not context.args:
            await update.message.reply_text(usage)
            return None
        try:
            return int(context.args[0])
        except (ValueError, TypeError):
            await update.message.reply_text(usage)
            return None

    async def _cmd_takeover(self, update, context):  # pragma: no cover
        if not await self._guard(update):
            return
        peer = await self._peer_arg(update, context, "Usage: /takeover <peer_id> [persona]")
        if peer is None:
            return
        persona = context.args[1] if len(context.args) > 1 else self.default_persona
        if persona not in VALID_PERSONAS:
            await update.message.reply_text(
                f"Unknown persona '{persona}'. Choose from: {', '.join(sorted(VALID_PERSONAS))}"
            )
            return
        self.userbot.begin_takeover(peer, persona)
        await update.message.reply_text(f"Takeover started on {peer} as {persona}.")

    async def _cmd_persona(self, update, context):  # pragma: no cover
        if not await self._guard(update):
            return
        if not context.args:
            await update.message.reply_text(
                f"Usage: /persona <name>. Choose from: {', '.join(sorted(VALID_PERSONAS))}"
            )
            return
        name = context.args[0]
        if name not in VALID_PERSONAS:
            await update.message.reply_text(
                f"Unknown persona '{name}'. Choose from: {', '.join(sorted(VALID_PERSONAS))}"
            )
            return
        self.default_persona = name
        await update.message.reply_text(f"Default persona set to {self.default_persona}.")

    async def _cmd_status(self, update, context):  # pragma: no cover
        if not await self._guard(update):
            return
        peer = await self._peer_arg(update, context, "Usage: /status <peer_id>")
        if peer is None:
            return
        entry = self.userbot._sessions.get(peer)
        if entry is None:
            await update.message.reply_text(f"No active takeover on {peer}.")
            return
        await update.message.reply_text(self.engine.summary(entry[0]))

    async def _cmd_stop(self, update, context):  # pragma: no cover
        if not await self._guard(update):
            return
        peer = await self._peer_arg(update, context, "Usage: /stop <peer_id>")
        if peer is None:
            return
        entry = self.userbot.end_takeover(peer)
        if entry is None:
            await update.message.reply_text(f"No active takeover on {peer}.")
            return
        session, chain = entry
        os.makedirs("evidence", exist_ok=True)
        out_path = f"evidence/bundle_{peer}.pdf"
        self.engine.close_session(session, chain, out_path, self.settings.signing_key_path)
        await update.message.reply_text(self.engine.summary(session))
        with open(out_path, "rb") as fh:
            await update.message.reply_document(fh, filename=f"evidence_{peer}.pdf")
        log.info("control bot: stopped + sealed peer=%s", peer)
