"""Short-lived Telethon login state for the localhost setup wizard."""

from __future__ import annotations

import inspect
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hive.provisioning.env_store import EnvStore
from hive.security.session_store import save_session


@dataclass
class _Attempt:
    client: Any
    api_id: int
    api_hash: str
    phone: str
    passphrase: str
    session_path: str
    env_session_path: str
    phone_code_hash: str


class TelethonLoginManager:
    """Drive Telegram code and 2FA login without exposing a StringSession."""

    def __init__(
        self,
        env_store: EnvStore,
        client_factory: Callable[[int, str], Any] | None = None,
    ) -> None:
        self.env_store = env_store
        self._client_factory = client_factory or _default_client
        self._attempts: dict[str, _Attempt] = {}

    async def start(
        self,
        *,
        api_id: int,
        api_hash: str,
        phone: str,
        passphrase: str,
        session_path: str,
        env_session_path: str | None = None,
    ) -> dict[str, str]:
        if api_id <= 0 or not api_hash.strip() or not phone.strip():
            raise ValueError("API ID, API hash, and phone are required")
        if len(passphrase) < 12:
            raise ValueError("session passphrase must be at least 12 characters")

        client = self._client_factory(api_id, api_hash.strip())
        try:
            await client.connect()
            sent = await client.send_code_request(phone.strip())
        except Exception as exc:
            await _disconnect(client)
            raise ValueError("Telegram could not send a login code") from exc

        attempt_id = secrets.token_urlsafe(24)
        self._attempts[attempt_id] = _Attempt(
            client=client,
            api_id=api_id,
            api_hash=api_hash.strip(),
            phone=phone.strip(),
            passphrase=passphrase,
            session_path=session_path,
            env_session_path=env_session_path or session_path,
            phone_code_hash=sent.phone_code_hash,
        )
        return {"attempt_id": attempt_id, "state": "code_required"}

    async def submit_code(self, attempt_id: str, code: str) -> dict[str, str]:
        attempt = self._get(attempt_id)
        if not code.strip():
            raise ValueError("login code is required")
        try:
            await attempt.client.sign_in(
                phone=attempt.phone,
                code=code.strip(),
                phone_code_hash=attempt.phone_code_hash,
            )
        except Exception as exc:
            if exc.__class__.__name__ == "SessionPasswordNeededError":
                return {"state": "password_required"}
            raise ValueError("Telegram rejected the login code") from exc
        await self._finish(attempt_id, attempt)
        return {"state": "ready"}

    async def submit_password(self, attempt_id: str, password: str) -> dict[str, str]:
        attempt = self._get(attempt_id)
        if not password:
            raise ValueError("2FA password is required")
        try:
            await attempt.client.sign_in(password=password)
        except Exception as exc:
            raise ValueError("Telegram rejected the 2FA password") from exc
        await self._finish(attempt_id, attempt)
        return {"state": "ready"}

    async def cancel(self, attempt_id: str) -> None:
        attempt = self._attempts.pop(attempt_id, None)
        if attempt is not None:
            await _disconnect(attempt.client)

    async def close(self) -> None:
        for attempt_id in list(self._attempts):
            await self.cancel(attempt_id)

    def _get(self, attempt_id: str) -> _Attempt:
        attempt = self._attempts.get(attempt_id)
        if attempt is None:
            raise ValueError("login attempt expired or was not found")
        return attempt

    async def _finish(self, attempt_id: str, attempt: _Attempt) -> None:
        try:
            session_string = attempt.client.session.save()
            path = Path(attempt.session_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            save_session(session_string, str(path), attempt.passphrase)
            self.env_store.save(
                {
                    "HIVE_TG_API_ID": attempt.api_id,
                    "HIVE_TG_API_HASH": attempt.api_hash,
                    "HIVE_TG_PHONE": attempt.phone,
                    "HIVE_TG_SESSION_PATH": attempt.env_session_path,
                    "HIVE_SESSION_PASSPHRASE": attempt.passphrase,
                }
            )
        finally:
            self._attempts.pop(attempt_id, None)
            await _disconnect(attempt.client)


def _default_client(api_id: int, api_hash: str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    return TelegramClient(StringSession(), api_id, api_hash)


async def _disconnect(client: Any) -> None:
    result = client.disconnect()
    if inspect.isawaitable(result):
        await result
