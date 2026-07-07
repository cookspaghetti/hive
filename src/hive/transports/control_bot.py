"""Bot API control plane (fyp.txt Platform).

A separate @Bot the operator chats with to drive the system. Only
HIVE_OPERATOR_ID is authorised. Commands:
    /takeover <peer> [persona]  begin a takeover on the userbot
    /persona <name>             set default persona
    /stop <peer>                kill switch: reclaim the conversation now
    /status <peer>              current verdict + summary
On session end, delivers the summary and the signed evidence bundle.

Runtime (Telegram) behaviour is not unit-tested here.
"""

from __future__ import annotations

from hive.logging_setup import get_logger
from hive.runtime import HiveEngine
from hive.transports.userbot import UserbotTransport

log = get_logger(__name__)


class ControlBot:
    def __init__(self, token: str, operator_id: int, engine: HiveEngine, userbot: UserbotTransport) -> None:
        self.token = token
        self.operator_id = operator_id
        self.engine = engine
        self.userbot = userbot
        self._app = None  # python-telegram-bot Application, built in start()

    def _authorised(self, sender_id: int) -> bool:
        return sender_id == self.operator_id

    async def start(self) -> None:
        """Build the Application and register command handlers.

        TODO(build): register /takeover /persona /stop /status; guard every
        handler with _authorised(update.effective_user.id).
        """
        raise NotImplementedError

    async def cmd_takeover(self, peer_id: int, persona: str) -> str:
        self.userbot.begin_takeover(peer_id, persona)
        return f"Takeover started on {peer_id} as {persona}."

    async def cmd_stop(self, peer_id: int, bundle_path: str, key_path: str, operator_name: str) -> str:
        """Kill switch: end takeover, seal evidence, return summary."""
        entry = self.userbot.end_takeover(peer_id)
        if entry is None:
            return f"No active takeover on {peer_id}."
        session, chain = entry
        self.engine.close_session(session, chain, bundle_path, key_path, operator_name)
        summary = self.engine.summary(session)
        log.info("control: stopped + sealed peer=%d", peer_id)
        return summary

    async def send_summary(self, text: str, bundle_path: str | None = None) -> None:
        """Deliver the summary (and attach the evidence PDF) to the operator.

        TODO(build): self._app.bot.send_message / send_document.
        """
        raise NotImplementedError
