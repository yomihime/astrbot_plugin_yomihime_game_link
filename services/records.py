"""Registry-backed, owner-bound ModuleRecords service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ..api.services import ModuleRecords
from ..api.storage import (
    CollectionDescriptor,
    DeclaredIndexQuery,
    OwnerScope,
    RecordCollection,
    RecordPage,
    VersionedRecord,
)
from ..core.ports import (
    ModuleNotRegistered,
    ModuleRegistrationLookup,
    ModuleRegistrationSnapshot,
    RecordRepository,
)


def _module_id(value: object) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or any(character in value for character in ("\\", "\n", "\r"))
    ):
        raise ValueError("module_id is invalid")
    return value


def _collection_name(value: object) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or any(character.isspace() or character in "/\\" for character in value)
    ):
        raise ValueError("collection name is invalid")
    return value


class _ModuleRecordsCoordinator:
    """Trusted host-side coordinator for owner-bound records access."""

    __slots__ = ("__module_id", "__repository", "__owner", "__lookup")

    def __init__(
        self,
        module_id: str,
        repository: RecordRepository,
        owner: OwnerScope,
        registration_lookup: ModuleRegistrationLookup | None = None,
    ) -> None:
        module_id = _module_id(module_id)
        owner = OwnerScope.validate(owner)
        if not callable(getattr(repository, "collection", None)):
            raise TypeError("record repository is not usable")
        if registration_lookup is None:
            registration_lookup = getattr(repository, "registration_lookup", None)
        if not callable(getattr(registration_lookup, "require_registered", None)):
            raise TypeError("module records requires a registration lookup")
        object.__setattr__(self, "_ModuleRecordsCoordinator__module_id", module_id)
        object.__setattr__(self, "_ModuleRecordsCoordinator__repository", repository)
        object.__setattr__(self, "_ModuleRecordsCoordinator__owner", owner)
        object.__setattr__(
            self, "_ModuleRecordsCoordinator__lookup", registration_lookup
        )

    def view(self) -> "ModuleRecordsService":
        return ModuleRecordsService(self)

    async def collection(self, name: str) -> RecordCollection:
        name = _collection_name(name)
        snapshot = await self._registered()
        if not snapshot.enabled:
            raise ValueError("module is disabled")
        descriptor = next(
            (item for item in snapshot.collections if item.name == name), None
        )
        if descriptor is None:
            raise ValueError("collection is not declared for this module")
        collection = await self.repository.collection(
            self.module_id, descriptor, self.owner
        )
        if any(
            not callable(getattr(collection, method, None))
            for method in ("get", "create", "replace", "query", "delete")
        ):
            raise ValueError("record repository returned an invalid collection")
        current = await self._registered()
        self._assert_collection(current, descriptor, snapshot.epoch)
        return _BoundRecordCollection(self, descriptor, self.owner, snapshot.epoch)

    async def operate(
        self,
        descriptor: CollectionDescriptor,
        owner: OwnerScope,
        module_epoch: int,
        method: str,
        *args: object,
        **kwargs: object,
    ) -> object:
        snapshot = await self._registered()
        self._assert_collection(snapshot, descriptor, module_epoch)
        collection = await self.repository.collection(self.module_id, descriptor, owner)
        snapshot = await self._registered()
        self._assert_collection(snapshot, descriptor, module_epoch)
        operation = getattr(collection, method, None)
        if not callable(operation):
            raise ValueError("record repository returned an invalid collection")
        result = await operation(*args, **kwargs)
        snapshot = await self._registered()
        self._assert_collection(snapshot, descriptor, module_epoch)
        return result

    def _assert_collection(
        self,
        snapshot: ModuleRegistrationSnapshot,
        descriptor: CollectionDescriptor,
        module_epoch: int,
    ) -> None:
        if (
            snapshot.module_id != self.module_id
            or not snapshot.enabled
            or snapshot.epoch != module_epoch
            or descriptor not in snapshot.collections
        ):
            raise ValueError("module registration changed")

    async def _registered(self) -> ModuleRegistrationSnapshot:
        try:
            snapshot = await self.__lookup.require_registered(self.__module_id)
            return ModuleRegistrationSnapshot(
                snapshot.module_id,
                snapshot.enabled,
                snapshot.registry_revision,
                snapshot.epoch,
                snapshot.collections,
            )
        except ModuleNotRegistered:
            raise
        except (AttributeError, TypeError, ValueError):
            raise ValueError(
                "module registration lookup returned an invalid snapshot"
            ) from None

    @property
    def module_id(self) -> str:
        return self.__module_id

    @property
    def repository(self) -> RecordRepository:
        return self.__repository

    @property
    def owner(self) -> OwnerScope:
        return self.__owner


class _BoundRecordCollection(RecordCollection):
    """Refresh the declaration snapshot before each repository operation.

    SQLite collections retain their issuing registration snapshot. Reissuing a
    collection for each operation lets an unrelated directory publication
    advance the global revision without invalidating this module's collection;
    the coordinator still fences module epoch, enabled state, and declaration.
    """

    __slots__ = ("__coordinator", "__descriptor", "__owner", "__module_epoch")

    def __init__(
        self,
        coordinator: _ModuleRecordsCoordinator,
        descriptor: CollectionDescriptor,
        owner: OwnerScope,
        module_epoch: int,
    ) -> None:
        object.__setattr__(self, "_BoundRecordCollection__coordinator", coordinator)
        object.__setattr__(self, "_BoundRecordCollection__descriptor", descriptor)
        object.__setattr__(self, "_BoundRecordCollection__owner", owner)
        object.__setattr__(self, "_BoundRecordCollection__module_epoch", module_epoch)

    @property
    def scope(self) -> OwnerScope:
        return self.__owner

    async def get(self, key: str) -> VersionedRecord | None:
        result = await self._operate("get", key)
        return cast(VersionedRecord | None, result)

    async def create(self, key: str, value: Mapping[str, object]) -> VersionedRecord:
        result = await self._operate("create", key, value)
        return cast(VersionedRecord, result)

    async def replace(
        self, key: str, value: Mapping[str, object], *, expected_revision: int
    ) -> VersionedRecord:
        result = await self._operate(
            "replace", key, value, expected_revision=expected_revision
        )
        return cast(VersionedRecord, result)

    async def query(self, query: DeclaredIndexQuery) -> RecordPage:
        result = await self._operate("query", query)
        return cast(RecordPage, result)

    async def delete(self, key: str, *, expected_revision: int) -> None:
        await self._operate("delete", key, expected_revision=expected_revision)

    async def _operate(self, method: str, *args: object, **kwargs: object) -> object:
        return await self.__coordinator.operate(
            self.__descriptor,
            self.__owner,
            self.__module_epoch,
            method,
            *args,
            **kwargs,
        )


class ModuleRecordsService(ModuleRecords):
    """Module-facing records facade for trusted same-process extensions.

    Its supported API is limited to ``collection(name)``.  The host retains
    coordinator, repository, lookup, and owner state; this declarative
    boundary does not claim to sandbox arbitrary same-process Python
    reflection.
    """

    __slots__ = ("__collection",)

    def __init__(
        self,
        coordinator_or_module_id: _ModuleRecordsCoordinator | str,
        repository: RecordRepository | None = None,
        owner: OwnerScope | None = None,
        registration_lookup: ModuleRegistrationLookup | None = None,
    ) -> None:
        if isinstance(coordinator_or_module_id, _ModuleRecordsCoordinator):
            if any(
                value is not None for value in (repository, owner, registration_lookup)
            ):
                raise TypeError("coordinator facade does not accept storage arguments")
            coordinator = coordinator_or_module_id
        else:
            if repository is None or owner is None:
                raise TypeError("records facade requires a coordinator")
            coordinator = _ModuleRecordsCoordinator(
                coordinator_or_module_id, repository, owner, registration_lookup
            )

        async def collection(name: str) -> RecordCollection:
            return await coordinator.collection(name)

        object.__setattr__(self, "_ModuleRecordsService__collection", collection)

    async def collection(self, name: str) -> RecordCollection:
        collection = object.__getattribute__(self, "_ModuleRecordsService__collection")
        return await collection(name)

    def __getattribute__(self, name: str):
        if name in {"collection", "__class__", "__dir__"}:
            return object.__getattribute__(self, name)
        raise AttributeError("records facade exposes only collection")

    def __dir__(self) -> list[str]:
        return ["collection"]


ModuleRecordsView = ModuleRecordsService
RecordsService = ModuleRecordsService
ModuleRecordsCoordinator = _ModuleRecordsCoordinator
RecordService = ModuleRecordsService

__all__ = [
    "ModuleRecordsCoordinator",
    "ModuleRecordsService",
    "ModuleRecordsView",
    "RecordService",
    "RecordsService",
]
