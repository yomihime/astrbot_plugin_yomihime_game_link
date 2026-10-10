"""Core-owned client-credentials tokens for declared source HTTP calls."""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from time import monotonic

from yomihime_game_link_sdk.declarations import ConfigField, SourceDeclaration
from yomihime_game_link_sdk.errors import SourceHttpError
from yomihime_game_link_sdk.services import (
    ConfigSnapshot,
    ConfigTarget,
    HttpRequest,
    HttpResponse,
)
from yomihime_game_link_sdk.storage import SecretMetadataState, SecretRef

from ..core.contracts.validation_boundary import validate_contract
from ..core.ports import ConfigRepository, SecretOwner, SecretStore, validate_module_id
from ..infrastructure.http import (
    CredentialExchangeRequest,
    CredentialLease,
    _create_credential_exchange_request,
)

_CLIENT_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{1,4096}\Z")
_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 5.0
_TOKEN_RESPONSE_BYTES = 64 * 1024
_MAX_CREDENTIAL_PAYLOAD_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class SourceCredentialPolicy:
    """Host-trusted, immutable endpoint and path permissions for one alias."""

    module_id: str
    source_id: str
    credential_ref: str
    resource_host: str
    allowed_resource_paths: tuple[str, ...]
    token_host: str
    token_path: str
    scope: str | None = None

    def __post_init__(self) -> None:
        validate_module_id(self.module_id, "credential policy module_id")
        resource = validate_contract(
            SourceDeclaration(self.source_id, self.resource_host, self.credential_ref)
        )
        token = validate_contract(SourceDeclaration("oauth_token", self.token_host))
        if resource.credential_ref is None:
            raise ValueError("source credential policy requires a credential alias")
        validate_contract(
            HttpRequest(
                "oauth_token",
                self.token_path,
                "POST",
                body=b"grant_type=client_credentials",
            )
        )
        paths = tuple(self.allowed_resource_paths)
        if not paths or len(paths) != len(set(paths)):
            raise ValueError("credential policy requires unique resource paths")
        for path in paths:
            validate_contract(HttpRequest(self.source_id, path, "GET"))
        object.__setattr__(self, "resource_host", resource.host)
        object.__setattr__(self, "token_host", token.host)
        object.__setattr__(self, "allowed_resource_paths", paths)
        if self.scope is not None and (
            type(self.scope) is not str
            or not self.scope
            or self.scope != self.scope.strip()
            or len(self.scope) > 512
            or any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in self.scope)
        ):
            raise ValueError("credential policy scope is invalid")

    def __repr__(self) -> str:
        return (
            "SourceCredentialPolicy("
            f"module_id={self.module_id!r}, source_id={self.source_id!r}, "
            f"resource_host={self.resource_host!r}, token_host={self.token_host!r})"
        )


def bind_source_credential_declarations(
    module_id: str,
    declarations: Sequence[SourceDeclaration],
    config_fields: Sequence[ConfigField],
    policies: Sequence[SourceCredentialPolicy],
) -> tuple[SourceDeclaration, ...]:
    """Apply trusted host aliases only to Core's effective source declarations.

    Disk manifests keep `credential_ref=None`; module code cannot select an
    alias. Programmatic declarations that already contain an alias must match
    the exact host policy or are rejected.
    """

    validate_contract(declarations)
    validate_contract(config_fields)
    validate_module_id(module_id, "source credential module_id")
    sources = tuple(declarations)
    fields = tuple(config_fields)
    bindings = tuple(policies)
    if any(not isinstance(item, SourceDeclaration) for item in sources):
        raise TypeError("source declarations are invalid")
    if any(not isinstance(item, ConfigField) for item in fields):
        raise TypeError("source credential config fields are invalid")
    if any(not isinstance(item, SourceCredentialPolicy) for item in bindings):
        raise TypeError("source credential policies are invalid")
    source_map = {item.source_id: item for item in sources}
    field_map = {item.name: item for item in fields}
    if len(source_map) != len(sources) or len(field_map) != len(fields):
        raise ValueError("source declarations and config fields must be unique")

    policy_map: dict[str, SourceCredentialPolicy] = {}
    aliases: set[str] = set()
    for policy in bindings:
        if policy.module_id != module_id:
            raise ValueError("source credential policy module does not match")
        declaration = source_map.get(policy.source_id)
        field = field_map.get(policy.credential_ref)
        if (
            declaration is None
            or declaration.host != policy.resource_host
            or (
                declaration.credential_ref is not None
                and declaration.credential_ref != policy.credential_ref
            )
            or field is None
            or not field.sensitive
            or field.default is not None
            or policy.source_id in policy_map
            or policy.credential_ref in aliases
        ):
            raise ValueError("source credential policy does not match declaration")
        policy_map[policy.source_id] = policy
        aliases.add(policy.credential_ref)

    effective: list[SourceDeclaration] = []
    for declaration in sources:
        policy = policy_map.get(declaration.source_id)
        if declaration.credential_ref is not None and policy is None:
            raise ValueError("declared source credential has no host policy")
        if policy is None:
            effective.append(declaration)
            continue
        effective.append(
            validate_contract(
                SourceDeclaration(
                    declaration.source_id,
                    declaration.host,
                    policy.credential_ref,
                    declaration.timeout_seconds,
                    declaration.requests_per_minute,
                )
            )
        )
    if len(policy_map) != len(bindings):
        raise ValueError("source credential policy aliases must be unique")
    return tuple(effective)


