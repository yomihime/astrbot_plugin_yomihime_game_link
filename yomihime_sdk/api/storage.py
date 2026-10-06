"""Typed, scope-bound storage contracts exposed to game modules.

These contracts deliberately describe collections rather than a database.  A
collection is issued for one declared module collection and one owner scope;
callers cannot select another user's partition through a query parameter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Mapping, Protocol, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class ModuleStoragePaths:
    """Core-issued descriptive ownership routes, never filesystem authority.

    Existing configuration/cache/secret records retain their shared SQLite
    transactions. Paths identify retained module directories for future data;
    this object supplies no arbitrary file access or deletion operation.
    """

    module_id: str
    root: str
    config: str
    cache: str
    data: str
    secrets: str


JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
JsonObject: TypeAlias = Mapping[str, JsonValue]

_LOCAL_MODULE_ID = r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*"
_MODULE_ID = re.compile(rf"{_LOCAL_MODULE_ID}(?:/{_LOCAL_MODULE_ID})?\Z")


def validate_module_id(value: object, field: str = "module_id") -> None:
    """Validate a B02 local or Registry-issued ``package/module`` ID.

    The optional qualified form is deliberately grammar-bound.  It is not a
    filesystem path and therefore rejects empty segments, traversal, encoded
    separators, and additional authority/path components.
    """

    if type(value) is not str or not _MODULE_ID.fullmatch(value):
        raise ValueError(f"{field} must be a local or package/module identifier")


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

    @classmethod
    def validate(cls, value: object) -> "GrantReference":
        if not isinstance(value, cls):
            raise TypeError("grant reference must be a GrantReference")
        try:
            return cls(value.grant_id, value.revision)
        except (AttributeError, TypeError, ValueError):
            raise ValueError("grant reference invariants are invalid") from None


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
            try:
                grant = GrantReference.validate(self.grant)
            except (TypeError, ValueError):
                raise ValueError(
                    "authorized scope requires a valid grant reference"
                ) from None
            object.__setattr__(self, "grant", grant)
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

    @classmethod
    def validate(cls, value: object) -> "OwnerScope":
        """Rebuild a scope at a service boundary instead of trusting dataclass identity."""
        if not isinstance(value, cls):
            raise TypeError("owner scope must be an OwnerScope")
        try:
            return cls(value.kind, value.user_id, value.grant)
        except (AttributeError, TypeError, ValueError):
            raise ValueError("owner scope invariants are invalid") from None


@dataclass(frozen=True, slots=True)
class SecretRef:
    """A copyable declaration; issuance is proven by ``SecretReceiptLedger``."""

    token: str
    principal_id: str
    module_id: str
    field: str
    operation_id: str

    def __post_init__(self) -> None:
        for field in ("token", "principal_id", "field", "operation_id"):
            value = getattr(self, field)
            if (
                not isinstance(value, str)
                or not value
                or any(character.isspace() or character in "/\\" for character in value)
            ):
                raise ValueError("secret references require bounded opaque fields")
        validate_module_id(self.module_id)
        if not self.token.startswith("secret_"):
            raise ValueError("secret reference token must be opaque")

    def __repr__(self) -> str:
        return "SecretRef(<opaque>)"

    @classmethod
    def validate(cls, value: object) -> "SecretRef":
        """Rebuild syntax only; this never proves store issuance."""
        if not isinstance(value, cls):
            raise TypeError("secret reference must be a SecretRef")
        try:
            return cls(
                value.token,
                value.principal_id,
                value.module_id,
                value.field,
                value.operation_id,
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("secret reference invariants are invalid") from None


@dataclass(frozen=True, slots=True)
class SecretTarget:
    principal_id: str
    module_id: str
    field: str

    def __post_init__(self) -> None:
        for value in (self.principal_id, self.field):
            _require_identifier(value, "secret target field")
            if any(character in value for character in ("/", "\\", "\n")):
                raise ValueError("secret target fields must be bounded")
        validate_module_id(self.module_id)


class SecretReceiptState(str, Enum):
    STAGED = "staged"
    CLAIMED = "claimed"
    ACTIVE = "active"
    CAS_CONFLICT = "cas_conflict"
    ORPHAN = "orphan"
    DELETE_FAILED = "delete_failed"
    RECOVERABLE = "recoverable"
    TOMBSTONED = "tombstoned"


@dataclass(frozen=True, slots=True)
class SecretReceipt:
    """Copyable receipt whose authenticity comes from the persistent ledger."""

    secret_ref: SecretRef
    target: SecretTarget
    operation_id: str
    expected_config_revision: int
    ledger_revision: int
    state: SecretReceiptState = SecretReceiptState.STAGED

    def __post_init__(self) -> None:
        ref = SecretRef.validate(self.secret_ref)
        target = self.target if isinstance(self.target, SecretTarget) else None
        if target is None:
            raise TypeError("secret receipt requires a SecretTarget")
        target = SecretTarget(target.principal_id, target.module_id, target.field)
        if (
            ref.principal_id != target.principal_id
            or ref.module_id != target.module_id
            or ref.field != target.field
        ):
            raise ValueError("secret receipt target does not match its ref")
        if ref.operation_id != self.operation_id:
            raise ValueError("secret receipt operation does not match its ref")
        if (
            not isinstance(self.expected_config_revision, int)
            or isinstance(self.expected_config_revision, bool)
            or self.expected_config_revision < 0
            or not isinstance(self.ledger_revision, int)
            or isinstance(self.ledger_revision, bool)
            or self.ledger_revision < 1
        ):
            raise ValueError("secret receipt revisions are invalid")
        if not isinstance(self.state, SecretReceiptState):
            raise TypeError("secret receipt state must be a SecretReceiptState")
        object.__setattr__(self, "secret_ref", ref)
        object.__setattr__(self, "target", target)

    @classmethod
    def validate(cls, value: object) -> "SecretReceipt":
        if not isinstance(value, cls):
            raise TypeError("secret receipt must be a SecretReceipt")
        try:
            return cls(
                value.secret_ref,
                value.target,
                value.operation_id,
                value.expected_config_revision,
                value.ledger_revision,
                value.state,
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("secret receipt invariants are invalid") from None


@dataclass(frozen=True, slots=True)
class ClaimedSecretReceipt(SecretReceipt):
    """Ledger claim result; the DTO itself is not an issuance proof."""

    state: SecretReceiptState = SecretReceiptState.CLAIMED

    def __post_init__(self) -> None:
        SecretReceipt.__post_init__(self)
        if self.state is not SecretReceiptState.CLAIMED:
            raise ValueError("claimed receipt must be in CLAIMED state")

    @classmethod
    def validate(cls, value: object) -> "ClaimedSecretReceipt":
        if not isinstance(value, cls):
            raise TypeError("claimed receipt must be a ClaimedSecretReceipt")
        try:
            return cls(
                value.secret_ref,
                value.target,
                value.operation_id,
                value.expected_config_revision,
                value.ledger_revision,
                value.state,
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("claimed receipt invariants are invalid") from None


class SecretMetadataState(str, Enum):
    ACTIVE = "active"
    TOMBSTONED = "tombstoned"


@dataclass(frozen=True, slots=True)
class SecretMetadata:
    field: str
    secret_ref: SecretRef | None
    revision: int
    state: SecretMetadataState

    @classmethod
    def validate(cls, value: object) -> "SecretMetadata":
        if not isinstance(value, cls):
            raise TypeError("secret metadata must be a SecretMetadata")
        try:
            return cls(value.field, value.secret_ref, value.revision, value.state)
        except (AttributeError, TypeError, ValueError):
            raise ValueError("secret metadata invariants are invalid") from None

    def __post_init__(self) -> None:
        _require_identifier(self.field, "secret metadata field")
        if self.secret_ref is not None:
            object.__setattr__(self, "secret_ref", SecretRef.validate(self.secret_ref))
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("secret metadata revision must be positive")
        if not isinstance(self.state, SecretMetadataState):
            raise TypeError("secret metadata state must be a SecretMetadataState")
        if self.state is SecretMetadataState.ACTIVE and self.secret_ref is None:
            raise ValueError("active secret metadata requires a secret ref")
        if self.state is SecretMetadataState.TOMBSTONED and self.secret_ref is not None:
            raise ValueError("tombstoned secret metadata cannot retain an active ref")


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
    revision: int = 1

    def __post_init__(self) -> None:
        _require_identifier(self.key, "cache key")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("cache expiry must be timezone-aware")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("cache revision must be positive")
        snapshot = freeze_json(self.payload)
        if not isinstance(snapshot, Mapping):
            raise TypeError("cache payload must be a JSON object")
        object.__setattr__(self, "payload", snapshot)


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

    def __post_init__(self) -> None:
        if not isinstance(self.status, CacheLookupStatus):
            raise TypeError("cache lookup status must be a CacheLookupStatus")
        if self.status is CacheLookupStatus.HIT and not isinstance(
            self.entry, CacheEntry
        ):
            raise ValueError("a cache hit requires an entry")
        if self.status is not CacheLookupStatus.HIT and self.entry is not None:
            raise ValueError("non-hit cache results cannot expose an entry")


def _scope_name(value: str, field: str) -> None:
    _require_identifier(value, field)
    if any(character in value for character in ("/", "\\", ":")):
        raise ValueError(f"{field} cannot contain a path or namespace separator")


@dataclass(frozen=True, slots=True)
class CollectionIndex:
    """A manifest-declared scalar index; callers cannot submit expressions."""

    name: str
    field: str

    def __post_init__(self) -> None:
        _scope_name(self.name, "index name")
        _scope_name(self.field, "index field")

    @classmethod
    def validate(cls, value: object) -> "CollectionIndex":
        if not isinstance(value, cls):
            raise TypeError("collection indexes require CollectionIndex values")
        if type(value.name) is not str or type(value.field) is not str:
            raise ValueError("collection index identifiers must be built-in strings")
        try:
            return cls(value.name, value.field)
        except (AttributeError, TypeError, ValueError):
            raise ValueError("collection index invariants are invalid") from None


@dataclass(frozen=True, slots=True)
class CollectionDescriptor:
    """The collection contract used to issue a scope-bound RecordCollection."""

    name: str
    schema_version: int
    owner_kind: OwnershipKind
    indexes: tuple[CollectionIndex, ...] = ()
    retention_category: str = "standard"

    def __post_init__(self) -> None:
        _scope_name(self.name, "collection name")
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("collection schema_version must be positive")
        if not isinstance(self.owner_kind, OwnershipKind):
            raise TypeError("collection owner_kind must be an OwnershipKind")
        indexes = tuple(self.indexes)
        if any(not isinstance(item, CollectionIndex) for item in indexes):
            raise TypeError("collection indexes require CollectionIndex values")
        if len({item.name for item in indexes}) != len(indexes):
            raise ValueError("collection index names must be unique")
        _scope_name(self.retention_category, "retention_category")
        object.__setattr__(self, "indexes", indexes)

    @classmethod
    def validate(cls, value: object) -> "CollectionDescriptor":
        if not isinstance(value, cls):
            raise TypeError("collection descriptor must be a CollectionDescriptor")
        if type(value.name) is not str or type(value.retention_category) is not str:
            raise ValueError(
                "collection descriptor identifiers must be built-in strings"
            )
        if type(value.schema_version) is not int:
            raise ValueError("collection schema_version must be a built-in integer")
        try:
            indexes = tuple(CollectionIndex.validate(item) for item in value.indexes)
            return cls(
                value.name,
                value.schema_version,
                value.owner_kind,
                indexes,
                value.retention_category,
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("collection descriptor invariants are invalid") from None


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

    def __post_init__(self) -> None:
        _require_identifier(self.asset_id, "asset_id")
        if "/" in self.asset_id or "\\" in self.asset_id:
            raise ValueError("asset_id cannot contain a path")
        _require_identifier(self.media_type, "media_type")
        scope = OwnerScope.validate(self.scope)
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("resource size must be a non-negative integer")
        if self.expires_at is not None and (
            self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None
        ):
            raise ValueError("resource expiry must be timezone-aware")
        if not isinstance(self.temporary, bool):
            raise TypeError("resource temporary marker must be a bool")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("resource revision must be positive")
        object.__setattr__(self, "scope", scope)


RecordCollectionDescriptor = CollectionDescriptor
CacheScope = CacheVisibility
ResourceDescriptor = ResourceMetadata
