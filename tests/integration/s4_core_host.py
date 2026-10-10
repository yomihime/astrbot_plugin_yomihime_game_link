"""Controlled external Host ports driving the public actual Core ingress."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib
import json
import platform
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import fields, replace
from pathlib import Path

deployment = Path(sys.argv[1]).resolve()
plugin_root = deployment / "plugin"
mode = sys.argv[2] if len(sys.argv) > 2 else "minimal"
sys.path.insert(0, str(deployment))
sys.path.insert(0, str(plugin_root))

# Execute the original entry's guard definitions, without its AstrBot entry.
tree = ast.parse((plugin_root / "main.py").read_text(encoding="utf-8"))
guard_nodes = []
for node in tree.body:
    if (
        isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith("astrbot")
    ):
        break
    if isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "SDK" for t in node.targets
    ):
        break
    guard_nodes.append(node)
guard = {"__file__": str(plugin_root / "main.py"), "__name__": "s4_original_guard"}
exec(
    compile(
        ast.Module(body=guard_nodes, type_ignores=[]),
        str(plugin_root / "main.py"),
        "exec",
    ),
    guard,
)
sdk = guard["bootstrap_sdk"](plugin_root)
assert (
    Path(sdk.__file__).resolve() == plugin_root / "yomihime_game_link_sdk/__init__.py"
)
for name in guard["SDK_MODULES"]:
    assert (
        Path(sys.modules[name].__spec__.origin)
        .resolve()
        .is_relative_to(plugin_root / "yomihime_game_link_sdk")
    )

# Keep Core imports after the exact main bootstrap and its origin checks.
_core_runtime = importlib.import_module("plugin.services.core_runtime")
CoreRuntime = _core_runtime.CoreRuntime
HostIngress = _core_runtime.HostIngress
TrustedSubscriptionGate = _core_runtime.TrustedSubscriptionGate
_ports = importlib.import_module("plugin.core.ports")
MessageReceipt = _ports.MessageReceipt
MessageStatus = _ports.MessageStatus
_administration = importlib.import_module("plugin.core.contracts.administration")
AdminOperation = _administration.AdminOperation
AdminAuthorizationDenied = _administration.AdminAuthorizationDenied
_config = importlib.import_module("plugin.core.contracts.services")
ConfigPatch = _config.ConfigPatch
ConfigFieldUpdate = _config.ConfigFieldUpdate
discover_packages = importlib.import_module(
    "plugin.extensions.discovery"
).discover_packages
_rendering = importlib.import_module("plugin.presentation.rendering")
GenericDisplayRenderer = _rendering.GenericDisplayRenderer
RenderingBounds = _rendering.RenderingBounds
windows_fs = importlib.import_module("plugin.extensions.windows_fs")

windows_fs._check_runtime()
print(
    json.dumps(
        {
            "phase": "native_preflight",
            "windows": windows_fs._native_windows_version(),
            "python": platform.python_version(),
            "machine": platform.machine(),
            "sdk": sdk.__version__,
        }
    ),
    flush=True,
)
assert (
    importlib.import_module("plugin.core.invocation").CapabilityResult
    is sdk.CapabilityResult
)
assert not any(name == "astrbot" or name.startswith("astrbot.") for name in sys.modules)


class OfflineTransport:
    def __init__(self):
        self.requests = 0
        self.closed = False

    async def request(self, *args, **kwargs):
        self.requests += 1
        raise AssertionError("unexpected external HTTP")

    async def close(self):
        self.closed = True


class ControlledHost:
    def __init__(self):
        self.events = {}
        self.sent = []
        self.tool_registry = {}

    def command_event(self, actor="offline:alice", session="direct-alice"):
        receipt = object()
        event = HostIngress(
            "offline", actor, session, session, sdk.ConversationKind.DIRECT, receipt
        )
        self.events[id(receipt)] = (
            event,
            self.facts(event),
            sdk.InvocationOrigin.COMMAND,
        )
        return event

    @staticmethod
    def facts(event):
        return (
            event.adapter_id,
            event.actor_id,
            event.conversation_id,
            event.delivery_route,
            event.conversation_kind,
            event.message_text,
            event.message_correlation,
        )

    def tool_event(self, runtime, actor="offline:alice", session="direct-alice"):
        assert runtime.registry.snapshot().tool("sample_message") == (
            "offline_sample/status",
            "message",
        )
        event = HostIngress(
            "offline",
            actor,
            session,
            session,
            sdk.ConversationKind.DIRECT,
            object(),
            message_text="sample-private-message",
            message_correlation="sample-private-correlation",
        )
        self.events[id(event.evidence)] = (
            event,
            self.facts(event),
            sdk.InvocationOrigin.LLM_TOOL,
        )
        self.tool_registry[id(event.evidence)] = (
            runtime,
            runtime.registry.snapshot().module("offline_sample/status").epoch,
        )
        return event

    def revoke(self, event):
        self.events.pop(id(event.evidence), None)
        self.tool_registry.pop(id(event.evidence), None)

    def current(self, origin, ingress):
        owned = self.events.get(id(ingress.evidence))
        current = bool(
            owned
            and owned[0] is ingress
            and owned[2] is origin
            and owned[1] == self.facts(ingress)
        )
        if current and origin is sdk.InvocationOrigin.LLM_TOOL:
            runtime, epoch = self.tool_registry[id(ingress.evidence)]
            module = runtime.registry.snapshot().modules.get("offline_sample/status")
            current = bool(
                module
                and module.enabled
                and module.epoch == epoch
                and runtime.registry.snapshot().tools.get("sample_message")
                == ("offline_sample/status", "message")
            )
        return current

    def validate(self, origin, ingress):
        return self.current(origin, ingress)

    async def send(self, target, payload):
        self.sent.append((target, payload))
        return MessageReceipt(MessageStatus.ACCEPTED, "offline-accepted")

    def close(self):
        self.events.clear()
        self.tool_registry.clear()


def region_validator(value):
    if type(value) is not str or value not in {"cn", "global"}:
        raise ValueError("invalid region")


def gate_validator(value):
    if type(value) is not bool:
        raise ValueError("invalid gate")


@contextmanager
def admin_request(
    runtime,
    operation,
    *,
    owner="offline_sample/status",
    resource_fields=frozenset({"__module_lifecycle__"}),
):
    resources = {sdk.ConfigTarget("s4-task", owner): resource_fields}
    operations = (
        {operation, AdminOperation.SET_ENABLED}
        if operation is AdminOperation.UNLOAD_MODULE
        else {operation}
    )
    source = runtime.admin_authorization.register_source(
        "s4-" + operation.value + "-" + str(time.monotonic_ns()),
        resources=resources,
        operations=operations,
    )
    request = object()
    active = [True]
    context = source.issue(
        subject="offline-verified-admin",
        request=request,
        expiry=time.time() + 60,
        operations=operations,
        resources=resources,
        live=lambda item: active[0] and item is request,
    )
    try:
        yield context
    finally:
        active[0] = False
        source.end(context)
        source.close()


async def enable(runtime, enabled=True):
    with admin_request(runtime, AdminOperation.SET_ENABLED) as context:
        return await runtime.admin_operations.set_enabled(
            None,
            "offline_sample/status",
            enabled,
            expected_registry_revision=runtime.registry.snapshot().revision,
            authorization=context,
        )


async def command(
    runtime, host, operation, parameters, actor="offline:alice", session="direct-alice"
):
    return await runtime.invoke_command(
        "offline_sample/status",
        operation,
        parameters,
        ingress=host.command_event(actor, session),
    )


async def denied(call):
    try:
        await call()
    except (sdk.AccessDenied, sdk.ServiceUnavailable, sdk.OperationTimeout):
        return
    raise AssertionError("old or foreign authority accepted")


def sample_instance(runtime):
    return (
        runtime.registry.snapshot()
        .module("offline_sample/status")
        .handlers.capabilities["state"]
        .owner
    )


async def ended_scope(scope):
    await denied(lambda: scope.records.collection("notebook"))
    await denied(lambda: scope.cache.get("note"))
    await denied(lambda: scope.message.read())
    work = asyncio.sleep(0)
    try:
        try:
            scope.tasks.create_task(work, name="old-scope-negative")
        except (sdk.AccessDenied, sdk.ServiceUnavailable, sdk.OperationTimeout):
            pass
        else:
            raise AssertionError("old task scope accepted")
    finally:
        work.close()


def compose(data, host, transport, *, gate=True):
    trusted = {}
    if mode != "minimal" and gate:
        (package,) = discover_packages(deployment / "extensions")
        assert package.valid and package._provenance is not None
        manifest = next(m for m in package.manifest.modules if m.module_id == "status")
        assert (
            hashlib.sha256(
                (
                    deployment / "extensions/offline_sample/yomihime.manifest.json"
                ).read_bytes()
            ).digest()
            == package._provenance.manifest_sha256
        )
        trusted = {
            "trusted_bundled_manifests": {"offline_sample/status": manifest},
            "trusted_subscription_gates": {
                "offline_sample/status": TrustedSubscriptionGate(
                    manifest,
                    "sample_subscriptions_enabled",
                    package._provenance.manifest_sha256,
                )
            },
        }
    return CoreRuntime(
        database=data / "core.sqlite3",
        extension_root=deployment / "extensions",
        file_root=data / "files",
        secret_root=data / "secrets",
        secret_codec=None,
        http_transport=transport,
        renderer=GenericDisplayRenderer(
            RenderingBounds(4096, 100, 32, 64, 1024, 1024, 8, 32, 32)
        ),
        display_limits=sdk.DisplayLimits(2, 4096),
        message_port=host,
        admin_context_validator=lambda *_: False,
        host_ingress_validator=host.validate,
        host_message_current_validator=host.current,
        config_principal_id="s4-task",
        identity_namespace="s4-task",
        managed_module_owners={"offline_sample/status", "offline_sample/source"},
        module_config_validators={
            "offline_sample/status": {
                "region": region_validator,
                "sample_subscriptions_enabled": gate_validator,
            }
        },
        **trusted,
    )


async def expanded(runtime, host):
    instance = sample_instance(runtime)
    assert instance.start_count == 1 and instance.stop_count == 0
    assert {f.name for f in fields(instance._services)} == {
        "config",
        "identities",
        "accounts",
        "subscriptions",
        "scopes",
    }
    assert type(instance._services) is sdk.ModuleServices
    assert not any(
        hasattr(instance._services, name)
        for name in ("admin", "database", "repository", "issuer", "authorization")
    )
    with admin_request(
        runtime, AdminOperation.UPDATE_CONFIG, resource_fields=frozenset({"region"})
    ) as context:
        current = await runtime.config_repository.current(
            sdk.ConfigTarget("s4-task", "offline_sample/status")
        )
        patch = ConfigPatch(
            current.revision,
            (ConfigFieldUpdate("region", sdk.ConfigUpdateMode.REPLACE, "cn"),),
        )
        await runtime.admin_operations.update_config(
            None, "offline_sample/status", patch, authorization=context
        )
    from plugin.adapters.astrbot.command_bridge import (
        AstrBotCommandBridge,
        CommandInvocation,
    )

    class Event:
        def get_message_str(self):
            return "/ygl sample state action=write value=7"

    action = AstrBotCommandBridge(runtime.registry).parse(Event())
    assert isinstance(action, CommandInvocation), action
    first = await runtime.invoke_command(
        action.module_id,
        action.operation_path,
        action.parameters,
        ingress=host.command_event(),
    )
    assert (
        first.result.status is sdk.ResultStatus.SUCCESS
        and first.output.status.value == "sent"
    ), first
    alice = instance.observations["record"]
    assert alice.value["value"] == 7 and instance.observations["region"] == "cn"
    await ended_scope(instance.last_scope)
    cached = await command(runtime, host, "state", {"action": "cache"})
    assert cached.result.status is sdk.ResultStatus.SUCCESS, cached
    assert instance.observations["lookup"].entry == instance.observations["cache"]
    assert instance.observations["missing"].entry is None
    assert instance.observations["expired"].status is sdk.CacheLookupStatus.EXPIRED
    stored_cache = instance.observations["cache"]
    await command(
        runtime,
        host,
        "state",
        {"action": "read"},
        actor="offline:bob",
        session="direct-bob",
    )
    assert instance.observations["record"] is None
    assert instance.observations["cache"].status is sdk.CacheLookupStatus.MISS
    await command(
        runtime,
        host,
        "state",
        {"action": "write", "value": 9},
        actor="offline:bob",
        session="direct-bob",
    )
    bob = instance.observations["record"]
    assert bob.value["value"] == 9
    await command(
        runtime, host, "state", {"action": "read"}, session="alice-second-session"
    )
    assert instance.observations["record"] == alice
    outcome = await command(runtime, host, "state", {"action": "command_message"})
    assert outcome.result.status is sdk.ResultStatus.ERROR
    assert instance.observations["public_error_code"] == "service_unavailable"
    error = await command(runtime, host, "public error", {})
    assert (
        error.result.status is sdk.ResultStatus.ERROR and error.result.document is None
    )
    assert error.result.error.code is sdk.ErrorCode.NO_RECORDS
    assert "supplement" in error.result.model_facts.facts
    message = host.tool_event(runtime)
    outcome = await runtime.invoke_tool(
        "offline_sample/status", "sample_message", {}, ingress=message
    )
    assert outcome.result.status is sdk.ResultStatus.SUCCESS
    assert instance.observations["message_type"] is sdk.MessageContext
    await ended_scope(instance.last_scope)
    copied = replace(message, conversation_id="foreign-session")
    await denied(
        lambda: runtime.invoke_tool(
            "offline_sample/status", "sample_message", {}, ingress=copied
        )
    )
    print("PHASE B_config_records_cache_errors_tool E_identity_session", flush=True)

    handlers = (
        runtime.registry.snapshot()
        .module("offline_sample/status")
        .handlers.capabilities
    )
    params = {
        "type_id": "public_watch",
        "region": "cn",
        "minimum": 0,
        "mode": "instant",
    }
    created = await command(runtime, host, "subscription create", params)
    assert created.result.status is sdk.ResultStatus.SUCCESS, created
    a = handlers["subscription_create"].last_view
    assert a.revision == 1
    await command(
        runtime,
        host,
        "subscription create",
        params,
        actor="offline:bob",
        session="direct-bob",
    )
    b = handlers["subscription_create"].last_view
    assert b.owner_id != a.owner_id
    a_link = await runtime.b04_repositories.jobs.current_for_subscription(
        a.subscription_id
    )
    b_link = await runtime.b04_repositories.jobs.current_for_subscription(
        b.subscription_id
    )
    assert (
        a_link is not None
        and b_link is not None
        and a_link.collection_key == b_link.collection_key
    )
    await command(runtime, host, "subscription list", {})
    assert [v.subscription_id for v in handlers["subscription_list"].last_views] == [
        a.subscription_id
    ]
    revised = {
        "subscription_id": a.subscription_id,
        "expected_revision": a.revision,
        "region": "cn",
        "minimum": 1,
    }
    assert (
        await command(runtime, host, "subscription revise", revised)
    ).result.status is sdk.ResultStatus.SUCCESS
    a2 = handlers["subscription_revise"].last_view
    assert a2.revision == 2
    a2_link = await runtime.b04_repositories.jobs.current_for_subscription(
        a.subscription_id
    )
    assert a2_link is not None
    assert (
        await command(runtime, host, "subscription revise", revised)
    ).result.status is sdk.ResultStatus.ERROR
    assert (
        await command(runtime, host, "subscription list", {})
    ).result.status is sdk.ResultStatus.SUCCESS
    assert handlers["subscription_list"].last_views == (a2,)
    assert (
        await runtime.b04_repositories.jobs.current_for_subscription(a.subscription_id)
        == a2_link
    )
    assert (
        await runtime.b04_repositories.jobs.current_for_subscription(b.subscription_id)
        == b_link
    )
    assert (
        await command(
            runtime,
            host,
            "subscription cancel",
            {"subscription_id": a.subscription_id, "expected_revision": 1},
        )
    ).result.status is sdk.ResultStatus.ERROR
    assert (
        await command(runtime, host, "subscription list", {})
    ).result.status is sdk.ResultStatus.SUCCESS
    assert handlers["subscription_list"].last_views == (a2,)
    assert (
        await runtime.b04_repositories.jobs.current_for_subscription(a.subscription_id)
        == a2_link
    )
    assert (
        await runtime.b04_repositories.jobs.current_for_subscription(b.subscription_id)
        == b_link
    )
    assert (
        await command(
            runtime,
            host,
            "subscription cancel",
            {"subscription_id": a.subscription_id, "expected_revision": 2},
        )
    ).result.status is sdk.ResultStatus.SUCCESS
    assert (
        await runtime.b04_repositories.jobs.current_for_subscription(a.subscription_id)
        is None
    )
    assert (
        await runtime.b04_repositories.jobs.current_for_subscription(b.subscription_id)
        == b_link
    )
    await command(
        runtime,
        host,
        "subscription list",
        {},
        actor="offline:bob",
        session="direct-bob",
    )
    assert [v.subscription_id for v in handlers["subscription_list"].last_views] == [
        b.subscription_id
    ]
    assert (
        await command(
            runtime,
            host,
            "subscription cancel",
            {"subscription_id": b.subscription_id, "expected_revision": 1},
            actor="offline:bob",
            session="direct-bob",
        )
    ).result.status is sdk.ResultStatus.SUCCESS
    print(
        "PHASE C_subscriptions_create_list_revise_staleCAS_cancel_shared_owner",
        flush=True,
    )

    # Module finally ends actual SDK-spawned work on normal completion.
    instance.hold_entered.clear()
    instance.hold_release.clear()
    flight = asyncio.create_task(command(runtime, host, "state", {"action": "hold"}))
    await asyncio.wait_for(instance.hold_entered.wait(), 5)
    scope = instance.last_scope
    instance.hold_release.set()
    assert (await flight).result.status is sdk.ResultStatus.SUCCESS
    assert instance.task_finished.is_set()
    await ended_scope(scope)
    # Cancellation closes the issued scope; module finally ends child work.
    instance.hold_entered.clear()
    instance.hold_release.clear()
    flight = asyncio.create_task(command(runtime, host, "state", {"action": "hold"}))
    await asyncio.wait_for(instance.hold_entered.wait(), 5)
    scope = instance.last_scope
    flight.cancel()
    try:
        await flight
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("public invocation cancellation did not propagate")
    await asyncio.wait_for(instance.task_finished.wait(), 5)
    await ended_scope(scope)

    # Host receipt revocation is separate from module admission revocation.
    instance.hold_entered.clear()
    instance.hold_release.clear()
    message = host.tool_event(runtime)
    flight = asyncio.create_task(
        runtime.invoke_tool(
            "offline_sample/status", "sample_message", {"hold": True}, ingress=message
        )
    )
    await asyncio.wait_for(instance.hold_entered.wait(), 5)
    scope = instance.last_scope
    host.revoke(message)
    await denied(lambda: scope.message.read())
    sent_before = len(host.sent)
    instance.hold_release.set()
    await denied(lambda: flight)
    assert len(host.sent) == sent_before

    await enable(runtime, False)
    await ended_scope(scope)
    await enable(runtime, True)
    instance = sample_instance(runtime)
    instance.hold_entered.clear()
    instance.hold_release.clear()
    flight = asyncio.create_task(command(runtime, host, "state", {"action": "late"}))
    await asyncio.wait_for(instance.hold_entered.wait(), 5)
    old_scope = instance.last_scope
    old_services = instance._services
    old_generation = runtime.extension_runtime.owner_generation("offline_sample/status")
    sent_before = len(host.sent)

    # Release the finite module barrier after unload has begun; do not change
    # the Core safety deadlines or substitute any cancellation implementation.
    async def release_late():
        while (
            runtime.registry.snapshot().modules.get("offline_sample/status") is not None
            and runtime.registry.snapshot().modules["offline_sample/status"].enabled
        ):
            await asyncio.sleep(0)
        instance.hold_release.set()

    release = asyncio.create_task(release_late())
    try:
        with admin_request(runtime, AdminOperation.UNLOAD_MODULE) as context:
            receipt = await runtime.admin_operations.unload_module(
                None,
                "offline_sample/status",
                expected_registry_revision=runtime.registry.snapshot().revision,
                authorization=context,
            )
        assert receipt.data_retained is True and receipt.reopen_required is True
    finally:
        instance.hold_release.set()
        await release
    late = await flight
    assert late.result is None, late
    assert (
        late.output.status.value == "failed"
        and late.output.error_code == "invocation_unavailable"
    ), late
    assert late.output.result is None and late.output.receipt is None
    assert instance.late_returns == 1, "late handler did not actually return"
    assert len(host.sent) == sent_before
    assert instance.stop_count >= 1
    snapshot = runtime.registry.snapshot()
    assert (
        "offline_sample/status" not in snapshot.modules
        and "sample" not in snapshot.routes
    )
    assert not any(
        owner == "offline_sample/status" for owner, _ in snapshot.tools.values()
    )
    await ended_scope(old_scope)
    await enable(runtime, True)
    new_instance = sample_instance(runtime)
    assert new_instance is not instance
    assert (
        runtime.extension_runtime.owner_generation("offline_sample/status")
        > old_generation
    )
    assert (
        await command(runtime, host, "state", {"action": "read"})
    ).result.status is sdk.ResultStatus.SUCCESS
    assert (
        new_instance.observations["record"] == alice
        and new_instance.observations["region"] == "cn"
    )
    assert new_instance.observations["cache"].status is sdk.CacheLookupStatus.HIT
    assert new_instance.observations["cache"].entry == stored_cache
    await ended_scope(old_scope)
    await denied(lambda: old_services.config.current())
    print("PHASE D_end_cancel_revoke_unload_late_restore_old_refs_fenced", flush=True)
    for _, payload in host.sent:
        text = repr(payload)
        assert all(
            marker not in text
            for marker in (
                "sample-private-note",
                "sample-private-message",
                "sample-private-correlation",
            )
        )
    assert not runtime._host_flights
    return alice, old_scope, stored_cache


async def revoked(operation):
    try:
        await operation()
    except sdk.InvalidInvocation as error:
        assert (
            type(error) is sdk.InvalidInvocation and error.code == "invalid_invocation"
        )
    else:
        raise AssertionError("retired instance bundle remained usable")


async def close_checked(runtime, host):
    instance = sample_instance(runtime)
    services = instance._services
    instance.hold_entered.clear()
    instance.hold_release.clear()
    flight = asyncio.create_task(command(runtime, host, "state", {"action": "hold"}))
    try:
        await asyncio.wait_for(instance.hold_entered.wait(), 5)
        view = instance.last_scope.invocation
        operations = (
            lambda: services.config.current(),
            lambda: services.scopes.bind(view),
            lambda: services.identities.default_identity(view),
            lambda: services.accounts.bindings(view),
            lambda: services.subscriptions.list_current(view),
        )
        for operation in operations:
            await operation()
        closed = await runtime.close()
        result = (await asyncio.gather(flight, return_exceptions=True))[0]
        assert isinstance(result, asyncio.CancelledError), result
        assert instance.task_finished.is_set()
        for operation in operations:
            await revoked(operation)
        return closed
    finally:
        instance.hold_release.set()
        if not flight.done():
            flight.cancel()
            await asyncio.gather(flight, return_exceptions=True)


async def stale_services(runtime, host):
    old_instance = sample_instance(runtime)
    assert (
        old_instance.factory_config_revision >= 1
        and old_instance.start_config_revision >= 1
    )
    old = old_instance._services
    assert await old.config.current() is not None
    with admin_request(runtime, AdminOperation.UNLOAD_MODULE) as context:
        receipt = await runtime.admin_operations.unload_module(
            None,
            "offline_sample/status",
            expected_registry_revision=runtime.registry.snapshot().revision,
            authorization=context,
        )
    assert receipt.data_retained is True
    await enable(runtime, True)
    new = sample_instance(runtime)._services
    assert new is not old and await new.config.current() is not None
    await revoked(lambda: old.config.current())
    instance = sample_instance(runtime)
    flight = asyncio.create_task(command(runtime, host, "state", {"action": "hold"}))
    try:
        await asyncio.wait_for(instance.hold_entered.wait(), 5)
        view = instance.last_scope.invocation
        await new.scopes.bind(view)
        await revoked(lambda: old.scopes.bind(view))
        await new.identities.default_identity(view)
        await revoked(lambda: old.identities.default_identity(view))
        await new.accounts.bindings(view)
        await revoked(lambda: old.accounts.bindings(view))
        await new.subscriptions.list_current(view)
        await revoked(lambda: old.subscriptions.list_current(view))
    finally:
        instance.hold_release.set()
        outcome = await flight
        assert outcome.result.status is sdk.ResultStatus.SUCCESS
    assert old_instance.stop_config_revision >= 1
    print("PHASE stale_module_services_revoked_after_actual_restore", flush=True)


async def gate_denied(runtime, host):
    handlers = (
        runtime.registry.snapshot()
        .module("offline_sample/status")
        .handlers.capabilities
    )
    result = await command(
        runtime,
        host,
        "subscription create",
        {"type_id": "public_watch", "region": "cn", "minimum": 0, "mode": "instant"},
    )
    assert result.result.status is sdk.ResultStatus.ERROR
    assert handlers["subscription_create"].last_view is None
    await command(runtime, host, "subscription list", {})
    assert handlers["subscription_list"].last_views == ()
    print("PHASE subscription_gate_denied_no_rows", flush=True)


async def negative_contracts(runtime, host, data):
    original = json.loads(
        (deployment / "extensions/offline_sample/yomihime.manifest.json").read_text(
            encoding="utf-8"
        )
    )
    from plugin.extensions.loader import CandidateState, ExtensionLoader

    for name in (
        "schema",
        "abi",
        "output-current",
        "output",
        "unknown-field",
        "declaration",
        "policy",
    ):
        manifest = json.loads(json.dumps(original))
        if name == "schema":
            manifest["schema_version"] = 9
        elif name == "abi":
            manifest["contract_version"] = "9.0"
        elif name == "output":
            manifest["modules"][0]["capabilities"][0]["output_version"] = "9.0"
        elif name == "unknown-field":
            manifest["modules"][0]["capabilities"][0]["output_schema_version"] = "9.0"
        elif name == "output-current":
            assert (
                manifest["modules"][0]["capabilities"][0].get("output_version", "1.8.0")
                == "1.8.0"
            )
        elif name == "declaration":
            manifest["modules"][0]["commands"][0]["capability_id"] = "undeclared"
        else:
            manifest["modules"][0]["capabilities"][0]["invocation_policy"] = (
                "self_authorized"
            )
        root = data / "negative" / name
        package = root / "offline_sample"
        package.mkdir(parents=True)
        (package / "yomihime.manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (package / "module.py").write_text(
            "raise AssertionError('invalid module imported')\n", encoding="utf-8"
        )
        (rejected,) = discover_packages(root)
        (candidate,) = ExtensionLoader().scan(root)
        if name == "output-current":
            assert (
                rejected.valid
                and rejected.manifest is not None
                and rejected.diagnostic is None
            )
            assert (
                candidate.state is CandidateState.DISABLED
                and candidate.reason_code == "not_enabled"
            )
            continue
        assert not rejected.valid and rejected.manifest is None and rejected.diagnostic
        assert (
            candidate.state is CandidateState.INVALID
            and candidate.reason_code == "manifest_invalid"
        )
        if name == "output":
            assert rejected.diagnostic == "manifest descriptor validation failed"
            manifest["modules"][0]["capabilities"][0]["output_version"] = "1.8.0"
            restored = json.loads(json.dumps(original))
            restored["modules"][0]["capabilities"][0]["output_version"] = "1.8.0"
            assert manifest == restored, (
                "unsupported output control changed another factor"
            )
        elif name == "unknown-field":
            assert rejected.diagnostic == "unknown capability field"
    try:
        await runtime.admin_operations.set_enabled(
            None,
            "offline_sample/status",
            False,
            expected_registry_revision=runtime.registry.snapshot().revision,
            authorization=object(),
        )
    except AdminAuthorizationDenied:
        pass
    else:
        raise AssertionError("forged administration accepted")
    with admin_request(runtime, AdminOperation.SET_ENABLED) as ended:
        pass
    try:
        await runtime.admin_operations.set_enabled(
            None,
            "offline_sample/status",
            False,
            expected_registry_revision=runtime.registry.snapshot().revision,
            authorization=ended,
        )
    except AdminAuthorizationDenied:
        pass
    else:
        raise AssertionError("ended administration accepted")
    source = runtime.admin_authorization.register_source(
        "expiry-negative",
        resources={
            sdk.ConfigTarget("s4-task", "offline_sample/status"): {
                "__module_lifecycle__"
            }
        },
        operations={AdminOperation.SET_ENABLED},
    )
    try:
        try:
            source.issue(
                subject="offline-verified-admin",
                request=object(),
                expiry=time.time() - 1,
                operations=source.operations,
                resources=source.resources,
                live=lambda _: True,
            )
        except AdminAuthorizationDenied:
            pass
        else:
            raise AssertionError("expired context minted")
    finally:
        source.close()
    with admin_request(
        runtime,
        AdminOperation.UPDATE_CONFIG,
        resource_fields=frozenset({"sample_subscriptions_enabled"}),
    ) as context:
        current = await runtime.config_repository.current(
            sdk.ConfigTarget("s4-task", "offline_sample/status")
        )
        patch = ConfigPatch(
            current.revision,
            (
                ConfigFieldUpdate(
                    "sample_subscriptions_enabled", sdk.ConfigUpdateMode.REPLACE, False
                ),
            ),
        )
        await runtime.admin_operations.update_config(
            None, "offline_sample/status", patch, authorization=context
        )
    await gate_denied(runtime, host)
    print(
        "PHASE A_negative_native_schema_abi_output_declaration_policy E_admin",
        flush=True,
    )


async def main():
    host = ControlledHost()
    transport = OfflineTransport()
    before = {t.ident for t in threading.enumerate()}
    data = deployment / "data"
    data.mkdir()
    runtime = compose(data, host, transport, gate=mode != "nogate")
    source = None
    try:
        print("PHASE start", flush=True)
        report = await runtime.start()
        assert not report.config_failures and not report.extension_failures, report
        from plugin.extensions.discovery import discover_packages

        print(
            "DISCOVERY",
            [
                (p.package_id, p.diagnostic)
                for p in discover_packages(deployment / "extensions")
            ],
            flush=True,
        )
        assert runtime.extension_runtime.candidate("offline_sample") is not None
        print("PHASE startup_discovered", flush=True)
        resources = {
            sdk.ConfigTarget("s4-task", owner): frozenset({"__module_lifecycle__"})
            for owner in ("offline_sample/status", "offline_sample/source")
        }
        source = runtime.admin_authorization.register_source(
            "s4-admin", resources=resources, operations={AdminOperation.SET_ENABLED}
        )
        request = object()
        active = True
        context = source.issue(
            subject="offline-verified-admin",
            request=request,
            expiry=time.time() + 60,
            operations={AdminOperation.SET_ENABLED},
            resources=resources,
            live=lambda item: active and item is request,
        )
        try:
            status = await runtime.admin_operations.set_enabled(
                None,
                "offline_sample/status",
                True,
                expected_registry_revision=runtime.registry.snapshot().revision,
                authorization=context,
            )
            assert status.lifecycle.value == "active", status
        finally:
            active = False
            source.end(context)
        print("PHASE factory_lifecycle_active", flush=True)
        outcome = await runtime.invoke_command(
            "offline_sample/status", "status", {}, ingress=host.command_event()
        )
        assert isinstance(outcome.result, sdk.CapabilityResult)
        assert outcome.result.status is sdk.ResultStatus.SUCCESS, outcome.result
        assert outcome.output.status.value == "sent", outcome.output
        assert len(host.sent) == 1
        assert "offline" in repr(host.sent[0][1]).lower()
        assert not runtime._host_flights
        assert transport.requests == 0
        print("PHASE public_command_gateway_output_sent", flush=True)
        if mode == "expanded":
            saved, old_scope, stored_cache = await expanded(runtime, host)
        elif mode == "restart":
            with admin_request(
                runtime,
                AdminOperation.UPDATE_CONFIG,
                resource_fields=frozenset({"region"}),
            ) as context:
                current = await runtime.config_repository.current(
                    sdk.ConfigTarget("s4-task", "offline_sample/status")
                )
                update = ConfigPatch(
                    current.revision,
                    (ConfigFieldUpdate("region", sdk.ConfigUpdateMode.REPLACE, "cn"),),
                )
                await runtime.admin_operations.update_config(
                    None, "offline_sample/status", update, authorization=context
                )
            assert (
                await command(runtime, host, "state", {"action": "write", "value": 7})
            ).result.status is sdk.ResultStatus.SUCCESS
            instance = sample_instance(runtime)
            saved = instance.observations["record"]
            assert (
                await command(runtime, host, "state", {"action": "cache"})
            ).result.status is sdk.ResultStatus.SUCCESS
            old_scope = instance.last_scope
            stored_cache = instance.observations["cache"]
        elif mode == "stale":
            await stale_services(runtime, host)
        elif mode == "negative":
            await negative_contracts(runtime, host, data)
        elif mode == "nogate":
            await gate_denied(runtime, host)
    finally:
        if source is not None:
            source.close()
        closed = (
            await runtime.close()
            if mode == "minimal"
            else await close_checked(runtime, host)
        )
        host.close()
        await transport.close()
        assert closed and runtime.closed and runtime.extension_runtime.closed
        assert not runtime._host_flights
        assert runtime._pump_task is None or runtime._pump_task.done()
        assert (
            runtime.database.executor._worker is None
            or not runtime.database.executor._worker.is_alive()
        )
        # Source capture uses the loop's standard to_thread executor, owned by
        # this harness event loop rather than by CoreRuntime.close().
        if mode not in {"expanded", "restart"}:
            await asyncio.get_running_loop().shutdown_default_executor()
            assert not ({t.ident for t in threading.enumerate()} - before), (
                "owned worker thread remains"
            )
        print("PHASE core_closed_workers_zero", flush=True)
    if mode in {"expanded", "restart"}:
        host = ControlledHost()
        transport = OfflineTransport()
        restored = compose(data, host, transport)
        try:
            report = await restored.start()
            assert not report.config_failures and not report.extension_failures, report
            outcome = await command(restored, host, "state", {"action": "read"})
            assert outcome.result.status is sdk.ResultStatus.SUCCESS
            instance = sample_instance(restored)
            assert instance.observations["record"] == saved
            assert instance.observations["region"] == "cn"
            assert instance.observations["cache"].status is sdk.CacheLookupStatus.HIT
            assert instance.observations["cache"].entry == stored_cache
            await ended_scope(old_scope)
            print("PHASE D_new_Core_same_DB_restore_preserved_data", flush=True)
        finally:
            assert await close_checked(restored, host)
            host.close()
            await transport.close()
            await asyncio.get_running_loop().shutdown_default_executor()
            assert not ({t.ident for t in threading.enumerate()} - before)
    print(mode.upper() + "_PASS", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
