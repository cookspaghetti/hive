"""RSA digital signature over the completed bundle (fyp.txt L5).

The private key is loaded from a path outside the repo and is the highest-value
signing secret (see security/). The signature is only as trustworthy as key
custody (fyp.txt L5 caveat).
"""

from __future__ import annotations


def sign_bytes(data: bytes, key_path: str) -> bytes:
    """Return an RSA signature over `data`.

    TODO(L5): load PEM private key via cryptography; PSS/SHA-256 sign.
    """
    raise NotImplementedError


def verify_signature(data: bytes, signature: bytes, public_key_path: str) -> bool:
    raise NotImplementedError
