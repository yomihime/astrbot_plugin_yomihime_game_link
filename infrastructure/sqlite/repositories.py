"""Explicit composition of the reviewed SQLite domain repositories.

This module contains no module-facing service locator.  It keeps database and
repository objects on the host side and only constructs the repositories that
the B03 service assembly needs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from yomihime_game_link_sdk.services import ConfigTarget

from ...core.contracts.validation_boundary import validate_contract
from ...core.ports import ModuleRegistrationLookup
from ..files import SafeFileStore
from ..secret_store import SQLiteSecretStore
from .database import SQLiteDatabase
from .repositories_auth import SQLiteAuthRepository
from .repositories_cache_resources import (
    SQLiteCacheRepository,
    SQLiteResourceRepository,
)
from .repositories_config import SQLiteConfigRepository, SQLiteRecordRepository
from .repositories_grant_revocation import SQLiteGrantRevocationRepository
from .repositories_identity import (
    SQLiteBindingRepository,
    SQLiteConversationRepository,
    SQLiteIdentityRepository,
)


class SQLiteRepositories:
    """Host-owned collection of domain repositories over one SQLite file."""

    __slots__ = (
        "database",
        "config",
        "identities",
        "conversations",
        "bindings",
        "authorization",
        "grant_revocation",
        "cache",
        "resources",
        "secret_store",
        "registration_lookup",
    )

    def __init__(
        self,
        database: SQLiteDatabase | str | Path,
        registration_lookup: ModuleRegistrationLookup,
        *,
        file_store: SafeFileStore,
        secret_store: SQLiteSecretStore,
        subscription_gate_fields: Mapping[ConfigTarget, tuple[str, ...]] | None = None,
        config_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        validate_contract(subscription_gate_fields)
        if not callable(getattr(registration_lookup, "require_registered", None)):
            raise TypeError("registration lookup is not usable")
        if not all(
            callable(getattr(file_store, method, None))
            for method in ("stage", "commit", "read", "discard", "mark_orphan")
        ):
            raise TypeError("safe file store is not usable")
        if not all(
            callable(getattr(secret_store, method, None))
            for method in ("stage", "claim_for_config", "finalize_active", "pending")
        ):
            raise TypeError("secret store is not usable")
        self.database = (
            database
            if isinstance(database, SQLiteDatabase)
            else SQLiteDatabase(database)
        )
        try:
            secret_database_path = Path(secret_store.database.path).resolve()
            main_database_path = self.database.path.resolve()
        except Exception:
            raise TypeError(
                "secret store must use the composed SQLite database"
            ) from None
        if secret_database_path != main_database_path:
            raise ValueError("secret store database does not match repository database")
        self.registration_lookup = registration_lookup
        self.config = SQLiteConfigRepository(
            self.database,
            subscription_gate_fields=subscription_gate_fields,
            clock=config_clock,
        )
        self.identities = SQLiteIdentityRepository(self.database)
        self.conversations = SQLiteConversationRepository(self.database)
        self.bindings = SQLiteBindingRepository(self.database, registration_lookup)
        self.authorization = SQLiteAuthRepository(self.database, expire_on_open=True)
        self.grant_revocation = SQLiteGrantRevocationRepository(self.database)
        self.cache = SQLiteCacheRepository(self.database)
        self.resources = SQLiteResourceRepository(self.database, file_store)
        self.secret_store = secret_store

    def records_for(self, registered_module: object) -> SQLiteRecordRepository:
        """Bind records storage to the exact Registry-issued module object."""

        return SQLiteRecordRepository(
            self.database, registered_module, self.registration_lookup
        )


__all__ = ["SQLiteRepositories"]
