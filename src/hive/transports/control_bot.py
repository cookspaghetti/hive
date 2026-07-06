"""Bot API control plane (fyp.txt Platform).

A separate @Bot the operator chats with to configure and trigger the system:
/takeover, /persona, /stop (kill switch), and to receive the session summary
and signed evidence bundle. Only HIVE_OPERATOR_ID is authorised.
"""

from __future__ import annotations


class ControlBot:
    def __init__(self, token: str, operator_id: int) -> None:
        self.token = token
        self.operator_id = operator_id
        # TODO: build python-telegram-bot Application; register command handlers.

    async def start(self) -> None:
        raise NotImplementedError

    async def send_summary(self, text: str, bundle_path: str | None = None) -> None:
        raise NotImplementedError
