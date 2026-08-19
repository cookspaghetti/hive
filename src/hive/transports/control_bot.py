"""Bot API control plane (fyp.txt Platform).

A separate @Bot the operator chats with to drive the system. Every command is
gated to HIVE_OPERATOR_ID. Commands:
    /start, /help                 show the command reference
    /chats                        list recent private incoming chats
    /takeovers                    list active takeovers with controls
    /takeover <peer_id> [persona]  begin a takeover on the userbot
    /persona <name>                set the default persona for new takeovers
    /stop <peer_id>                confirm, reclaim, seal evidence, report
    /status <peer_id>              current verdict + summary

Requires a live Bot API connection, so not unit-tested.
"""

from __future__ import annotations

import asyncio

from hive.agent.personas import PERSONAS
from hive.audit import audit_event
from hive.config import Settings
from hive.history import HistoryStore, build_history_store
from hive.logging_setup import get_logger
from hive.runtime import HiveEngine
from hive.takeover import (
    TakeoverBusyError,
    TakeoverCoordinator,
    TakeoverNotFoundError,
    TakeoverSealError,
)
from hive.transports.userbot import UserbotTransport

log = get_logger(__name__)

VALID_PERSONAS = set(PERSONAS)
HELP_TEXT = (
    "HIVE control bot\n\n"
    "/chats - list recent incoming private chats\n"
    "/takeover <peer_id> [persona] - start a takeover\n"
    "/persona <name> - set the default persona\n"
    "/takeovers - list active takeovers with controls\n"
    "/status <peer_id> - show an active takeover and controls\n"
    "/stop <peer_id> - request stop-and-seal confirmation\n"
    "/help - show this command reference"
)


