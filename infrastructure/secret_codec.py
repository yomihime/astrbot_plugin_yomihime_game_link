"""Versioned AES-GCM codec for SQLiteSecretStore payload files."""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .key_provider import KeyProvider, SecretKeyUnavailable

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
            return self._encrypt_payload(value, AESGCM(key))
        except Exception:
            raise ValueError("secret payload could not be encrypted") from None

    def decrypt(self, value: bytes) -> bytes:
        if type(value) is not bytes or not value.startswith(_MAGIC):
            raise ValueError("secret payload is unavailable") from None
        offset = len(_MAGIC)
        if len(value) < offset + _NONCE_BYTES + _TAG_BYTES:
            raise ValueError("secret payload is unavailable") from None
        try:
            return self._decrypt_payload(value, AESGCM(self._key()))
        except Exception:
            raise ValueError("secret payload is unavailable") from None

    @staticmethod
    def _encrypt_payload(value: bytes, cipher: AESGCM) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = cipher.encrypt(nonce, value, _AAD)
        if type(ciphertext) is not bytes or ciphertext == value:
            raise ValueError("secret payload could not be encrypted") from None
        return _MAGIC + nonce + ciphertext

    @staticmethod
    def _decrypt_payload(value: bytes, cipher: AESGCM) -> bytes:
        offset = len(_MAGIC)
        if (
            type(value) is not bytes
            or not value.startswith(_MAGIC)
            or len(value) < offset + _NONCE_BYTES + _TAG_BYTES
        ):
            raise ValueError("secret payload is unavailable") from None
        nonce = value[offset : offset + _NONCE_BYTES]
        ciphertext = value[offset + _NONCE_BYTES :]
        return cipher.decrypt(nonce, ciphertext, _AAD)

    def encryption_diagnostics(self) -> tuple[str, str]:
        """Classify one in-memory probe, never inspect stored payloads."""
        try:
            key = self._key_provider.get_key()
        except SecretKeyUnavailable as error:
            reason = error.reason
            if reason not in {
                "not_provided",
                "invalid_encoding_or_length",
                "cannot_safely_confirm",
            }:
                reason = "cannot_safely_confirm"
            return reason, "not_run"
        except Exception:
            return "cannot_safely_confirm", "not_run"
        if type(key) is not bytes or len(key) != 32:
            return "cannot_safely_confirm", "not_run"
        try:
            cipher = AESGCM(key)
        except Exception:
            return "valid_format", "component_unavailable"
        try:
            probe = b"yomihime:encryption-readiness:v1"
            encrypted = self._encrypt_payload(probe, cipher)
            if type(encrypted) is not bytes or encrypted == probe:
                return "valid_format", "self_test_failed"
            decrypted = self._decrypt_payload(encrypted, cipher)
            if type(decrypted) is not bytes or decrypted != probe:
                return "valid_format", "self_test_failed"
        except Exception:
            return "valid_format", "self_test_failed"
        return "valid_format", "available"

    def __repr__(self) -> str:
        return "AESGCMSecretCodec(<external key provider>)"


__all__ = ["AESGCMSecretCodec"]
