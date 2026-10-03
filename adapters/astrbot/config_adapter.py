"""Copy AstrBot's injected config into the reviewed bundled module snapshot."""

from __future__ import annotations

from collections.abc import Mapping

from ...modules.ff14.config import (
    ORDINARY_FIELDS,
    FF14ConfigError,
    FF14ConfigSnapshot,
)


def ff14_config_snapshot(config: Mapping[str, object] | None) -> FF14ConfigSnapshot:
    """Accept missing legacy fields; reject invalid or undeclared host inputs."""
    if config is None:
        config = {}
    if isinstance(config, Mapping):
        config = {
            key: value for key, value in config.items() if key != "web_public_origin"
        }
    if not isinstance(config, Mapping) or set(config) - ORDINARY_FIELDS:
        raise FF14ConfigError("ordinary_config")
    return FF14ConfigSnapshot.from_values(config)


__all__ = ["ff14_config_snapshot"]
