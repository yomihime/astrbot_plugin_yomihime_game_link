"""Versioned AES-GCM codec for SQLiteSecretStore payload files."""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .key_provider import KeyProvider

_MAGIC = b"YHSC\x01"
_NONCE_BYTES = 12
_TAG_BYTES = 16
_AAD = b"yomihime:secret-store:aesgcm:v1"


class AESGCMSecretCodec:
    """Encrypt each payload with a random nonce and an externally supplied key."""

    __slots__ = ("_key_provider",)

    def __init__(self, key_provider: KeyProvider) -> None:
        if not callable(getattr(key_provider, "get_key", None)):
            raise TypeError("secret key provider must implement get_key")
        self._key_provider = key_provider

    def _key(self) -> bytes:
        try:
            key = self._key_provider.get_key()
        except Exception:
            raise ValueError("secret encryption key is unavailable") from None
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("secret encryption key is unavailable")
        return key

    def encrypt(self, value: bytes) -> bytes:
        if type(value) is not bytes:
            raise TypeError("secret payload must be bytes")
        key = self._key()
        try:
            nonce = os.urandom(_NONCE_BYTES)
            ciphertext = AESGCM(key).encrypt(nonce, value, _AAD)
            return _MAGIC + nonce + ciphertext
        except Exception:
            raise ValueError("secret payload could not be encrypted") from None

    def decrypt(self, value: bytes) -> bytes:
        if type(value) is not bytes or not value.startswith(_MAGIC):
            raise ValueError("secret payload is unavailable") from None
        offset = len(_MAGIC)
        if len(value) < offset + _NONCE_BYTES + _TAG_BYTES:
            raise ValueError("secret payload is unavailable") from None
        try:
            nonce = value[offset : offset + _NONCE_BYTES]
            ciphertext = value[offset + _NONCE_BYTES :]
            return AESGCM(self._key()).decrypt(nonce, ciphertext, _AAD)
        except Exception:
            raise ValueError("secret payload is unavailable") from None

    def __repr__(self) -> str:
        return "AESGCMSecretCodec(<external key provider>)"


__all__ = ["AESGCMSecretCodec"]
