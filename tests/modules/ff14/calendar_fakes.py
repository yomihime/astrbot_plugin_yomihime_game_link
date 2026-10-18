"""Deterministic public record storage for calendar snapshot contract tests."""

from yomihime_game_link_sdk.errors import RevisionConflict, UniqueConstraintViolation
from yomihime_game_link_sdk.storage import OwnerScope, VersionedRecord


class MemoryRecords:
    scope = OwnerScope.public()

    def __init__(self):
        self.rows = {}

    async def collection(self, name):
        assert name == "calendar_snapshots"
        return self

    async def get(self, key):
        return self.rows.get(key)

    async def create(self, key, value):
        if key in self.rows:
            raise UniqueConstraintViolation("calendar_snapshot")
        row = VersionedRecord(key, 1, value)
        self.rows[key] = row
        return row

    async def replace(self, key, value, *, expected_revision):
        current = self.rows[key]
        if current.revision != expected_revision:
            raise RevisionConflict(
                "calendar_snapshot", expected_revision, current.revision
            )
        row = VersionedRecord(key, expected_revision + 1, value)
        self.rows[key] = row
        return row
