"""Forward only Core's safe generic diagnostic envelope to the platform logger."""

from __future__ import annotations

import json


def forward_diagnostic(event, *, logger=None):
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
    wire = json.dumps(event, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    if len(wire.encode("ascii")) > 65536:
        raise ValueError("diagnostic envelope limit")
    if logger is None:
        from astrbot.api import logger
    logger.debug("GAME_LINK_DIAGNOSTIC " + wire)
