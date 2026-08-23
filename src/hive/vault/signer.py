"""RSA digital signatures over the evidence bundle (fyp.txt L5).

Uses RSA-PSS with SHA-256 via the `cryptography` package (lazy-imported so it
is only needed where signing happens). The private key is loaded from a path
OUTSIDE the repo and is the highest-value signing secret; the signature is only
as trustworthy as key custody (fyp.txt L5 caveat).

`generate_keypair` is provided for dev/test setup; in deployment the operator
supplies their own key.
"""

from __future__ import annotations

from hive.logging_setup import get_logger

log = get_logger(__name__)


def generate_keypair(private_path: str, public_path: str | None = None, bits: int = 2048) -> None:
    """Write a new RSA private (and optional public) key in PEM to disk."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    with open(private_path, "wb") as fh:
        fh.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    if public_path:
        with open(public_path, "wb") as fh:
            fh.write(
                key.public_key().public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )
    log.info("L5 signer: generated %d-bit keypair", bits)


def _pss_and_hash():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    pss = padding.PSS(
        mgf=padding.MGF1(hashes.SHA256()),
        salt_length=padding.PSS.MAX_LENGTH,
    )
    return pss, hashes.SHA256()


def sign_bytes(data: bytes, key_path: str) -> bytes:
    """Return an RSA-PSS/SHA-256 signature over `data`."""
    from cryptography.hazmat.primitives import serialization

    with open(key_path, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=None)
    pss, sha = _pss_and_hash()
    sig = key.sign(data, pss, sha)
    log.info("L5 signer: signed %d bytes -> %d-byte signature", len(data), len(sig))
    return sig


def verify_signature(data: bytes, signature: bytes, public_key_path: str) -> bool:
    """Verify an RSA-PSS/SHA-256 signature. Returns True/False (no raise)."""
    with open(public_key_path, "rb") as fh:
        public_key = fh.read()
    return verify_signature_with_public_key(data, signature, public_key)


def verify_signature_with_public_key(
    data: bytes,
    signature: bytes,
    public_key: bytes,
) -> bool:
    """Verify a signature using PEM public-key bytes."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization

    pub = serialization.load_pem_public_key(public_key)
    pss, sha = _pss_and_hash()
    try:
        pub.verify(signature, data, pss, sha)
        return True
    except InvalidSignature:
        return False


def public_key_bytes(private_key_path: str) -> bytes:
    """Derive SubjectPublicKeyInfo PEM bytes from an RSA private key."""
    from cryptography.hazmat.primitives import serialization

    with open(private_key_path, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=None)
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
