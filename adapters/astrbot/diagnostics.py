"""Forward only Core's safe generic diagnostic envelope to the platform logger."""

from __future__ import annotations

import json
import logging

from ...infrastructure.sync_diagnostics import related_auth_failure


def forward_diagnostic(event, *, logger=None, warning=False):
    if type(event) is not dict or set(event) != {
        "owner",
        "epoch",
        "trace",
        "event_id",
        "payload",
    }:
        raise ValueError("invalid diagnostic envelope")
    if (
        type(event["epoch"]) is not int
        or event["epoch"] < 1
        or type(event["payload"]) is not dict
    ):
        raise ValueError("invalid diagnostic envelope")
    if any(
        type(event[key]) is not str or not event[key] or len(event[key]) > 256
        for key in ("owner", "trace", "event_id")
    ):
        raise ValueError("invalid diagnostic identity")
    entry = event
    if warning:
        auth_failure = related_auth_failure(event["trace"])
        if auth_failure is not None:
            entry = {**event, "authorization_failure": auth_failure}
    wire = json.dumps(entry, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    if len(wire.encode("ascii")) > 65536:
        raise ValueError("diagnostic envelope limit")
    if logger is None:
        from astrbot.api import logger
    emit = logger.warning if warning else logger.debug
    emit("GAME_LINK_DIAGNOSTIC " + wire)


def forward_sync_failure(event, *, logger=None):
    """Receive the finite Core failure projection, never a raw trace."""
    if logger is None:
        from astrbot.api import logger
    wire = json.dumps(event, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    logger.warning("GAME_LINK_FAILURE " + wire)


def sync_trace_enabled(*, logger=None):
    if logger is None:
        from astrbot.api import logger
    return logger.isEnabledFor(logging.DEBUG)


def forward_sync_trace(event, *, logger=None):
    """Forward Core's bounded public terminal metadata only at DEBUG."""
    if logger is None:
        from astrbot.api import logger
    if not sync_trace_enabled(logger=logger):
        return
    wire = json.dumps(event, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    if len(wire.encode("ascii")) > 65536:
        raise ValueError("diagnostic trace limit")
    logger.debug("GAME_LINK_TRACE " + wire)