class ControlBot:
    def __init__(
        self,
        settings: Settings,
        engine: HiveEngine,
        userbot: UserbotTransport,
        history_store: HistoryStore | None = None,
        takeover_coordinator: TakeoverCoordinator | None = None,
    ) -> None:
        self.settings = settings
        self.operator_id = settings.operator_id
        self.engine = engine
        self.userbot = userbot
        self.default_persona = settings.default_persona
        self.history = (
            takeover_coordinator.history
            if takeover_coordinator is not None
            else history_store
            or build_history_store("evidence/history", getattr(settings, "database_url", ""))
        )
        self.takeovers = takeover_coordinator or TakeoverCoordinator(
            engine,
            userbot,
            settings,
            self.history,
            evidence_root="evidence",
        )
        self._app = None
        # Get notified when the userbot hands a benign conversation back.
        self.userbot.on_handback = self._on_handback
        self.userbot.on_takeover_request = self._on_takeover_request

    def _authorised(self, user_id: int | None) -> bool:
        return user_id == self.operator_id

    async def _reply_text(self, update, text: str, *, reply_markup=None) -> None:
        if reply_markup is None:
            await update.message.reply_text(text)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup)
        audit_event(
            "control_message",
            "control_bot_reply_sent",
            component="transport.control_bot",
            payload={"text": text},
        )

    async def _on_handback(self, peer_id, session) -> None:  # pragma: no cover
        """Fired by the userbot when a benign conversation is handed back.

        No evidence bundle is sealed (the verdict is benign); we just inform the
        operator that control has returned to them for this chat.
        """
        self.history.archive(session, status="handed_back")
        text = (
            f"↩️ Handed back chat {peer_id} — assessed as {session.verdict} "
            f"after {session.turn_count} turn(s). You are back in control.\n\n"
            f"{self.engine.summary(session)}"
        )
        if self._app is not None:
            await self._app.bot.send_message(chat_id=self.operator_id, text=text)
            audit_event(
                "control_message",
                "control_bot_reply_sent",
                component="transport.control_bot",
                payload={"text": text, "chat_id": self.operator_id},
                peer_id=peer_id,
                session_id=session.session_id,
            )
        log.info("control bot: notified operator of hand-back peer=%s", peer_id)

    async def _on_takeover_request(self, chat: dict[str, object]) -> bool:
        """Push a pending private chat to the operator's Telegram control bot."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        if self._app is None:
            return False
        peer_id = int(chat["peer_id"])
        display_name = str(chat.get("name") or f"Peer {peer_id}")
        username = str(chat.get("username") or "")
        account = f"{display_name} (@{username})" if username else display_name
        preview = str(chat.get("last_message") or "[media or empty message]")
        text = (
            "🚨 HIVE takeover request\n\n"
            f"From: {account}\n"
            f"Peer ID: {peer_id}\n"
            f"Messages waiting: {int(chat.get('message_count') or 1)}\n"
            f"Latest: {preview}\n\n"
            "Take over this chat?"
        )
        reply_markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Yes — Take over",
                        callback_data=f"hive_takeover:yes:{peer_id}",
                    ),
                    InlineKeyboardButton(
                        "No — Dismiss",
                        callback_data=f"hive_takeover:no:{peer_id}",
                    ),
                ]
            ]
        )
        await self._app.bot.send_message(
            chat_id=self.operator_id,
            text=text,
            reply_markup=reply_markup,
        )
        audit_event(
            "control_message",
            "takeover_request_sent_to_control_bot",
            component="transport.control_bot",
            payload={"chat_id": self.operator_id, "text": text},
            peer_id=peer_id,
        )
        return True

    async def _callback_takeover_request(self, update, context) -> None:
        query = update.callback_query
        user_id = update.effective_user.id if update.effective_user else None
        authorised = self._authorised(user_id)
        data = str(getattr(query, "data", "") or "")
        audit_event(
            "control_message",
            "takeover_request_button_pressed",
            component="transport.control_bot",
            payload={"operator_id": user_id, "callback_data": data, "authorised": authorised},
            level="info" if authorised else "warning",
        )
        if not authorised:
            await query.answer("Unauthorised.", show_alert=True)
            return
        await query.answer()
        try:
            _prefix, action, raw_peer_id = data.split(":", 2)
            peer_id = int(raw_peer_id)
        except (ValueError, TypeError):
            await query.edit_message_text("This takeover request is invalid.")
            return

        if action == "yes":
            if peer_id in self.userbot._sessions:
                result = f"✅ Takeover on {peer_id} is already active."
            elif not self.userbot.has_pending_takeover_request(peer_id):
                result = f"This takeover request for {peer_id} is no longer pending."
            else:
                self.userbot.begin_takeover(peer_id, self.default_persona)
                result = (
                    f"✅ Takeover started on {peer_id} "
                    f"as {self.default_persona}."
                )
        elif action == "no":
            if self.userbot.dismiss_takeover_request(peer_id):
                result = f"❌ Takeover request for {peer_id} dismissed."
            elif peer_id in self.userbot._sessions:
                result = f"Takeover on {peer_id} is already active and was not dismissed."
            else:
                result = f"This takeover request for {peer_id} is no longer pending."
        else:
            result = "This takeover request action is invalid."

        await query.edit_message_text(result)
        audit_event(
            "control_message",
            "takeover_request_button_completed",
            component="transport.control_bot",
            payload={"action": action, "result": result},
            peer_id=peer_id,
        )

    @staticmethod
    def _stop_markup(peer_id: int):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("Stop & seal", callback_data=f"hive_seal:request:{peer_id}")]]
        )

    @staticmethod
    def _seal_confirmation_markup(peer_id: int):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Confirm stop & seal",
                        callback_data=f"hive_seal:confirm:{peer_id}",
                    ),
                    InlineKeyboardButton(
                        "Keep running",
                        callback_data=f"hive_seal:cancel:{peer_id}",
                    ),
                ]
            ]
        )

    async def _callback_seal(self, update, context) -> None:
        query = update.callback_query
        user_id = update.effective_user.id if update.effective_user else None
        authorised = self._authorised(user_id)
        data = str(getattr(query, "data", "") or "")
        audit_event(
            "control_message",
            "stop_and_seal_button_pressed",
            component="transport.control_bot",
            payload={"operator_id": user_id, "callback_data": data, "authorised": authorised},
            level="info" if authorised else "warning",
        )
        if not authorised:
            await query.answer("Unauthorised.", show_alert=True)
            return
        await query.answer()
        try:
            _prefix, action, raw_peer_id = data.split(":", 2)
            peer_id = int(raw_peer_id)
        except (ValueError, TypeError):
            await query.edit_message_text("This stop-and-seal action is invalid.")
            return

        if action == "cancel":
            await query.edit_message_text(
                f"Takeover {peer_id} is still running.",
                reply_markup=self._stop_markup(peer_id),
            )
            return
        if peer_id not in self.userbot._sessions:
            await query.edit_message_text(f"No active takeover on {peer_id}.")
            return
        if action == "request":
            session = self.userbot._sessions[peer_id][0]
            await query.edit_message_text(
                (
                    f"Stop and seal takeover {peer_id}?\n\n"
                    f"Verdict: {session.verdict}\n"
                    f"Messages: {len(session.messages)}\n\n"
                    "HIVE will stop replying, archive the chat, generate the signed PDF, "
                    "and send it here."
                ),
                reply_markup=self._seal_confirmation_markup(peer_id),
            )
            return
        if action != "confirm":
            await query.edit_message_text("This stop-and-seal action is invalid.")
            return

        await query.edit_message_text(f"Sealing takeover {peer_id}…")
        try:
            sealed = await asyncio.to_thread(self.takeovers.seal, peer_id)
        except TakeoverNotFoundError:
            await query.edit_message_text(f"No active takeover on {peer_id}.")
            return
        except TakeoverBusyError:
            await query.edit_message_text(
                f"Takeover {peer_id} is already being sealed. Please wait."
            )
            return
        except TakeoverSealError:
            log.exception("control bot: seal failed; takeover retained peer=%s", peer_id)
            await query.edit_message_text(
                f"Could not seal {peer_id}. The takeover remains active; "
                "fix the report error and retry.",
                reply_markup=self._stop_markup(peer_id),
            )
            return

        await query.edit_message_text(
            f"✅ Takeover {peer_id} stopped and sealed.\n\n{sealed.summary}"
        )
        with sealed.path.open("rb") as stream:
            await query.message.reply_document(stream, filename=f"evidence_{peer_id}.pdf")
        audit_event(
            "control_message",
            "control_bot_document_sent",
            component="transport.control_bot",
            payload={"filename": f"evidence_{peer_id}.pdf", "path": str(sealed.path)},
            peer_id=peer_id,
            session_id=sealed.session.session_id,
        )
        log.info("control bot: stopped + sealed peer=%s", peer_id)

    async def start(self) -> None:  # pragma: no cover - needs live telegram
        from telegram import BotCommand
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            MessageHandler,
            filters,
        )

        self._app = Application.builder().token(self.settings.control_bot_token).build()
        self._app.add_handler(CommandHandler("start", self._cmd_help))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("chats", self._cmd_chats))
        self._app.add_handler(CommandHandler("takeovers", self._cmd_takeovers))
        self._app.add_handler(CommandHandler("takeover", self._cmd_takeover))
        self._app.add_handler(CommandHandler("persona", self._cmd_persona))
        self._app.add_handler(CommandHandler("stop", self._cmd_stop))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(
            CallbackQueryHandler(
                self._callback_takeover_request,
                pattern=r"^hive_takeover:(?:yes|no):-?\d+$",
            )
        )
        self._app.add_handler(
            CallbackQueryHandler(
                self._callback_seal,
                pattern=r"^hive_seal:(?:request|confirm|cancel):-?\d+$",
            )
        )
        self._app.add_handler(MessageHandler(filters.TEXT, self._cmd_fallback))
        await self._app.initialize()
        await self._app.bot.set_my_commands(
            [
                BotCommand("chats", "List recent incoming chats"),
                BotCommand("takeovers", "List active takeovers and controls"),
                BotCommand("takeover", "Start a takeover by peer ID"),
                BotCommand("persona", "Set the default persona"),
                BotCommand("status", "Show an active takeover"),
                BotCommand("stop", "Request stop-and-seal confirmation"),
                BotCommand("help", "Show available commands"),
            ]
        )
        await self._app.start()
        await self._app.updater.start_polling()
        await self.userbot.notify_pending_takeover_requests()
        log.info("control bot: polling started")

    async def stop(self) -> None:  # pragma: no cover - needs live telegram
        """Stop polling and release the Bot API application."""
        if self._app is None:
            return
        if self._app.updater.running:
            await self._app.updater.stop()
        if self._app.running:
            await self._app.stop()
        await self._app.shutdown()
        self._app = None
        log.info("control bot: stopped")

    # --- command handlers (thin wrappers around testable logic) ---

    async def _guard(self, update) -> bool:  # pragma: no cover
        uid = update.effective_user.id if update.effective_user else None
        authorised = self._authorised(uid)
        audit_event(
            "control_message",
            "control_bot_message_received",
            component="transport.control_bot",
            payload={
                "operator_id": uid,
                "text": str(getattr(update.message, "text", "") or ""),
                "authorised": authorised,
            },
            level="info" if authorised else "warning",
        )
        if not authorised:
            await self._reply_text(update, "Unauthorised.")
            log.warning("control bot: rejected unauthorised user=%s", uid)
            return False
        return True

    async def _peer_arg(self, update, context, usage: str) -> int | None:
        """Parse args[0] as an int peer id, or reply usage and return None."""
        if not context.args:
            await self._reply_text(update, usage)
            return None
        try:
            return int(context.args[0])
        except (ValueError, TypeError):
            await self._reply_text(update, usage)
            return None

    async def _cmd_help(self, update, context):  # pragma: no cover
        if not await self._guard(update):
            return
        await self._reply_text(update, HELP_TEXT)

    async def _cmd_fallback(self, update, context):  # pragma: no cover
        if not await self._guard(update):
            return
        await self._reply_text(update, "Use one of the commands below.\n\n" + HELP_TEXT)

    async def _cmd_chats(self, update, context):  # pragma: no cover
        if not await self._guard(update):
            return
        chats = self.userbot.list_observed_chats()
        if not chats:
            await self._reply_text(update, "No recent incoming private chats.")
            return
        lines = ["Recent incoming private chats:"]
        for chat in chats[:10]:
            account = str(chat["name"] or f"Peer {chat['peer_id']}")
            if chat["username"]:
                account += f" (@{chat['username']})"
            state = "active" if chat["active"] else "available"
            lines.append(f"{chat['peer_id']}: {account} [{state}]")
        await self._reply_text(update, "\n".join(lines))

    async def _cmd_takeovers(self, update, context):  # pragma: no cover
        if not await self._guard(update):
            return
        sessions = sorted(self.userbot._sessions.items())
        if not sessions:
            await self._reply_text(update, "No active takeovers.")
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        lines = ["Active takeovers:"]
        buttons = []
        for peer_id, (session, _chain) in sessions:
            lines.append(
                f"{peer_id} — {session.persona} — {session.verdict} "
                f"({len(session.messages)} messages)"
            )
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"Stop & seal {peer_id}",
                        callback_data=f"hive_seal:request:{peer_id}",
                    )
                ]
            )
        await self._reply_text(
            update,
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _cmd_takeover(self, update, context):  # pragma: no cover
        if not await self._guard(update):
            return
        peer = await self._peer_arg(update, context, "Usage: /takeover <peer_id> [persona]")
        if peer is None:
            return
        persona = context.args[1] if len(context.args) > 1 else self.default_persona
        if persona not in VALID_PERSONAS:
            await self._reply_text(
                update,
                f"Unknown persona '{persona}'. Choose from: {', '.join(sorted(VALID_PERSONAS))}"
            )
            return
        self.userbot.begin_takeover(peer, persona)
        await self._reply_text(update, f"Takeover started on {peer} as {persona}.")

    async def _cmd_persona(self, update, context):  # pragma: no cover
        if not await self._guard(update):
            return
        if not context.args:
            await self._reply_text(
                update,
                f"Usage: /persona <name>. Choose from: {', '.join(sorted(VALID_PERSONAS))}"
            )
            return
        name = context.args[0]
        if name not in VALID_PERSONAS:
            await self._reply_text(
                update,
                f"Unknown persona '{name}'. Choose from: {', '.join(sorted(VALID_PERSONAS))}"
            )
            return
        self.default_persona = name
        await self._reply_text(update, f"Default persona set to {self.default_persona}.")

    async def _cmd_status(self, update, context):  # pragma: no cover
        if not await self._guard(update):
            return
        peer = await self._peer_arg(update, context, "Usage: /status <peer_id>")
        if peer is None:
            return
        entry = self.userbot._sessions.get(peer)
        if entry is None:
            await self._reply_text(update, f"No active takeover on {peer}.")
            return
        await self._reply_text(
            update,
            self.engine.summary(entry[0]),
            reply_markup=self._stop_markup(peer),
        )

    async def _cmd_stop(self, update, context):  # pragma: no cover
        if not await self._guard(update):
            return
        peer = await self._peer_arg(update, context, "Usage: /stop <peer_id>")
        if peer is None:
            return
        entry = self.userbot._sessions.get(peer)
        if entry is None:
            await self._reply_text(update, f"No active takeover on {peer}.")
            return
        session = entry[0]
        await self._reply_text(
            update,
            (
                f"Stop and seal takeover {peer}?\n\n"
                f"Verdict: {session.verdict}\n"
                f"Messages: {len(session.messages)}\n\n"
                "HIVE will stop replying, archive the chat, generate the signed PDF, "
                "and send it here."
            ),
            reply_markup=self._seal_confirmation_markup(peer),
        )
