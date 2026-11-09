"""Stat comparison domains for closed-artifact reads.

Windows CPython path stat and descriptor stat expose different ctime semantics.
Compare birthtime across APIs, then retain raw ctime within the same open handle
to detect metadata/content changes even when size and mtime are restored.
"""

from __future__ import annotations

import os


class FileIdentityUnavailable(ValueError):
    """The platform cannot supply the required comparison fields."""


def stable_identity(info: os.stat_result) -> tuple[int, ...]:
    timestamp = (
        getattr(info, "st_birthtime_ns", None)
        if os.name == "nt"
        else getattr(info, "st_ctime_ns", None)
    )
    if type(timestamp) is not int:
        raise FileIdentityUnavailable("file_identity_unavailable")
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        timestamp,
    )


def handle_identity(info: os.stat_result) -> tuple[int, ...]:
    """Use only for comparisons between snapshots of the same open handle."""
    changed = getattr(info, "st_ctime_ns", None)
    if type(changed) is not int:
        raise FileIdentityUnavailable("file_identity_unavailable")
    return (*stable_identity(info), changed)
