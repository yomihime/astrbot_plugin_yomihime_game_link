"""Typed, scope-bound storage contracts exposed to game modules.

These contracts deliberately describe collections rather than a database.  A
collection is issued for one declared module collection and one owner scope;
callers cannot select another user's partition through a query parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Mapping, Protocol, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
JsonObject: TypeAlias = Mapping[str, JsonValue]


def freeze_json(value: object) -> JsonValue:
    """Copy structured JSON data into an immutable, JSON-compatible snapshot."""
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            frozen[key] = freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    raise TypeError("value is not structured JSON data")


def _require_identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


class OwnershipKind(str, Enum):
    PUBLIC = "public"
    USER = "user"
    AUTHORIZED = "authorized"


@dataclass(frozen=True, slots=True)
class GrantReference:
    """A versioned grant reference; it is not authority by itself."""

    grant_id: str
    revision: int

    def __post_init__(self) -> None:
        _require_identifier(self.grant_id, "grant_id")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("grant revision must be at least 1")


@dataclass(frozen=True, slots=True)
class OwnerScope:
    """The visibility partition that is bound when a collection is opened."""

    kind: OwnershipKind
    user_id: str | None = None
    grant: GrantReference | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OwnershipKind):
            raise TypeError("ownership kind must be an OwnershipKind")
        if self.kind is OwnershipKind.PUBLIC:
            if self.user_id is not None or self.grant is not None:
                raise ValueError("a public scope cannot carry user or grant data")
        elif self.kind is OwnershipKind.USER:
            _require_identifier(self.user_id or "", "private user_id")
            if self.grant is not None:
                raise ValueError("a user scope cannot carry a grant reference")
        elif self.kind is OwnershipKind.AUTHORIZED:
            _require_identifier(self.user_id or "", "private user_id")
            if not isinstance(self.grant, GrantReference):
                raise ValueError("an authorized scope requires a grant reference")
        else:
            raise ValueError("unknown ownership kind")

    @classmethod
    def public(cls) -> "OwnerScope":
        return cls(OwnershipKind.PUBLIC)

    @classmethod
    def private(cls, user_id: str, grant: GrantReference) -> "OwnerScope":
        """Compatibility spelling for a private, authorization-bound scope."""
        return cls(OwnershipKind.AUTHORIZED, user_id, grant)

    @classmethod
    def user(cls, user_id: str) -> "OwnerScope":
        return cls(OwnershipKind.USER, user_id)

    @classmethod
    def authorized(cls, user_id: str, grant: GrantReference) -> "OwnerScope":
        return cls(OwnershipKind.AUTHORIZED, user_id, grant)


@dataclass(frozen=True, slots=True)
class VersionedRecord:
    key: str
    revision: int
    value: JsonObject

    def __post_init__(self) -> None:
        _require_identifier(self.key, "record key")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("record revision must be at least 1")
        snapshot = freeze_json(self.value)
        if not isinstance(snapshot, Mapping):
            raise TypeError("record value must be a JSON object")
        object.__setattr__(self, "value", snapshot)


class QueryOperator(str, Enum):
    EQUALS = "equals"
    PREFIX = "prefix"


@dataclass(frozen=True, slots=True)
class DeclaredIndexQuery:
    """A query over one manifest-declared index, never a field expression."""

    index_name: str
    operator: QueryOperator
    value: JsonScalar
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.index_name, "index_name")
        if isinstance(self.value, float) and not isfinite(self.value):
            raise ValueError("index query values must be finite")
        if self.value is not None and not isinstance(
            self.value, (str, int, float, bool)
        ):
            raise TypeError("index query values must be scalar")
        if (
            isinstance(self.operator, QueryOperator) is False
            or isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= 100
        ):
            raise ValueError("query limit must be between 1 and 100")
        if self.cursor is not None:
            _require_identifier(self.cursor, "cursor")


@dataclass(frozen=True, slots=True)
class RecordPage:
    records: tuple[VersionedRecord, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        records = tuple(self.records)
        if any(not isinstance(record, VersionedRecord) for record in records):
            raise TypeError("record pages require VersionedRecord instances")
        object.__setattr__(self, "records", records)
        if self.next_cursor is not None:
            _require_identifier(self.next_cursor, "next_cursor")


class RecordCollection(Protocol):
    """A declared collection already bound to its module and OwnerScope."""

    @property
    def scope(self) -> OwnerScope: ...

    async def get(self, key: str) -> VersionedRecord | None: ...

    async def create(self, key: str, value: JsonObject) -> VersionedRecord: ...

    async def replace(
        self, key: str, value: JsonObject, *, expected_revision: int
    ) -> VersionedRecord: ...

    async def query(self, query: DeclaredIndexQuery) -> RecordPage: ...

    async def delete(self, key: str, *, expected_revision: int) -> None: ...


@dataclass(frozen=True, slots=True)
class CacheEntry:
    key: str
    payload: JsonObject
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_identifier(self.key, "cache key")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("cache expiry must be timezone-aware")
        snapshot = freeze_json(self.payload)
        if not isinstance(snapshot, Mapping):
            raise TypeError("cache payload must be a JSON object")
        object.__setattr__(self, "payload", snapshot)
