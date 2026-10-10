from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Protocol, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
JsonObject: TypeAlias = Mapping[str, JsonValue]


class OwnershipKind(str, Enum):
    PUBLIC = "public"
    USER = "user"
    AUTHORIZED = "authorized"


@dataclass(frozen=True, slots=True)
class GrantReference:
    """A versioned grant reference; it is not authority by itself."""

    grant_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class OwnerScope:
    """The visibility partition that is bound when a collection is opened."""

    kind: OwnershipKind
    user_id: str | None = None
    grant: GrantReference | None = None

    @classmethod
    def public(cls) -> "OwnerScope":
        return cls(OwnershipKind.PUBLIC)

    @classmethod
    def user(cls, user_id: str) -> "OwnerScope":
        return cls(OwnershipKind.USER, user_id)

    @classmethod
    def authorized(cls, user_id: str, grant: GrantReference) -> "OwnerScope":
        return cls(OwnershipKind.AUTHORIZED, user_id, grant)


@dataclass(frozen=True, slots=True)
class SecretRef:
    """A copyable declaration; issuance is proven by ``SecretReceiptLedger``."""

    token: str
    principal_id: str
    module_id: str
    field: str
    operation_id: str

    def __repr__(self) -> str:
        return "SecretRef(<opaque>)"


class SecretMetadataState(str, Enum):
    ACTIVE = "active"
    TOMBSTONED = "tombstoned"


@dataclass(frozen=True, slots=True)
class SecretMetadata:
    field: str
    secret_ref: SecretRef | None
    revision: int
    state: SecretMetadataState


@dataclass(frozen=True, slots=True)
class VersionedRecord:
    key: str
    revision: int
    value: JsonObject

    def __post_init__(self) -> None:
        if isinstance(self.value, Mapping):
            object.__setattr__(self, "value", MappingProxyType(dict(self.value)))


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


@dataclass(frozen=True, slots=True)
class RecordPage:
    records: tuple[VersionedRecord, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        if self.records is not None:
            object.__setattr__(self, "records", tuple(self.records))


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
    revision: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.payload, Mapping):
            object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


class CacheVisibility(str, Enum):
    """The only cache partitions a module can request."""

    PUBLIC = "public"
    USER = "user"
    AUTHORIZED = "authorized"


class CacheLookupStatus(str, Enum):
    HIT = "hit"
    MISS = "miss"
    EXPIRED = "expired"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class CacheLookup:
    """A cache result whose visibility and freshness are explicit."""

    status: CacheLookupStatus
    entry: CacheEntry | None = None


@dataclass(frozen=True, slots=True)
class CollectionIndex:
    """A manifest-declared scalar index; callers cannot submit expressions."""

    name: str
    field: str


@dataclass(frozen=True, slots=True)
class CollectionDescriptor:
    """The collection contract used to issue a scope-bound RecordCollection."""

    name: str
    schema_version: int
    owner_kind: OwnershipKind
    indexes: tuple[CollectionIndex, ...] = ()
    retention_category: str = "standard"

    def __post_init__(self) -> None:
        if self.indexes is not None:
            object.__setattr__(self, "indexes", tuple(self.indexes))


@dataclass(frozen=True, slots=True)
class ResourceMetadata:
    """Metadata for a registered resource; it deliberately has no local path."""

    asset_id: str
    media_type: str
    scope: OwnerScope
    size_bytes: int
    expires_at: datetime | None = None
    temporary: bool = True
    revision: int = 1


@dataclass(frozen=True, slots=True)
class CacheQuery:
    key: str
    visibility: CacheVisibility | None = None