@dataclass(frozen=True, slots=True, repr=False)
class _ClientMaterial:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)

    def __repr__(self) -> str:
        return "_ClientMaterial(<redacted>)"


@dataclass(slots=True, repr=False)
class _TokenState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    secret_token: str | None = field(default=None, repr=False)
    access_token: str | None = field(default=None, repr=False)
    expires_at: float = 0.0
    generation: int = 0

    def __repr__(self) -> str:
        return f"_TokenState(generation={self.generation}, cached={self.access_token is not None})"


class SourceCredentialService:
    """Resolve one source alias to encrypted client material and a cached token.

    This service is internal to Core. Module-facing `HttpRequest` values never
    contain a secret or authorization header; the caller supplies the trusted
    module ID and source declaration captured when its services were built.
    """

    __slots__ = (
        "_module_id",
        "_config_principal_id",
        "_declarations",
        "_config_fields",
        "_policies",
        "_config_repository",
        "_secret_store",
        "_transport",
        "_clock",
        "_states",
        "_exchange_slots",
        "_closed",
    )

    def __init__(
        self,
        module_id: str,
        declarations: Sequence[SourceDeclaration],
        config_fields: Sequence[ConfigField],
        policies: Sequence[SourceCredentialPolicy],
        config_repository: ConfigRepository,
        secret_store: SecretStore,
        transport: object,
        *,
        config_principal_id: str,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        validate_contract(declarations)
        validate_contract(config_fields)
        validate_module_id(module_id, "source credential module_id")
        if type(config_principal_id) is not str or not config_principal_id.strip():
            raise ValueError("source credential principal is required")
        if not callable(clock):
            raise TypeError("source credential clock must be callable")
        if not callable(getattr(config_repository, "current", None)):
            raise TypeError("source credential config repository is required")
        if not callable(getattr(secret_store, "read", None)):
            raise TypeError("source credential secret store is required")
        policy_values = tuple(policies)
        if policy_values and not callable(
            getattr(transport, "request_credential_exchange", None)
        ):
            raise TypeError("transport must support credential exchange")

        source_map: dict[str, SourceDeclaration] = {}
        for declaration in declarations:
            if not isinstance(declaration, SourceDeclaration):
                raise TypeError("source credential declarations are invalid")
            if declaration.source_id in source_map:
                raise ValueError("source declarations must be unique")
            if declaration.credential_ref is not None:
                source_map[declaration.source_id] = declaration

        field_map: dict[str, ConfigField] = {}
        for config_field in config_fields:
            if not isinstance(config_field, ConfigField):
                raise TypeError("source credential config fields are invalid")
            if config_field.name in field_map:
                raise ValueError("source credential config fields must be unique")
            field_map[config_field.name] = config_field

        policy_map: dict[str, SourceCredentialPolicy] = {}
        aliases: set[str] = set()
        for policy in policy_values:
            if not isinstance(policy, SourceCredentialPolicy):
                raise TypeError("source credential policies are invalid")
            if policy.module_id != module_id:
                raise ValueError("source credential policy module does not match")
            declaration = source_map.get(policy.source_id)
            if (
                declaration is None
                or declaration.host != policy.resource_host
                or declaration.credential_ref != policy.credential_ref
                or policy.credential_ref in aliases
            ):
                raise ValueError("source credential policy does not match declaration")
            if policy.source_id in policy_map:
                raise ValueError("source credential policies must be unique")
            policy_map[policy.source_id] = policy
            aliases.add(policy.credential_ref)

        self._module_id = module_id
        self._config_principal_id = config_principal_id
        self._declarations = source_map
        self._config_fields = field_map
        self._policies = policy_map
        self._config_repository = config_repository
        self._secret_store = secret_store
        self._transport = transport
        self._clock = clock
        self._states = {source_id: _TokenState() for source_id in source_map}
        self._exchange_slots = asyncio.Semaphore(2)
        self._closed = False

    def __repr__(self) -> str:
        return f"SourceCredentialService(module_id={self._module_id!r})"

    async def authorize(
        self,
        module_id: str,
        declaration: SourceDeclaration,
        request: HttpRequest,
        deadline: float,
    ) -> CredentialLease:
        validate_contract(declaration)
        validate_contract(request)
        self._ensure_active()
        policy = self._checked_binding(module_id, declaration, request)
        state = self._states[declaration.source_id]
        async with state.lock:
            self._ensure_active()
            secret_ref = await self._current_secret_ref(declaration.credential_ref)
            self._ensure_active()
            if state.secret_token != secret_ref.token:
                state.access_token = None
                state.expires_at = 0.0
                state.secret_token = secret_ref.token
                state.generation += 1
            # A live token must not bypass a missing/wrong encryption key or a
            # damaged payload after startup. Reopen and authenticate the small
            # local payload before every token use.
            material = await self._read_material(secret_ref, declaration.credential_ref)
            self._ensure_active()
            if state.access_token is not None and self._clock() < state.expires_at:
                return self._lease(state)

            token, lifetime = await self._exchange(policy, material, deadline)
            self._ensure_active()
            current_ref = await self._current_secret_ref(declaration.credential_ref)
            self._ensure_active()
            verified_material = await self._read_material(
                current_ref, declaration.credential_ref
            )
            self._ensure_active()
            if current_ref != secret_ref or verified_material != material:
                state.access_token = None
                state.secret_token = None
                state.expires_at = 0.0
                state.generation += 1
                raise SourceHttpError("credentials_unavailable") from None
            state.access_token = token
            early_expiry = min(30.0, lifetime * 0.1)
            state.expires_at = self._clock() + max(0.0, lifetime - early_expiry)
            state.generation += 1
            return self._lease(state)

    async def invalidate(
        self,
        module_id: str,
        declaration: SourceDeclaration,
        lease: CredentialLease,
    ) -> None:
        validate_contract(declaration)
        if (
            module_id != self._module_id
            or not isinstance(declaration, SourceDeclaration)
            or declaration != self._declarations.get(declaration.source_id)
            or not isinstance(lease, CredentialLease)
        ):
            raise SourceHttpError("credentials_unavailable") from None
        state = self._states[declaration.source_id]
        async with state.lock:
            if (
                state.secret_token == lease.secret_token
                and state.generation == lease.generation
            ):
                state.access_token = None
                state.expires_at = 0.0
                state.generation += 1

    async def close(self) -> None:
        """Clear all in-memory client tokens during Core shutdown."""

        self._closed = True
        for state in self._states.values():
            async with state.lock:
                state.access_token = None
                state.secret_token = None
                state.expires_at = 0.0
                state.generation += 1

    def retire(self) -> None:
        """Synchronously make this binding unusable after manifest replacement."""

        self._closed = True
        for state in self._states.values():
            state.access_token = None
            state.secret_token = None
            state.expires_at = 0.0
            state.generation += 1

    def _ensure_active(self) -> None:
        if self._closed:
            raise SourceHttpError("credentials_unavailable") from None

    def _checked_binding(
        self,
        module_id: str,
        declaration: SourceDeclaration,
        request: HttpRequest,
    ) -> SourceCredentialPolicy:
        validate_contract(declaration)
        validate_contract(request)
        if (
            module_id != self._module_id
            or not isinstance(declaration, SourceDeclaration)
            or declaration != self._declarations.get(declaration.source_id)
            or not isinstance(request, HttpRequest)
            or request.source_id != declaration.source_id
            or declaration.credential_ref is None
        ):
            raise SourceHttpError("credentials_unavailable") from None
        policy = self._policies.get(declaration.source_id)
        field = self._config_fields.get(declaration.credential_ref)
        if (
            policy is None
            or policy.module_id != module_id
            or policy.source_id != declaration.source_id
            or policy.resource_host != declaration.host
            or policy.credential_ref != declaration.credential_ref
            or field is None
            or not field.sensitive
            or request.path not in policy.allowed_resource_paths
        ):
            raise SourceHttpError("credentials_unavailable") from None
        return policy

    async def _current_secret_ref(self, credential_ref: str | None) -> SecretRef:
        if credential_ref is None:
            raise SourceHttpError("credentials_unavailable") from None
        target = validate_contract(
            ConfigTarget(self._config_principal_id, self._module_id)
        )
        try:
            snapshot = await self._config_repository.current(target)
            if not isinstance(snapshot, ConfigSnapshot):
                raise ValueError
            if snapshot.target != target:
                raise ValueError
            matches = [
                item
                for item in snapshot.secret_metadata
                if item.field == credential_ref
            ]
            if len(matches) != 1:
                raise ValueError
            metadata = matches[0]
            secret_ref = metadata.secret_ref
            if (
                metadata.state is not SecretMetadataState.ACTIVE
                or not isinstance(secret_ref, SecretRef)
                or secret_ref.principal_id != self._config_principal_id
                or secret_ref.module_id != self._module_id
                or secret_ref.field != credential_ref
            ):
                raise ValueError
            return secret_ref
        except asyncio.CancelledError:
            raise
        except Exception:
            raise SourceHttpError("credentials_unavailable") from None

    async def _read_material(
        self, secret_ref: SecretRef, credential_ref: str | None
    ) -> _ClientMaterial:
        validate_contract(secret_ref)
        if credential_ref is None:
            raise SourceHttpError("credentials_unavailable") from None
        owner = SecretOwner(
            self._config_principal_id,
            self._module_id,
            credential_ref,
            secret_ref.operation_id,
        )
        try:
            payload = await self._secret_store.read(secret_ref, owner=owner)
            if (
                not isinstance(payload, bytes)
                or not payload
                or len(payload) > _MAX_CREDENTIAL_PAYLOAD_BYTES
            ):
                raise ValueError
            value = json.loads(
                payload.decode("utf-8"), object_pairs_hook=_unique_object
            )
            if (
                not isinstance(value, dict)
                or set(value) != {"schema", "client_id", "client_secret"}
                or type(value.get("schema")) is not int
                or value.get("schema") != 1
            ):
                raise ValueError
            client_id = _client_field(value["client_id"])
            client_secret = _client_field(value["client_secret"])
            return _ClientMaterial(client_id, client_secret)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise SourceHttpError("credentials_unavailable") from None

    async def _exchange(
        self,
        policy: SourceCredentialPolicy,
        material: _ClientMaterial,
        deadline: float,
    ) -> tuple[str, float]:
        exchange_method = getattr(self._transport, "request_credential_exchange", None)
        channel = getattr(self._transport, "_credential_channel", None)
        if not callable(exchange_method) or channel is None:
            raise SourceHttpError("credentials_unavailable") from None
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise SourceHttpError("timeout") from None
        try:
            async with self._exchange_slots:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise SourceHttpError("timeout") from None
                timeout = min(_TOKEN_EXCHANGE_TIMEOUT_SECONDS, remaining)
                exchange: CredentialExchangeRequest = (
                    _create_credential_exchange_request(
                        channel,
                        self._module_id,
                        policy.source_id,
                        policy.credential_ref,
                        policy.token_host,
                        policy.token_path,
                        material.client_id,
                        material.client_secret,
                        policy.scope,
                        timeout,
                    )
                )
                response = await exchange_method(exchange)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            raise SourceHttpError("timeout") from None
        except SourceHttpError as exc:
            raise SourceHttpError(exc.code, status_code=exc.status_code) from None
        except Exception:
            raise SourceHttpError("transport_failed") from None
        if not isinstance(response, HttpResponse):
            raise SourceHttpError("invalid_response") from None
        if not 200 <= response.status_code < 300:
            raise SourceHttpError(
                "rate_limited" if response.status_code == 429 else "upstream_error",
                status_code=response.status_code,
            ) from None
        if len(response.body) > _TOKEN_RESPONSE_BYTES:
            raise SourceHttpError("response_too_large") from None
        try:
            result = json.loads(
                response.body.decode("utf-8"), object_pairs_hook=_unique_object
            )
            if not isinstance(result, dict):
                raise ValueError
            token = result.get("access_token")
            token_type = result.get("token_type")
            expires_in = result.get("expires_in")
            if (
                type(token) is not str
                or not _CLIENT_TOKEN.fullmatch(token)
                or type(token_type) is not str
                or token_type.lower() != "bearer"
                or isinstance(expires_in, bool)
                or not isinstance(expires_in, (int, float))
                or (isinstance(expires_in, float) and not math.isfinite(expires_in))
                or expires_in <= 0
            ):
                raise ValueError
            # Accept the provider's finite positive lifetime, but never retain a
            # token locally for more than 24 hours regardless of that lifetime.
            lifetime = float(min(expires_in, 86400))
            return token, lifetime
        except Exception:
            raise SourceHttpError("invalid_response") from None

    @staticmethod
    def _lease(state: _TokenState) -> CredentialLease:
        if state.access_token is None or state.secret_token is None:
            raise SourceHttpError("credentials_unavailable") from None
        return CredentialLease(
            f"Bearer {state.access_token}", state.generation, state.secret_token
        )


def _client_field(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value)
    ):
        raise ValueError
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


__all__ = [
    "SourceCredentialPolicy",
    "SourceCredentialService",
    "bind_source_credential_declarations",
]
