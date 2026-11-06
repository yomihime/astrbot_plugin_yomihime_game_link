"""Host-private reconciliation of reviewed PUBLIC subscription config targets.

No SDK port exposes this authority. Definitions come from the selected reviewed
assembly; targets come from the existing administrator-owned config repository.
All writes use the existing admission and subscription lifecycle transaction.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import replace

from yomihime_game_link_sdk.services import ConfigTarget
from yomihime_game_link_sdk.storage import OwnerScope, OwnershipKind
from yomihime_game_link_sdk.subscriptions import (
    CollectionKey,
    ConversationKind,
    ConversationRef,
)

from ..core.contracts.subscriptions import (
    SubscriptionJobAssociation,
    SubscriptionJobChange,
    SubscriptionJobChangeKind,
    SubscriptionRecord,
    SubscriptionStatus,
)
from ..core.contracts.validation_boundary import validate_contract
from ..core.ports import CollectionRunRequest
from .subscriptions import _schema_matches

_COMPONENT = re.compile(r"[^\s:/\\\x00-\x1f\x7f]{1,128}\Z")


def configured_route(value):
    if type(value) is not str:
        raise ValueError("configured_target_invalid")
    parts = value.split(":")
    if len(parts) != 3 or not all(_COMPONENT.fullmatch(part) for part in parts):
        raise ValueError("configured_target_invalid")
    adapter, message_type, conversation = parts
    kind = {
        "FriendMessage": ConversationKind.DIRECT,
        "GroupMessage": ConversationKind.GROUP,
    }.get(message_type)
    if kind is None:
        raise ValueError("configured_target_invalid")
    return validate_contract(ConversationRef(adapter, kind, conversation, adapter))


class ConfiguredPublicSubscriptions:
    def __init__(self, core, principal_id, definitions):
        self.core = core
        self.principal = principal_id
        self.definitions = dict(definitions)
        self.failures = ()

    async def reconcile(self):
        # Failure is isolated from the scheduler pump. No arbitrary stored config
        # selects a module, collector, filter, handler, or non-public destination.
        failures = []
        for module_id, definition in self.definitions.items():
            try:
                field, request = definition
                await self._module(module_id, field, request)
            except Exception:
                failures.append((module_id, "configured_subscription_unavailable"))
        self.failures = tuple(failures)

    async def _module(self, module_id, field, request):
        core = self.core
        if module_id not in core._trusted_bundled_manifests:
            raise ValueError("unreviewed_configured_subscription")
        module = core.registry.snapshot().module(module_id)
        if module.manifest != core._trusted_bundled_manifests[module_id]:
            raise ValueError("unreviewed_configured_subscription")
        if not module.enabled:
            return
        spec = next(f for f in module.manifest.config_fields if f.name == field)
        if spec.sensitive:
            raise ValueError("configured_target_field_invalid")
        operations = core.subscription_operations
        descriptor, schedule = operations._declarations(module, request.type_id)
        if (
            schedule.shared_scope is not OwnershipKind.PUBLIC
            or request.notification_mode != "instant"
            or request.digest_schedule is not None
            or request.subscription_id is not None
        ):
            raise ValueError("configured_subscription_must_be_public")
        if not _schema_matches(
            schedule.input_schema, request.collector_parameters
        ) or not _schema_matches(descriptor.filter_schema, request.filters):
            raise ValueError("configured_subscription_parameters_invalid")
        config_target = validate_contract(ConfigTarget(self.principal, module_id))
        async with core.lifecycle.admission.mutation("configured-public-subscriptions"):
            snapshot = await core.config_repository.current(config_target)
            targets = snapshot.values.get(field, ())
            if not isinstance(targets, (list, tuple)) or len(targets) > 20:
                raise ValueError("configured_targets_invalid")
            routes = {configured_route(value) for value in targets}
            live = core.registry.snapshot().module(module_id)
            if not live.enabled or live.epoch != module.epoch:
                return
            gate = await core.b04_repositories.lifecycle.read_subscription_gate_state(
                module_id
            )
            if not gate.supported or not gate.enabled or not gate.intent_enabled:
                return
            # Separate owner and creation namespace from command-managed rows.
            owner = (
                "configured:"
                + hashlib.sha256(
                    (self.principal + ":" + module_id + ":" + field).encode()
                ).hexdigest()
            )
            current = await core.b04_repositories.subscriptions.list_for_owner(
                owner, limit=21
            )
            active = [r for r in current if r.status is SubscriptionStatus.ACTIVE]
            if len(active) > 20:
                raise ValueError("configured_subscription_limit")
            for record in active:
                if record.recipient in routes:
                    continue
                link = await operations._current_link(record)
                if link is None:
                    raise ValueError("configured_subscription_link_missing")
                await core.b04_repositories.lifecycle.apply(
                    SubscriptionJobChange(
                        SubscriptionJobChangeKind.CANCEL,
                        replace(
                            record,
                            revision=record.revision + 1,
                            status=SubscriptionStatus.CANCELLED,
                        ),
                        None,
                        record.revision,
                        link.association_revision,
                    )
                )
            for route in sorted(
                routes,
                key=lambda route: (
                    route.adapter_id,
                    route.kind.value,
                    route.conversation_id,
                    route.delivery_route,
                ),
            ):
                if any(r.recipient == route for r in active):
                    continue
                # Append-only route storage refuses an existing conflicting kind
                # or delivery route; it never silently retargets a conversation.
                await core.repositories.conversations.save(route)
                collector = live.handlers.collectors[schedule.collector_id]
                normalized = collector.normalize(request.collector_parameters)
                key = validate_contract(
                    CollectionKey(
                        module_id,
                        schedule.collector_id,
                        schedule.key_version,
                        schedule.source_id,
                        normalized,
                        OwnerScope.public(),
                    )
                )
                cadence, revision = operations._cadence.resolve(module_id, schedule)
                record = validate_contract(
                    SubscriptionRecord(
                        secrets.token_urlsafe(18),
                        1,
                        module_id,
                        key,
                        owner,
                        None,
                        route,
                        "instant",
                        request.filters,
                        SubscriptionStatus.ACTIVE,
                        request.type_id,
                        None,
                        "configured-public-v1",
                    )
                )
                association = SubscriptionJobAssociation(
                    record.subscription_id, 1, key, cadence, revision, 1
                )
                registry = core.registry.snapshot()
                await core.b04_repositories.lifecycle.apply(
                    SubscriptionJobChange(
                        SubscriptionJobChangeKind.CREATE,
                        record,
                        association,
                        None,
                        None,
                    ),
                    initial_run=CollectionRunRequest(
                        key,
                        operations._current_time(),
                        cadence,
                        revision,
                        live.epoch,
                        registry.revision,
                    ),
                )
