"""Encrypted-at-rest storage for the Telethon session file (fyp.txt S7).

Threat model: if the honeypot host is compromised, this file is the crown-jewel
target. It is encrypted with a passphrase-derived key and decrypted only into
the local agent process for the duration of a session.
"""

from __future__ import annotations


def load_session(encrypted_path: str, passphrase: str) -> bytes:
    """Decrypt and return the Telethon session bytes.

    TODO(S7): derive key (scrypt/argon2) from passphrase; decrypt with
    AES-GCM via `cryptography`; never write plaintext to disk.
    """
    raise NotImplementedError


def save_session(session_bytes: bytes, encrypted_path: str, passphrase: str) -> None:
    """Encrypt and persist the Telethon session bytes."""
    raise NotImplementedError
