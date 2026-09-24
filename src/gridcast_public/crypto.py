from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CheckpointCryptoError(Exception):
    """A fixed, deliberately detail-free checkpoint crypto error."""


def load_key(value: str | None = None) -> bytes:
    raw = value if value is not None else os.getenv("GRIDCAST_CHECKPOINT_KEY")
    if not raw:
        raise CheckpointCryptoError("checkpoint key unavailable")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise CheckpointCryptoError("checkpoint key invalid") from exc
    if len(key) != 32:
        raise CheckpointCryptoError("checkpoint key invalid")
    return key


def encrypt(key: bytes, request_id: str, version: int, payload: bytes) -> tuple[bytes, bytes]:
    nonce = os.urandom(12)
    aad = f"gridcast-checkpoint:{request_id}:{version}:1".encode()
    return nonce, AESGCM(key).encrypt(nonce, payload, aad)


def decrypt(key: bytes, request_id: str, version: int, nonce: bytes, ciphertext: bytes) -> bytes:
    aad = f"gridcast-checkpoint:{request_id}:{version}:1".encode()
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except Exception as exc:
        raise CheckpointCryptoError("checkpoint cannot be decrypted") from exc
