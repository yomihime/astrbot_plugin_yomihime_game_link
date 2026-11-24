"""Host-approved bearer read of one fixed resource for the current player."""

import re
from dataclasses import dataclass, replace

from ..core.contracts.validation_boundary import validate_contract
from ..core.ports import SecretOwner, validate_module_id
from ..infrastructure.http import CredentialLease
from ..yomihime_game_link_sdk.contexts import (
    InvocationConversationKind,
    InvocationOrigin,
)
from ..yomihime_game_link_sdk.declarations import PrivacyFloor, SourceDeclaration
from ..yomihime_game_link_sdk.errors import SourceHttpError
from ..yomihime_game_link_sdk.services import HttpRequest
from ..yomihime_game_link_sdk.subscriptions import ConversationKind
from .authorization import AuthorizationError


@dataclass(frozen=True, slots=True)
class PlayerReadPolicy:
    module_id: str
    source_id: str
    host: str
    path: str
    capability_id: str

    def __post_init__(self):
        validate_module_id(self.module_id, "player source module")
        validate_contract(SourceDeclaration(self.source_id, self.host, self.field))
        validate_contract(HttpRequest(self.source_id, self.path))
        if not self.capability_id or not re.fullmatch(r"/[A-Za-z0-9_./-]*", self.path):
            raise ValueError("expected a fixed resource and capability")

    @property
    def field(self):
        return f"credential_{self.source_id}"

    @property
    def scope(self):
        return f"GET https://{self.host}{self.path}"

    def bind(self, manifest, source):
        capability = next(
            (c for c in manifest.capabilities if c.capability_id == self.capability_id),
            None,
        )
        if (
            source.credential_ref is not None
            or source.authorization_only
            or source.host != self.host
            or capability is None
            or self.source_id not in capability.required_sources
            or capability.privacy_floor
            not in (PrivacyFloor.PRIVATE, PrivacyFloor.OWNER)
        ):
            raise ValueError(
                "player source does not match a declared private capability and host"
            )
        return validate_contract(
            replace(
                source,
                credential_ref=self.field,
                max_redirects=0,
                redirect_path_prefixes=(),
            )
        )

    async def acquire(
        self, invocation, request, authorization, repositories, *, owner_command
    ):
        if (
            (invocation.grant_id is None) != owner_command
            or invocation.module_id != self.module_id
            or invocation.capability_id != self.capability_id
            or invocation.origin is not InvocationOrigin.COMMAND
            or invocation.conversation_kind is not InvocationConversationKind.DIRECT
            or invocation.parent_id is not None
            or request.method != "GET"
            or request.path != self.path
            or request.source_id != self.source_id
            or request.headers
            or request.query
            or request.body is not None
        ):
            raise SourceHttpError("request_rejected")

        async def current():
            room = await repositories.conversations.current(
                invocation.adapter_id, invocation.conversation_id
            )
            if room is None or room.kind is not ConversationKind.DIRECT:
                raise SourceHttpError("request_rejected")
            try:
                if invocation.grant_id is None:
                    grant = await authorization.require_player_read_grant(
                        invocation, source_scope=self.scope, credential_field=self.field
                    )
                else:
                    grant = await authorization.require_current_grant(invocation)
            except AuthorizationError:
                raise SourceHttpError("credentials_unavailable") from None
            if (
                self.scope not in grant.scopes
                or not grant.secret_ref
                or grant.secret_ref.field != self.field
            ):
                raise SourceHttpError("credentials_unavailable")
            return grant

        grant = await current()
        ref = grant.secret_ref
        material = await repositories.secret_store.read(
            ref,
            owner=SecretOwner(
                grant.principal_id, self.module_id, self.field, ref.operation_id
            ),
        )
        if type(material) is not bytes or not re.fullmatch(
            rb"[A-Za-z0-9._~+/-]{1,2048}={0,2}", material
        ):
            raise SourceHttpError("credentials_unavailable")
        token = material.decode("ascii")

        async def check():
            if await current() != grant:
                raise SourceHttpError("credentials_unavailable")

        return CredentialLease(
            f"Bearer {token}", grant.revision, ref.token, redactions=(token,)
        ), check
