"""Neutral coordinator result and quiet-proof vocabulary.

Wire parsing lives in extensions.hot_update_protocol. These shapes contain no
SDK classes and are intentionally simple enough for a reloaded bootstrap.
"""

from __future__ import annotations

from typing import TypedDict


class QuietProof(TypedDict):
    owner_token: str
    tracked_inflight: int
    cleanup_complete: bool
    durable_fence: bool
    resources_closed: bool


class AcquireResult(TypedDict, total=False):
    state: str
    core_epoch: int
    owner_token: str
    cleanup_id: str
    error: str
