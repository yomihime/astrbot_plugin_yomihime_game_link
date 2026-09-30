"""External key sources for encrypted Core secret payloads."""

from __future__ import annotations

import base64
import binascii
import os
import re
from typing import Protocol


class SecretKeyUnavailable(RuntimeError):
    """A deployment key is missing or invalid; plaintext fallback is forbidden."""

    def __init__(self) -> None:
        super().__init__("secret encryption key is unavailable")


class KeyProvider(Protocol):
    """Provide one external 256-bit key without persisting it with ciphertext."""

    def get_key(self) -> bytes: ...


class EnvironmentKeyProvider:
    """Read a base64-encoded AES-256 key from one explicitly named env var."""

    __slots__ = ("_variable_name",)

    def __init__(self, variable_name: str) -> None:
        if type(variable_name) is not str or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]{0,127}", variable_name
        ):
            raise ValueError("secret key environment variable name is invalid")
        self._variable_name = variable_name

    def get_key(self) -> bytes:
        encoded = os.environ.get(self._variable_name)
        if not encoded:
            raise SecretKeyUnavailable() from None
        try:
            key = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise SecretKeyUnavailable() from None
        if len(key) != 32:
            raise SecretKeyUnavailable() from None
        return key

    def __repr__(self) -> str:
        return f"EnvironmentKeyProvider(variable_name={self._variable_name!r})"


__all__ = ["EnvironmentKeyProvider", "KeyProvider", "SecretKeyUnavailable"]
