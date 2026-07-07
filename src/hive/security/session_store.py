"""Encrypted-at-rest storage for the Telethon session (fyp.txt S7).

The Telethon session (a StringSession) is a FULL credential to the user's
entire Telegram account — the highest-value secret in the system. It is stored
encrypted with AES-256-GCM under a key derived from the operator passphrase via
scrypt. Plaintext is only ever held in memory for the life of a session.

File format (all binary, concatenated):
    magic(4) | scrypt_salt(16) | nonce(12) | ciphertext(+16 GCM tag)

Threat model: if the honeypot host is compromised, this file is the crown-jewel
target; without the passphrase it yields nothing.
"""

from __future__ import annotations

import os

from hive.logging_setup import get_logger

log = get_logger(__name__)

_MAGIC = b"HIVE"
_SALT_LEN = 16
_NONCE_LEN = 12
# scrypt work factors (interactive-strong).
_N, _R, _P = 2**15, 8, 1
_KEY_LEN = 32


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=_N, r=_R, p=_P)
    return kdf.derive(passphrase.encode("utf-8"))


def save_session(session_str: str, encrypted_path: str, passphrase: str) -> None:
    """Encrypt a Telethon StringSession and write it to disk."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not passphrase:
        raise ValueError("a non-empty passphrase is required to protect the session")
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = _derive_key(passphrase, salt)
    ct = AESGCM(key).encrypt(nonce, session_str.encode("utf-8"), _MAGIC)
    with open(encrypted_path, "wb") as fh:
        fh.write(_MAGIC + salt + nonce + ct)
    os.chmod(encrypted_path, 0o600)
    log.info("S7 session store: wrote encrypted session (%d bytes)", len(ct))


def load_session(encrypted_path: str, passphrase: str) -> str:
    """Decrypt and return the Telethon StringSession. Raises on bad passphrase."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    with open(encrypted_path, "rb") as fh:
        blob = fh.read()
    if blob[:4] != _MAGIC:
        raise ValueError("not a HIVE session file")
    salt = blob[4 : 4 + _SALT_LEN]
    nonce = blob[4 + _SALT_LEN : 4 + _SALT_LEN + _NONCE_LEN]
    ct = blob[4 + _SALT_LEN + _NONCE_LEN :]
    key = _derive_key(passphrase, salt)
    try:
        pt = AESGCM(key).decrypt(nonce, ct, _MAGIC)
    except InvalidTag as exc:
        raise ValueError("wrong passphrase or corrupted session file") from exc
    log.info("S7 session store: decrypted session into memory")
    return pt.decode("utf-8")
