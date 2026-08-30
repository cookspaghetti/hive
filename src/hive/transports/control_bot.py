"""Bot API control plane (fyp.txt Platform).

A separate @Bot the operator chats with to drive the system. Every command is
gated to HIVE_OPERATOR_ID. Commands:
    /start, /help                 show the command reference
    /chats                        list recent private incoming chats
    /takeovers                    list active takeovers and open their controls

Requires a live Bot API connection, so not unit-tested.
"""

from __future__ import annotations

import asyncio
from typing import Any

from hive.agent.personas import PERSONAS
from hive.audit import audit_event
from hive.config import Settings
from hive.history import HistoryStore, build_history_store
from hive.logging_setup import get_logger
from hive.runtime import HiveEngine
from hive.state import SessionState
from hive.takeover import (
    TakeoverBusyError,
    TakeoverCoordinator,
    TakeoverNotFoundError,
    TakeoverSealError,
)
from hive.transports.userbot import UserbotTransport
from hive.vault.package import evidence_package_path

log = get_logger(__name__)

VALID_PERSONAS = set(PERSONAS)
HELP_TEXT = (
    "HIVE control bot\n\n"
    "/chats - list recent incoming private chats\n"
    "/takeovers - inspect active takeovers, change persona, or stop and seal\n"
    "New takeover requests are sent here automatically.\n"
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
        self._app: Any = None
        self._takeover_request_message_ids: dict[int, int] = {}
        # Get notified when the userbot hands a benign conversation back.
        self.userbot.on_handback = self._on_handback
        self.userbot.on_limit_reached = self._on_limit_reached
        self.userbot.on_takeover_request = self._on_takeover_request

    def _authorised(self, user_id: int | None) -> bool:
        return user_id == self.operator_id

    async def _reply_text(
        self,
        update: Any,
        text: str,
        *,
        reply_markup: Any = None,
    ) -> None:
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

    async def _on_handback(
        self,
        peer_id: int,
        session: SessionState,
    ) -> None:  # pragma: no cover
        """Fired by the userbot when a benign conversation is handed back.

        No evidence bundle is sealed (the verdict is benign); we just inform the
        operator that control has returned to them for this chat.
        """
        try:
            self.history.archive(session, status="handed_back")
        except Exception:
            log.exception("control bot: hand-back history archive failed peer=%s", peer_id)
            audit_event(
                "takeover_history",
                "handback_archive_failed",
                component="transport.control_bot",
                peer_id=peer_id,
                session_id=session.session_id,
                level="error",
            )
        display_name = session.peer_display_name or f"Peer {peer_id}"
        account = (
            f"{display_name} (@{session.peer_username})"
            if session.peer_username
            else display_name
        )
        exchanges = getattr(session, "exchange_count", session.turn_count)
        text = (
            "↩️ HIVE handed the chat back\n\n"
            f"Chat: {account}\n"
            f"Peer ID: {peer_id}\n"
            f"Decision: {session.verdict.replace('_', ' ').title()}\n"
            f"Analysed exchanges: {exchanges}\n"
            f"Messages observed: {session.turn_count}\n\n"
            "HIVE has stopped replying. You can continue the conversation manually "
            "in Telegram.\n\n"
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

    async def _on_limit_reached(
        self,
        peer_id: int,
        session: SessionState,
        reason: str,
    ) -> None:  # pragma: no cover
        """Tell the operator that automation paused without discarding evidence."""
        reason_label = reason.replace("_", " ").title()
        text = (
            "⏸️ HIVE paused automatic replies\n\n"
            f"Peer ID: {peer_id}\n"
            f"Reason: {reason_label}\n"
            f"Analysed exchanges: {session.exchange_count}\n"
            f"Messages observed: {session.turn_count}\n\n"
            "The takeover remains open and checkpointed. Review the case, then "
            "use Stop & seal when you are ready. HIVE will not send more replies "
            "while this safety pause is active."
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
        log.info("control bot: notified operator of automation pause peer=%s", peer_id)

    async def _on_takeover_request(self, chat: dict[str, object]) -> bool:
        """Push a pending private chat to the operator's Telegram control bot."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        if self._app is None:
            return False
        peer_id = int(str(chat["peer_id"]))
        display_name = str(chat.get("name") or f"Peer {peer_id}")
        username = str(chat.get("username") or "")
        account = f"{display_name} (@{username})" if username else display_name
        preview = str(chat.get("last_message") or "[media or empty message]")
        text = (
            "🚨 HIVE takeover request\n\n"
            f"From: {account}\n"
            f"Peer ID: {peer_id}\n"
            f"Messages waiting: {int(str(chat.get('message_count') or 1))}\n"
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
        message_id = self._takeover_request_message_ids.get(peer_id)
        delivery = "updated" if message_id is not None else "sent"
        if message_id is not None:
            try:
                await self._app.bot.edit_message_text(
                    chat_id=self.operator_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=reply_markup,
                )
            except Exception:
                log.warning(
                    "control bot: could not update request card; sending replacement peer=%s",
                    peer_id,
                )
                sent = await self._app.bot.send_message(
                    chat_id=self.operator_id,
                    text=text,
                    reply_markup=reply_markup,
                )
                if getattr(sent, "message_id", None) is not None:
                    self._takeover_request_message_ids[peer_id] = int(sent.message_id)
                delivery = "replaced"
        else:
            sent = await self._app.bot.send_message(
                chat_id=self.operator_id,
                text=text,
                reply_markup=reply_markup,
            )
            if getattr(sent, "message_id", None) is not None:
                self._takeover_request_message_ids[peer_id] = int(sent.message_id)
        audit_event(
            "control_message",
            f"takeover_request_{delivery}_in_control_bot",
            component="transport.control_bot",
            payload={"chat_id": self.operator_id, "text": text, "delivery": delivery},
            peer_id=peer_id,
        )
        return True

    async def _callback_takeover_request(self, update: Any, context: Any) -> None:
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
                await self.userbot.process_pending_takeover(peer_id)
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
        self._takeover_request_message_ids.pop(peer_id, None)
        audit_event(
            "control_message",
            "takeover_request_button_completed",
            component="transport.control_bot",
            payload={"action": action, "result": result},
            peer_id=peer_id,
        )

    @staticmethod
    def _persona_label(persona: str) -> str:
        return persona.replace("_", " ").title()

    def _takeovers_list_markup(self) -> Any:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        buttons = []
        for peer_id, (session, _chain) in sorted(self.userbot._sessions.items()):
            buttons.append(
                [
                    InlineKeyboardButton(
                        (
                            f"{session.peer_display_name or peer_id} · "
                            f"{self._persona_label(session.persona)} · "
                            f"{session.verdict.replace('_', ' ').title()}"
                        ),
                        callback_data=f"hive_takeovers:show:{peer_id}",
                    )
                ]
            )
        return InlineKeyboardMarkup(buttons)

    def _takeover_controls_markup(self, peer_id: int) -> Any:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Change persona",
                        callback_data=f"hive_takeovers:persona:{peer_id}",
                    ),
                    InlineKeyboardButton(
                        "Stop & seal",
                        callback_data=f"hive_seal:request:{peer_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "← Active takeovers",
                        callback_data="hive_takeovers:list:0",
                    )
                ],
            ]
        )

    def _takeover_persona_markup(self, peer_id: int, current: str) -> Any:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        buttons: list[list[Any]] = []
        row: list[Any] = []
        for persona in sorted(VALID_PERSONAS):
            label = self._persona_label(persona)
            if persona == current:
                label = f"✓ {label}"
            row.append(
                InlineKeyboardButton(
                    label,
                    callback_data=f"hive_takeover_persona:{peer_id}:{persona}",
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append(
            [
                InlineKeyboardButton(
                    "← Takeover details",
                    callback_data=f"hive_takeovers:show:{peer_id}",
                )
            ]
        )
        return InlineKeyboardMarkup(buttons)

    def _takeover_detail(self, peer_id: int, session: SessionState) -> str:
        account = session.peer_display_name or f"Peer {peer_id}"
        if session.peer_username:
            account = f"{account} (@{session.peer_username})"
        messages = list(session.messages)[-10:]
        lines = [
            "🛡️ Active HIVE takeover",
            "",
            f"Chat: {account}",
            f"Peer ID: {peer_id}",
            f"Persona: {self._persona_label(session.persona)}",
            f"Verdict: {session.verdict.replace('_', ' ').title()}",
            f"Risk: {float(session.verdict_score):.2f}",
            f"Messages: {len(session.messages)}",
            "",
            "Recent messages (latest 10):",
        ]
        if not messages:
            lines.append("No messages recorded yet.")
        for message in messages:
            speaker = "HIVE" if message.role == "agent" else "Stranger"
            content = (message.text or f"[{message.media_kind or 'media'}]").strip()
            if len(content) > 240:
                content = content[:237] + "…"
            lines.append(f"{speaker}: {content}")
        return "\n".join(lines)

    async def _callback_takeovers(self, update: Any, context: Any) -> None:
        query = update.callback_query
        user_id = update.effective_user.id if update.effective_user else None
        authorised = self._authorised(user_id)
        data = str(getattr(query, "data", "") or "")
        audit_event(
            "control_message",
            "takeovers_button_pressed",
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
            if action not in {"list", "show", "persona"}:
                raise ValueError
        except (ValueError, TypeError):
            await query.edit_message_text("This takeover action is invalid.")
            return

        if action == "list":
            if not self.userbot._sessions:
                await query.edit_message_text("No active takeovers.")
            else:
                await query.edit_message_text(
                    "Choose an active takeover:",
                    reply_markup=self._takeovers_list_markup(),
                )
            return
        entry = self.userbot._sessions.get(peer_id)
        if entry is None:
            await query.edit_message_text(
                f"No active takeover on {peer_id}.",
                reply_markup=(
                    self._takeovers_list_markup() if self.userbot._sessions else None
                ),
            )
            return
        session = entry[0]
        if action == "persona":
            await query.edit_message_text(
                f"Choose the persona for takeover {peer_id}:",
                reply_markup=self._takeover_persona_markup(
                    peer_id,
                    session.persona,
                ),
            )
            return
        await query.edit_message_text(
            self._takeover_detail(peer_id, session),
            reply_markup=self._takeover_controls_markup(peer_id),
        )

    async def _callback_takeover_persona(self, update: Any, context: Any) -> None:
        query = update.callback_query
        user_id = update.effective_user.id if update.effective_user else None
        authorised = self._authorised(user_id)
        data = str(getattr(query, "data", "") or "")
        audit_event(
            "control_message",
            "takeover_persona_button_pressed",
            component="transport.control_bot",
            payload={"operator_id": user_id, "callback_data": data, "authorised": authorised},
            level="info" if authorised else "warning",
        )
        if not authorised:
            await query.answer("Unauthorised.", show_alert=True)
            return
        await query.answer()
        try:
            _prefix, raw_peer_id, persona = data.split(":", 2)
            peer_id = int(raw_peer_id)
        except (ValueError, TypeError):
            await query.edit_message_text("This persona action is invalid.")
            return

        if persona not in VALID_PERSONAS:
            await query.edit_message_text("This persona action is invalid.")
            return
        entry = self.userbot._sessions.get(peer_id)
        if entry is None:
            await query.edit_message_text(f"No active takeover on {peer_id}.")
            return
        previous = entry[0].persona
        self.userbot.update_persona(peer_id, persona)
        await query.edit_message_text(
            self._takeover_detail(peer_id, entry[0]),
            reply_markup=self._takeover_controls_markup(peer_id),
        )
        audit_event(
            "control_message",
            "takeover_persona_changed",
            component="transport.control_bot",
            payload={"previous_persona": previous, "persona": persona},
            peer_id=peer_id,
            session_id=entry[0].session_id,
        )

    @staticmethod
    def _seal_confirmation_markup(peer_id: int) -> Any:
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

    async def _callback_seal(self, update: Any, context: Any) -> None:
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
            entry = self.userbot._sessions.get(peer_id)
            if entry is None:
                await query.edit_message_text(f"No active takeover on {peer_id}.")
            else:
                await query.edit_message_text(
                    self._takeover_detail(peer_id, entry[0]),
                    reply_markup=self._takeover_controls_markup(peer_id),
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
                    "HIVE will stop replying, archive the chat, generate the signed PDF "
                    "and portable verification package, then send them here."
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
                reply_markup=self._takeover_controls_markup(peer_id),
            )
            return

        await query.edit_message_text(
            f"✅ Takeover {peer_id} stopped and sealed.\n\n{sealed.summary}",
            reply_markup=(
                self._takeovers_list_markup() if self.userbot._sessions else None
            ),
        )
        with sealed.path.open("rb") as stream:
            await query.message.reply_document(stream, filename=f"evidence_{peer_id}.pdf")
        package = evidence_package_path(sealed.path)
        if package.is_file():
            with package.open("rb") as stream:
                await query.message.reply_document(
                    stream,
                    filename=f"evidence_{peer_id}.evidence.zip",
                )
        audit_event(
            "control_message",
            "control_bot_document_sent",
            component="transport.control_bot",
            payload={
                "filename": f"evidence_{peer_id}.pdf",
                "path": str(sealed.path),
                "package": str(package) if package.is_file() else None,
            },
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
        self._app.add_handler(
            CallbackQueryHandler(
                self._callback_takeover_request,
                pattern=r"^hive_takeover:(?:yes|no):-?\d+$",
            )
        )
        self._app.add_handler(
            CallbackQueryHandler(
                self._callback_takeovers,
                pattern=r"^hive_takeovers:(?:list|show|persona):-?\d+$",
            )
        )
        self._app.add_handler(
            CallbackQueryHandler(
                self._callback_takeover_persona,
                pattern=r"^hive_takeover_persona:-?\d+:[a-z_]+$",
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
                BotCommand("takeovers", "Inspect and manage active takeovers"),
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

    async def _guard(self, update: Any) -> bool:  # pragma: no cover
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

    async def _cmd_help(self, update: Any, context: Any) -> None:  # pragma: no cover
        if not await self._guard(update):
            return
        await self._reply_text(update, HELP_TEXT)

    async def _cmd_fallback(self, update: Any, context: Any) -> None:  # pragma: no cover
        if not await self._guard(update):
            return
        await self._reply_text(update, "Use one of the commands below.\n\n" + HELP_TEXT)

    async def _cmd_chats(self, update: Any, context: Any) -> None:  # pragma: no cover
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

    async def _cmd_takeovers(self, update: Any, context: Any) -> None:  # pragma: no cover
        if not await self._guard(update):
            return
        if not self.userbot._sessions:
            await self._reply_text(update, "No active takeovers.")
            return
        await self._reply_text(
            update,
            "Choose an active takeover:",
            reply_markup=self._takeovers_list_markup(),
        )
