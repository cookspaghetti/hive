"""One-time bootstrap: create the encrypted Telethon session + signing key.

Run on the HOST (interactive Telegram login), then the produced files live under
./secrets and are mounted into the container at runtime.

    uv run python scripts/bootstrap_session.py

Reads Telegram creds + paths from your .env (HIVE_TG_API_ID / HASH / PHONE,
HIVE_SESSION_PASSPHRASE, HIVE_TG_SESSION_PATH, HIVE_SIGNING_KEY_PATH). Prompts
for the login code (and 2FA password if set). Nothing secret is printed.
"""

from __future__ import annotations

import os
import sys

from hive.config import load_settings
from hive.security.session_store import save_session
from hive.vault.signer import generate_keypair


def main() -> None:
    s = load_settings()

    missing = [
        name
        for name, val in [
            ("HIVE_TG_API_ID", s.tg_api_id),
            ("HIVE_TG_API_HASH", s.tg_api_hash),
            ("HIVE_TG_PHONE", s.tg_phone),
            ("HIVE_SESSION_PASSPHRASE", s.session_passphrase),
        ]
        if not val
    ]
    if missing:
        sys.exit(f"Missing required .env values: {', '.join(missing)}")

    os.makedirs(os.path.dirname(s.tg_session_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(s.signing_key_path) or ".", exist_ok=True)

    # 1) RSA signing key for the evidence vault (dev convenience).
    if os.path.exists(s.signing_key_path):
        print(f"[bootstrap] signing key already exists: {s.signing_key_path}")
    else:
        generate_keypair(s.signing_key_path, s.signing_key_path + ".pub")
        print(f"[bootstrap] generated signing key: {s.signing_key_path}")

    # 2) Telethon login -> encrypted StringSession.
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    print("[bootstrap] logging in to Telegram (interactive)...")
    with TelegramClient(StringSession(), s.tg_api_id, s.tg_api_hash) as client:
        client.start(phone=s.tg_phone)
        session_str = client.session.save()

    save_session(session_str, s.tg_session_path, s.session_passphrase)
    print(f"[bootstrap] encrypted session written: {s.tg_session_path}")
    print("[bootstrap] done. Keep ./secrets private; it is git-ignored.")


if __name__ == "__main__":
    main()
