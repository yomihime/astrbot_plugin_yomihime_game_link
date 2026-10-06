"""Installed-wheel CoreRuntime harness for the CORE-HARDENING-01 R tests.

The child probe is deliberately isolated: it adds the freshly installed SDK
site before importing the repository Core, then extracts the example only from
that installed package. Test doubles below the Core boundary model only the
host's renderer, message port, validator, codec, and clock.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

if __name__ != "__main__":
    from tests.fixtures.b05_runtime import (
        REPOSITORY_ROOT,
        InstalledSdk,
        build_and_install_pinned_sdk,
    )
else:
    REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


PROBE_ARGUMENT = "--core-hardening-probe"


class InstalledCoreHardeningWorkspace:
    """Build one pinned wheel and run real-Core scenarios in isolated children."""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="core-hardening-r-", dir=REPOSITORY_ROOT
        )
        self.root = Path(self._temporary.name)
        self.installed: InstalledSdk = build_and_install_pinned_sdk(self.root)

    def close(self) -> None:
        self._temporary.cleanup()

    def run(self, scenario: str) -> dict[str, object]:
        if scenario not in {"activation", "subscriptions", "reopen", "empty"}:
            raise ValueError("unknown CoreHardening scenario")
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                PROBE_ARGUMENT,
                str(self.installed.site_root),
                str(REPOSITORY_ROOT),
                str(self.root / scenario),
                scenario,
            ],
            check=False,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "isolated CoreHardening probe failed:\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        return json.loads(result.stdout)


def _isolated_probe_main() -> None:
    if len(sys.argv) != 6 or sys.argv[1] != PROBE_ARGUMENT:
        raise SystemExit("invalid isolated CoreHardening probe arguments")

    # `-I` ignores ambient PYTHONPATH. Install the site first and verify it
    # before adding or importing any repository-owned Core module.
    site_root, repository_root, work_root = map(Path, sys.argv[2:5])
    scenario = sys.argv[5]
    sys.path.insert(0, str(site_root))

    import asyncio
    import importlib
    import importlib.metadata
    import importlib.resources
    import importlib.util
    import threading
    from dataclasses import dataclass
    from datetime import UTC, datetime, timedelta
    from types import MappingProxyType

    sdk = importlib.import_module("yomihime_sdk")
    if not Path(sdk.__file__).resolve().is_relative_to(site_root.resolve()):
        raise AssertionError("canonical SDK did not load from the installed site")
    if importlib.metadata.version("yomihime-module-sdk") != "1.5.0":
        raise AssertionError("installed SDK version changed")

    sys.path.insert(1, str(repository_root))
    spec = importlib.util.spec_from_file_location(
        "ygl_core_hardening_subject",
        repository_root / "__init__.py",
        submodule_search_locations=[str(repository_root)],
    )
    if spec is None or spec.loader is None:
        raise AssertionError("repository Core package could not be loaded")
    plugin = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = plugin
    spec.loader.exec_module(plugin)

    from ygl_core_hardening_subject.api import contexts as core_contexts
    from ygl_core_hardening_subject.api import services as core_services
    from ygl_core_hardening_subject.api import storage as core_storage
    from ygl_core_hardening_subject.api.administration import (
        AdminAuthorizationDenied,
        AdminOperation,
    )
    from ygl_core_hardening_subject.api.display import (
        DisplayLimits,
        DisplayOutput,
    )
    from ygl_core_hardening_subject.api.services import (
        ConfigFieldUpdate,
        ConfigPatch,
        ConfigPatchMode,
        ConversationKey,
        Grant,
        GrantStatus,
        HealthStatus,
        Principal,
    )
    from ygl_core_hardening_subject.api.storage import GrantReference
    from ygl_core_hardening_subject.core.help_catalog import HelpCatalog
    from ygl_core_hardening_subject.core.ports import (
        MessageReceipt,
        MessageStatus,
        SecretOwner,
    )
    from ygl_core_hardening_subject.extensions.discovery import discover_packages
    from ygl_core_hardening_subject.extensions.source_snapshot import PackageProvenance
    from ygl_core_hardening_subject.infrastructure.sqlite.database import (
        SQLiteDatabase,
    )
    from ygl_core_hardening_subject.presentation.rendering import (
        GenericDisplayRenderer,
        RenderingBounds,
    )
    from ygl_core_hardening_subject.services.admin_authorization import (
        AdminCredentialOperation,
        _digest,
    )
    from ygl_core_hardening_subject.services.b05_runtime import (
        extract_installed_sdk_examples,
    )
    from ygl_core_hardening_subject.services.core_runtime import (
        CoreRuntime,
        HostIngress,
        TrustedSubscriptionGate,
    )

    from yomihime_sdk.api import contexts as sdk_contexts
    from yomihime_sdk.api import services as sdk_services
    from yomihime_sdk.api import storage as sdk_storage

    if core_contexts.InvocationOrigin is not sdk_contexts.InvocationOrigin:
        raise AssertionError("Core and installed SDK InvocationOrigin diverged")
    if core_services.Grant is not sdk_services.Grant:
        raise AssertionError("Core and installed SDK Grant class identity diverged")
    if core_storage.GrantReference is not sdk_storage.GrantReference:
        raise AssertionError("Core and installed SDK GrantReference identity diverged")

    @dataclass(frozen=True)
    class _AdminContext:
        adapter_id: str
        request_id: str
        session_id: str

    class _Clock:
        def __init__(self) -> None:
            self.current = datetime.now(UTC)

        def __call__(self) -> datetime:
            return self.current

        def advance(self, delta: timedelta) -> None:
            self.current += delta

    class _Codec:
        def encrypt(self, value: bytes) -> bytes:
            return b"offline-test-envelope:" + value[::-1]

        def decrypt(self, value: bytes) -> bytes:
            prefix = b"offline-test-envelope:"
            if not value.startswith(prefix):
                raise ValueError("invalid offline test envelope")
            return value[len(prefix) :][::-1]

    class _Renderer:
        async def render(self, document, *, limits, audience):
            del limits, audience
            return DisplayOutput(document.title)

        async def render_batch(self, batch, limits):
            del limits
            return DisplayOutput(
                "\n".join(item.document.title for item in batch.members)
            )

    class _MessagePort:
        def __init__(self) -> None:
            self.calls = []

        async def send(self, target, payload):
            self.calls.append((target, payload))
            return MessageReceipt(MessageStatus.ACCEPTED, f"offline-{len(self.calls)}")

    class _HttpTransport:
        async def request(self, request):
            del request
            raise AssertionError("offline sample must not issue HTTP requests")

    async def _run() -> dict[str, object]:
        work_root.mkdir(parents=True, exist_ok=False)
        extension_root = work_root / "extensions"
        extension_root.mkdir()
        if scenario == "empty":
            examples = ()
        else:
            examples = extract_installed_sdk_examples(extension_root)
            if tuple(item.package_id for item in examples) != (
                "empty_module",
                "offline_sample",
            ):
                raise AssertionError("examples did not come from the installed SDK")

        clock = _Clock()
        message_port = _MessagePort()
        trusted_sessions: dict[str, int] = {}
        trusted_deployment = False
        manifests = MappingProxyType({})
        gates = MappingProxyType({})
        if scenario in {"subscriptions", "reopen"}:
            resource = (
                importlib.resources.files("yomihime_sdk._examples.offline_sample")
                .joinpath("manifest.json")
                .read_bytes()
            )
            if (
                extension_root / "offline_sample" / "yomihime.manifest.json"
            ).read_bytes() != resource:
                raise AssertionError("installed sample manifest bytes changed")
            packages = discover_packages(extension_root)
            matching = tuple(p for p in packages if p.package_id == "offline_sample")
            if len(packages) != 2 or len(matching) != 1:
                raise AssertionError("installed sample discovery is not unique")
            package = matching[0]
            provenance = package._provenance
            if (
                not all(p.valid for p in packages)
                or package.manifest is None
                or type(provenance) is not PackageProvenance
                or not provenance.trusted
                or provenance.package_id != "offline_sample"
            ):
                raise AssertionError("installed sample provenance is not sealed")
            import hashlib

            if provenance.manifest_sha256 != hashlib.sha256(resource).digest():
                raise AssertionError("installed sample discovery bytes changed")
            modules = tuple(
                m for m in package.manifest.modules if m.module_id == "status"
            )
            if len(modules) != 1:
                raise AssertionError("installed sample status module is not unique")
            fields = tuple(
                f
                for f in modules[0].config_fields
                if f.name == "sample_subscriptions_enabled"
            )
            if len(fields) != 1 or fields[0].sensitive or fields[0].default is not True:
                raise AssertionError("installed sample gate declaration is invalid")
            manifests = MappingProxyType({"offline_sample/status": modules[0]})
            gates = MappingProxyType(
                {
                    "offline_sample/status": TrustedSubscriptionGate(
                        modules[0], fields[0].name, provenance.manifest_sha256
                    )
                }
            )

        def admin_validator(operation, invocation, context, generation):
            del invocation
            return (
                isinstance(context, _AdminContext)
                and context.adapter_id == "offline-admin"
                and context.request_id.startswith("trusted-request-")
                and trusted_sessions.get(context.session_id) == generation
                and (
                    isinstance(operation, AdminOperation)
                    or operation is AdminCredentialOperation.ROTATE
                )
            )

        def ingress_validator(origin, ingress):
            return (
                origin
                in (
                    core_contexts.InvocationOrigin.COMMAND,
                    core_contexts.InvocationOrigin.LLM_TOOL,
                )
                and ingress.evidence == f"trusted-host:{ingress.actor_id}"
                and ingress.adapter_id == "offline-host"
            )

        def create_runtime() -> CoreRuntime:
            return CoreRuntime(
                database=SQLiteDatabase(work_root / "runtime.sqlite3"),
                extension_root=extension_root,
                file_root=work_root / "files",
                secret_root=work_root / "secrets",
                secret_codec=_Codec(),
                http_transport=_HttpTransport(),
                renderer=GenericDisplayRenderer(
                    RenderingBounds(2000, 100, 100, 100, 1024 * 1024, 2048, 8, 64, 100)
                ),
                display_limits=DisplayLimits(4, 1024 * 1024),
                message_port=message_port,
                admin_context_validator=admin_validator,
                host_ingress_validator=ingress_validator,
                config_principal_id="host-config",
                identity_namespace="host-bridge",
                utc_clock=clock,
                pump_interval=3600,
                cleanup_timeout=1.0,
                trusted_bundled_manifests=manifests if trusted_deployment else None,
                trusted_subscription_gates=gates if trusted_deployment else None,
            )

        runtime = create_runtime()
        try:
            await runtime.start()
            candidates = {
                item.package.package_id: item
                for item in runtime.extension_runtime.candidates()
            }
            if scenario == "empty":
                if (
                    not runtime.started
                    or not runtime.accepting
                    or candidates
                    or runtime.registry.snapshot().modules
                ):
                    raise AssertionError(
                        "empty-root CoreRuntime did not start with zero candidates and modules"
                    )
                await asyncio.sleep(0)
                await runtime.close(timeout=1.0)
                await asyncio.sleep(0)
                leaked_threads = [
                    thread
                    for thread in threading.enumerate()
                    if thread.name == "yomihime-sqlite-worker" and thread.is_alive()
                ]
                leaked_tasks = [
                    task.get_name()
                    for task in asyncio.all_tasks()
                    if task is not asyncio.current_task()
                    and task.get_name().startswith("yomihime-core-")
                    and not task.done()
                ]
                if leaked_threads or leaked_tasks or not runtime.closed:
                    raise AssertionError(
                        "zero-module CoreRuntime leaked an owned thread or task"
                    )
                return {
                    "started": True,
                    "closed": runtime.closed,
                    "module_count": 0,
                    "owned_threads_after_close": len(leaked_threads),
                    "owned_tasks_after_close": len(leaked_tasks),
                }
            if set(candidates) != {"empty_module", "offline_sample"}:
                raise AssertionError(
                    "CoreRuntime E discovery omitted an installed package"
                )
            if any(item.state.value != "disabled" for item in candidates.values()):
                raise AssertionError(
                    "installed packages were not inert before authorization"
                )
            if runtime.registry.snapshot().modules:
                raise AssertionError(
                    "static discovery registered modules before activation"
                )
            if any(
                getattr(module, "__file__", "")
                and str(extension_root) in str(getattr(module, "__file__", ""))
                for module in tuple(sys.modules.values())
            ):
                raise AssertionError(
                    "E imported installed sample source during discovery"
                )

            untrusted = _AdminContext(
                "offline-admin", "trusted-request-rejected", "missing"
            )
            try:
                await runtime.admin_operations.set_enabled(
                    None,
                    "offline_sample/status",
                    True,
                    expected_registry_revision=runtime.registry.snapshot().revision,
                    authorization=untrusted,
                )
            except AdminAuthorizationDenied:
                pass
            else:
                raise AssertionError(
                    "module activation succeeded before trusted admin proof"
                )
            if runtime.registry.snapshot().modules:
                raise AssertionError("unauthorized activation changed the Registry")

            credential = ""
            while len(credential) < 40:
                import secrets

                credential = secrets.token_urlsafe(32)
            await runtime.admin_credential_repository.bootstrap(_digest(credential))
            admin = _AdminContext(
                "offline-admin", "trusted-request-1", "trusted-session-1"
            )
            trusted_sessions[admin.session_id] = 1

            if scenario in {"subscriptions", "reopen"}:
                # Preserve the original inert/unauthorized checks above, then
                # persist disabled intent through the real admin coordinator.
                # False with no persisted intent is a production no-op. A real
                # enable/disable transition establishes intent without SQL seeds.
                for enabled in (True, False):
                    await runtime.admin_operations.set_enabled(
                        None,
                        "offline_sample/status",
                        enabled,
                        expected_registry_revision=runtime.registry.snapshot().revision,
                        authorization=admin,
                    )
                await runtime.close(timeout=1.0)
                trusted_deployment = True
                runtime = create_runtime()
                await runtime.start()
                gate_state = await runtime.subscription_gate_state(
                    "offline_sample/status"
                )
                if (
                    not gate_state.supported
                    or gate_state.enabled is not True
                    or gate_state.can_run is not False
                    or gate_state.reason != "module_disabled"
                ):
                    raise AssertionError(
                        f"trusted gate changed persisted disabled intent: {gate_state!r}"
                    )
                before = runtime.registry.snapshot()
                try:
                    await runtime.admin_operations.set_enabled(
                        None,
                        "offline_sample/status",
                        True,
                        expected_registry_revision=before.revision,
                        authorization=untrusted,
                    )
                except AdminAuthorizationDenied:
                    pass
                else:
                    raise AssertionError("trusted deployment bypassed admin proof")
                if runtime.registry.snapshot() is not before:
                    raise AssertionError(
                        "unauthorized trusted activation changed Registry"
                    )

            principals = (
                Principal("principal-alice", "host-bridge", "external-alice"),
                Principal("principal-bob", "host-bridge", "external-bob"),
            )
            for principal in principals:
                await runtime.repositories.identities.save_principal(principal)
            for principal_id, subject in (
                ("principal-alice", "account-alice"),
                ("principal-bob", "account-bob"),
            ):
                await runtime.repositories.identities.save_identity(
                    principal_id,
                    core_services.ResolvedIdentity(
                        f"fixture:{subject}", "fixture", subject, principal_id
                    ),
                )

            def ingress(user: str, *, grant=None) -> HostIngress:
                return HostIngress(
                    "offline-host",
                    f"external-{user}",
                    f"dm-{user}",
                    f"route-{user}",
                    core_services.ConversationKind.DIRECT,
                    f"trusted-host:external-{user}",
                    grant,
                )

            async def enable(module_id: str) -> object:
                return await runtime.admin_operations.set_enabled(
                    None,
                    module_id,
                    True,
                    expected_registry_revision=runtime.registry.snapshot().revision,
                    authorization=admin,
                )

            await enable("offline_sample/source")
            await enable("offline_sample/status")
            admitted_gate = await runtime.subscription_gate_state(
                "offline_sample/status"
            )
            if trusted_deployment and (
                not admitted_gate.supported
                or admitted_gate.enabled is not True
                or admitted_gate.can_run is not True
                or admitted_gate.reason is not None
            ):
                raise AssertionError("trusted sample subscription gate is not admitted")

            status = await runtime.invoke_command(
                "offline_sample/status", "status", {}, ingress=ingress("alice")
            )
            if status.result is None or status.output.status.value != "sent":
                raise AssertionError("installed sample status command did not send")
            command_send_count = len(message_port.calls)

            tool = await runtime.invoke_tool(
                "offline_sample/status", "sample_status", {}, ingress=ingress("alice")
            )
            if (
                tool.output.status.value != "tool_result"
                or len(message_port.calls) != command_send_count
                or tool.result is None
                or tool.result.model_facts is None
            ):
                raise AssertionError("Tool output did not remain facts-only")

            before_dependency = runtime.health_resolver.current(
                "offline_sample/status", "from_source"
            )[0]
            independent_before = runtime.health_resolver.current(
                "offline_sample/status", "status"
            )[0]
            await runtime.admin_operations.set_enabled(
                None,
                "offline_sample/source",
                False,
                expected_registry_revision=runtime.registry.snapshot().revision,
                authorization=admin,
            )
            module_after_disable = runtime.registry.snapshot().module(
                "offline_sample/status"
            )
            if not module_after_disable.enabled:
                raise AssertionError(
                    "disabling dependency disabled its dependent module"
                )
            dependency_after = runtime.health_resolver.current(
                "offline_sample/status", "from_source"
            )[0]
            independent_after = runtime.health_resolver.current(
                "offline_sample/status", "status"
            )[0]
            still_independent = await runtime.invoke_command(
                "offline_sample/status", "status", {}, ingress=ingress("alice")
            )
            if (
                dependency_after.status is not HealthStatus.UNAVAILABLE
                or independent_after.status is not HealthStatus.AVAILABLE
                or still_independent.output.status.value != "sent"
            ):
                raise AssertionError(
                    "dependency loss leaked into independent capability"
                )
            try:
                await runtime.invoke_command(
                    "offline_sample/status", "from source", {}, ingress=ingress("alice")
                )
            except PermissionError:
                dependency_denied = True
            else:
                dependency_denied = False
            await enable("offline_sample/source")
            dependency_restored = await runtime.invoke_command(
                "offline_sample/status", "from source", {}, ingress=ingress("alice")
            )
            if dependency_restored.result is None:
                raise AssertionError("re-enabled dependency did not restore invocation")

            initial_health = runtime.health_resolver.current(
                "offline_sample/status", "configured_status"
            )[0]
            if initial_health.status is not HealthStatus.UNAVAILABLE:
                raise AssertionError(
                    "required missing region did not degrade capability"
                )
            try:
                await runtime.invoke_command(
                    "offline_sample/status", "configured", {}, ingress=ingress("alice")
                )
            except PermissionError:
                config_denied = True
            else:
                config_denied = False

            manifest = runtime.extension_runtime.candidate(
                "offline_sample"
            ).package.manifest.modules[0]
            config = await runtime.admin_operations.module_snapshot(
                None, "offline_sample/status", authorization=admin
            )
            patch = ConfigPatch(
                config.config.revision,
                (ConfigFieldUpdate("region", ConfigPatchMode.REPLACE, value="north"),),
                manifest.config_fields,
            )
            await runtime.admin_operations.update_config(
                None, "offline_sample/status", patch, authorization=admin
            )
            restored_health = runtime.health_resolver.current(
                "offline_sample/status", "configured_status"
            )[0]
            configured = await runtime.invoke_command(
                "offline_sample/status", "configured", {}, ingress=ingress("alice")
            )
            help_catalog = HelpCatalog(
                runtime.health_resolver.current, active_query=runtime.registry.is_active
            )
            help_doc = help_catalog.module(runtime.registry.snapshot(), "sample")
            help_lines = []
            for block in help_doc.ordered_blocks:
                block_text = getattr(block, "text", None)
                if isinstance(block_text, str):
                    help_lines.append(block_text)
                help_lines.extend(getattr(block, "commands", ()))
            help_text = "\n".join(help_lines)
            admin_snapshot = await runtime.admin_operations.module_snapshot(
                None, "offline_sample/status", authorization=admin
            )
            configured_admin = next(
                item
                for item in admin_snapshot.capabilities
                if item.capability_id == "configured_status"
            )
            if (
                restored_health.status is not HealthStatus.AVAILABLE
                or configured.result is None
                or not configured_admin.available
                or "configured" not in help_text
            ):
                raise AssertionError(
                    "config recovery was not reflected consistently: "
                    f"health={restored_health.status.value}, "
                    f"command={configured.result!r}, "
                    f"admin={configured_admin.available}/{configured_admin.reason_code}, "
                    f"help={help_text!r}"
                )

            activation_result = {
                "subscription_gate_admitted": admitted_gate.can_run is True,
                "installed_sdk_origin": str(Path(sdk.__file__).resolve()),
                "package_ids": sorted(candidates),
                "status_command_sent": status.output.status.value,
                "tool_status": tool.output.status.value,
                "tool_facts": dict(tool.result.model_facts.facts),
                "command_send_count": command_send_count,
                "independent_before": independent_before.status.value,
                "dependent_before": before_dependency.status.value,
                "independent_after": independent_after.status.value,
                "dependent_after": dependency_after.status.value,
                "dependency_denied": dependency_denied,
                "dependency_restored": dependency_restored.result is not None,
                "required_config_denied": config_denied,
                "required_config_before": initial_health.status.value,
                "required_config_after": restored_health.status.value,
                "configured_admin_available": configured_admin.available,
                "configured_admin_reason": configured_admin.reason_code,
                "help_includes_configured": "/ygl sample configured" in help_text,
                "send_calls": len(message_port.calls),
            }

            if scenario == "activation":
                return activation_result

            def document_text(outcome) -> str:
                result = outcome.result
                document = getattr(result, "document", None)
                if document is None:
                    return ""
                return "\n".join(
                    block.text
                    for block in document.ordered_blocks
                    if isinstance(getattr(block, "text", None), str)
                )

            async def command(user: str, operation: str, parameters, *, grant=None):
                return await runtime.invoke_command(
                    "offline_sample/status",
                    operation,
                    parameters,
                    ingress=ingress(user, grant=grant),
                )

            def assert_public_management_receipt(
                outcome, category: str, *, private_values: tuple[str, ...] = ()
            ) -> None:
                result = outcome.result
                if (
                    result is None
                    or outcome.output.status.value != "sent"
                    or result.status.value != "success"
                    or result.privacy.value != "public"
                    or result.document is None
                    or result.document.privacy.value != "public"
                    or result.model_facts is not None
                    or result.provenance
                    or result.warnings
                    or result.timestamps
                ):
                    raise AssertionError(
                        "management command did not return and send a PUBLIC receipt"
                    )
                is_account = category == "account"
                expected = (
                    "offline-account-management"
                    if is_account
                    else "offline-subscription-management"
                )
                title = (
                    "Sample account management"
                    if is_account
                    else "Sample subscription management"
                )
                subject = (
                    "The sample account management request was processed."
                    if is_account
                    else "The sample subscription management request was processed."
                )
                if (
                    result.result_id != expected
                    or result.document.title != title
                    or result.document.subject != subject
                    or len(result.document.ordered_blocks) != 1
                    or result.document.ordered_blocks[0].text
                    != "This offline sample returns a fixed public receipt."
                    or result.document.sources
                    or result.document.timestamps
                ):
                    raise AssertionError("management receipt was not fixed and static")
                rendered = repr(result)
                for private_value in private_values:
                    if private_value and private_value in rendered:
                        raise AssertionError("management receipt exposed private data")

            def assert_gateway_failure(
                outcome,
                expected_code: str,
                *,
                private_values: tuple[str, ...] = (),
            ) -> None:
                result = outcome.result
                if (
                    result is None
                    or result.result_id != "gateway-error"
                    or result.status.value != "error"
                    or result.error is None
                    or result.error.code.value != expected_code
                    or result.document is not None
                    or result.model_facts is not None
                    or result.provenance
                    or result.warnings
                    or result.timestamps
                    or result.privacy.value != "public"
                ):
                    raise AssertionError(
                        "Gateway failure was missing or became a success receipt"
                    )
                rendered = repr(result)
                for private_value in private_values:
                    if private_value and private_value in rendered:
                        raise AssertionError("Gateway failure exposed private data")
                if outcome.output.status.value == "sent":
                    expected_text = {
                        "module_unavailable": "module temporarily unavailable",
                        "unknown": "request failed",
                    }.get(expected_code)
                    if (
                        expected_text is None
                        or not message_port.calls
                        or message_port.calls[-1][1].text != expected_text
                    ):
                        raise AssertionError(
                            "Gateway error output was not the stable generic denial"
                        )

            if scenario == "reopen":
                module_id = "offline_sample/status"
                private_values = ("principal-alice", "account-alice", "reopen-region")
                binding_receipt = await command(
                    "alice",
                    "account bind",
                    {"provider": "fixture", "subject": "account-alice"},
                )
                assert_public_management_receipt(
                    binding_receipt, "account", private_values=private_values
                )
                bindings_before = await runtime.repositories.bindings.list_for(
                    "principal-alice",
                    module_id,
                    ConversationKey("offline-host", "dm-alice"),
                )
                if len(bindings_before) != 1:
                    raise AssertionError(
                        "reopen scenario did not persist its account binding"
                    )
                subscription_receipt = await command(
                    "alice",
                    "subscription create",
                    {
                        "type_id": "public_watch",
                        "region": "reopen-region",
                        "minimum": 1,
                        "mode": "instant",
                    },
                )
                assert_public_management_receipt(
                    subscription_receipt,
                    "subscription",
                    private_values=private_values,
                )
                subscriptions_before = (
                    await runtime.b04_repositories.subscriptions.list_for_owner(
                        "principal-alice", limit=20
                    )
                )
                subscriptions_before = tuple(
                    item
                    for item in subscriptions_before
                    if item.module_id == module_id
                    and item.type_id == "public_watch"
                    and item.collection_key.parameters.values.get("region")
                    == "reopen-region"
                )
                if len(subscriptions_before) != 1:
                    raise AssertionError(
                        "reopen scenario did not persist its subscription"
                    )

                current_module = runtime.registry.snapshot().module(module_id)
                stale_view = runtime.issuer.issue(
                    origin=core_contexts.InvocationOrigin.COMMAND,
                    module_id=module_id,
                    module_epoch=current_module.epoch,
                    registry_revision=runtime.registry.snapshot().revision,
                    actor_id="external-alice",
                    adapter_id="offline-host",
                    conversation_id="dm-alice",
                    capability_id="status",
                )
                replacement_credential = secrets.token_urlsafe(32)
                rotated = await runtime.admin_authorization.rotate(
                    credential,
                    replacement_credential,
                    invocation=None,
                    context=admin,
                )
                if rotated.generation != 2:
                    raise AssertionError(
                        "admin credential rotation did not advance generation"
                    )
                rotated_admin = _AdminContext(
                    "offline-admin", "trusted-request-2", "trusted-session-2"
                )
                trusted_sessions[rotated_admin.session_id] = rotated.generation
                try:
                    await runtime.admin_operations.set_enabled(
                        None,
                        module_id,
                        False,
                        expected_registry_revision=runtime.registry.snapshot().revision,
                        authorization=admin,
                    )
                except AdminAuthorizationDenied:
                    stale_admin_denied = True
                else:
                    stale_admin_denied = False
                if not stale_admin_denied:
                    raise AssertionError("rotated admin credential remained accepted")

                before_disable = runtime.registry.snapshot().module(module_id)
                await runtime.admin_operations.set_enabled(
                    None,
                    module_id,
                    False,
                    expected_registry_revision=runtime.registry.snapshot().revision,
                    authorization=rotated_admin,
                )
                disabled = runtime.registry.snapshot().module(module_id)
                await runtime.admin_operations.set_enabled(
                    None,
                    module_id,
                    True,
                    expected_registry_revision=runtime.registry.snapshot().revision,
                    authorization=rotated_admin,
                )
                reenabled = runtime.registry.snapshot().module(module_id)
                stale_replay = await runtime.gateway.invoke_command(
                    stale_view, "status", {}
                )
                stale_error_code = (
                    None
                    if stale_replay.error is None
                    else stale_replay.error.code.value
                )
                if (
                    disabled.enabled
                    or not reenabled.enabled
                    or reenabled.epoch <= before_disable.epoch
                    or stale_error_code != "module_unavailable"
                ):
                    raise AssertionError(
                        "disable/reenable did not advance epoch or reject the old invocation"
                    )

                old_runtime = runtime
                await old_runtime.close(timeout=1.0)
                old_runtime.issuer.release(stale_view)
                runtime = create_runtime()
                await runtime.start()
                recovered_admin_state = (
                    await runtime.admin_credential_repository.current()
                )
                recovered_module = runtime.registry.snapshot().module(module_id)
                reopened_gate = await runtime.subscription_gate_state(module_id)
                if (
                    not reopened_gate.supported
                    or reopened_gate.enabled is not True
                    or reopened_gate.can_run is not True
                    or reopened_gate.reason is not None
                ):
                    raise AssertionError(
                        "Core reopen lost the trusted subscription gate"
                    )
                recovered_config = await runtime.admin_operations.module_snapshot(
                    None, module_id, authorization=rotated_admin
                )
                binding_after = await runtime.repositories.bindings.current(
                    bindings_before[0].binding_id
                )
                subscription_after = (
                    await runtime.b04_repositories.subscriptions.current(
                        subscriptions_before[0].subscription_id
                    )
                )
                foreign_replay = await runtime.gateway.invoke_command(
                    stale_view, "status", {}
                )
                foreign_error_code = (
                    None
                    if foreign_replay.error is None
                    else foreign_replay.error.code.value
                )
                reopened_status = await runtime.invoke_command(
                    module_id, "status", {}, ingress=ingress("alice")
                )
                if (
                    recovered_admin_state is None
                    or recovered_admin_state.generation != rotated.generation
                    or not recovered_module.enabled
                    or recovered_config.config.fields.get("region") != "configured"
                    or binding_after != bindings_before[0]
                    or subscription_after != subscriptions_before[0]
                    or foreign_error_code != "module_unavailable"
                    or reopened_status.output.status.value != "sent"
                ):
                    raise AssertionError(
                        "Core reopen did not recover runtime and business data"
                    )
                return {
                    **activation_result,
                    "stale_admin_denied": stale_admin_denied,
                    "subscription_gate_after_reopen": reopened_gate.can_run,
                    "epoch_advanced": reenabled.epoch > before_disable.epoch,
                    "old_epoch_rejected": stale_error_code,
                    "credential_generation_after_reopen": recovered_admin_state.generation,
                    "module_enabled_after_reopen": recovered_module.enabled,
                    "config_region_after_reopen": recovered_config.config.fields.get(
                        "region"
                    ),
                    "binding_persisted": binding_after == bindings_before[0],
                    "subscription_persisted": subscription_after
                    == subscriptions_before[0],
                    "old_issuer_rejected_after_reopen": foreign_error_code,
                    "status_after_reopen": reopened_status.output.status.value,
                }

            if scenario == "subscriptions":
                secret_value = b"fixture-only-grant-secret-alice"
                private_markers = (
                    "principal-alice",
                    "principal-bob",
                    "account-alice",
                    "account-bob",
                    secret_value.decode(),
                    "grant-alice",
                    "north",
                    "cancel-only",
                )

                private_denied = await command("alice", "private status", {})
                assert_gateway_failure(
                    private_denied,
                    "module_unavailable",
                    private_values=private_markers,
                )
                if private_denied.result.privacy.value != "public":
                    raise AssertionError(
                        "missing-Grant private command returned private handler data"
                    )

                receipt = await runtime.secret_store.put(
                    secret_value,
                    owner=SecretOwner(
                        "principal-alice",
                        "offline_sample/status",
                        "credential",
                        "grant-seed-alice",
                    ),
                )
                grant = Grant(
                    "grant-alice",
                    1,
                    "principal-alice",
                    "offline_sample/status",
                    "account-alice",
                    ("read",),
                    receipt.secret_ref,
                    GrantStatus.ACTIVE,
                    clock.current + timedelta(days=30),
                )
                await runtime.repositories.authorization.create_grant(
                    grant, expected_revision=0
                )
                grant_ref = GrantReference(grant.grant_id, grant.revision)

                private_status = await command(
                    "alice", "private status", {}, grant=grant_ref
                )
                if (
                    private_status.result is None
                    or private_status.output.status.value != "sent"
                    or private_status.result.privacy.value != "private"
                    or private_status.result.document is None
                    or private_status.result.document.privacy.value != "private"
                    or secret_value.decode() in document_text(private_status)
                    or not message_port.calls
                    or message_port.calls[-1][0].conversation_id != "dm-alice"
                    or message_port.calls[-1][0].conversation is None
                    or message_port.calls[-1][0].conversation.kind.value != "direct"
                ):
                    raise AssertionError(
                        "private status exposed or rejected fixture secret"
                    )

                digest_boundary = clock.current.replace(
                    second=0, microsecond=0
                ) + timedelta(minutes=5)
                digest_time = digest_boundary.strftime("%H:%M")
                private_markers = (*private_markers, digest_time)

                alice_bind = await command(
                    "alice",
                    "account bind",
                    {"provider": "fixture", "subject": "account-alice"},
                )
                assert_public_management_receipt(
                    alice_bind, "account", private_values=private_markers
                )
                bob_bind = await command(
                    "bob",
                    "account bind",
                    {"provider": "fixture", "subject": "account-bob"},
                )
                assert_public_management_receipt(
                    bob_bind, "account", private_values=private_markers
                )
                alice_bindings = await runtime.repositories.bindings.list_for(
                    "principal-alice",
                    "offline_sample/status",
                    ConversationKey("offline-host", "dm-alice"),
                )
                bob_bindings = await runtime.repositories.bindings.list_for(
                    "principal-bob",
                    "offline_sample/status",
                    ConversationKey("offline-host", "dm-bob"),
                )
                if len(alice_bindings) != 1 or len(bob_bindings) != 1:
                    raise AssertionError(
                        "Gateway account bind did not persist one row per principal"
                    )
                alice_binding = alice_bindings[0]
                bob_binding = bob_bindings[0]
                alice_list = await command("alice", "account list", {})
                bob_list = await command("bob", "account list", {})
                assert_public_management_receipt(
                    alice_list, "account", private_values=private_markers
                )
                assert_public_management_receipt(
                    bob_list, "account", private_values=private_markers
                )
                if (
                    alice_binding.principal_id != "principal-alice"
                    or bob_binding.principal_id != "principal-bob"
                    or alice_binding.binding_id == bob_binding.binding_id
                    or alice_binding.object_type != "fixture"
                    or bob_binding.object_type != "fixture"
                    or alice_binding.object_id != "fixture:account-alice"
                    or bob_binding.object_id != "fixture:account-bob"
                ):
                    raise AssertionError(
                        "persisted account bindings crossed principal boundaries"
                    )
                bob_cross_owner_unbind = await command(
                    "bob",
                    "account unbind",
                    {
                        "binding_id": alice_binding.binding_id,
                        "expected_revision": alice_binding.revision,
                    },
                )
                assert_gateway_failure(
                    bob_cross_owner_unbind,
                    "unknown",
                    private_values=private_markers,
                )
                alice_rows_after_cross_owner_unbind = (
                    await runtime.repositories.bindings.list_for(
                        "principal-alice",
                        "offline_sample/status",
                        ConversationKey("offline-host", "dm-alice"),
                    )
                )
                bob_rows_after_cross_owner_unbind = (
                    await runtime.repositories.bindings.list_for(
                        "principal-bob",
                        "offline_sample/status",
                        ConversationKey("offline-host", "dm-bob"),
                    )
                )
                if alice_rows_after_cross_owner_unbind != (
                    alice_binding,
                ) or bob_rows_after_cross_owner_unbind != (bob_binding,):
                    raise AssertionError(
                        "cross-owner account unbind changed a persisted row"
                    )

                alice_stale_unbind = await command(
                    "alice",
                    "account unbind",
                    {
                        "binding_id": alice_binding.binding_id,
                        "expected_revision": alice_binding.revision + 1,
                    },
                )
                assert_gateway_failure(
                    alice_stale_unbind,
                    "unknown",
                    private_values=private_markers,
                )
                alice_rows_after_stale_unbind = (
                    await runtime.repositories.bindings.list_for(
                        "principal-alice",
                        "offline_sample/status",
                        ConversationKey("offline-host", "dm-alice"),
                    )
                )
                if alice_rows_after_stale_unbind != (alice_binding,):
                    raise AssertionError(
                        "stale account revision changed the persisted binding"
                    )

                alice_unbind = await command(
                    "alice",
                    "account unbind",
                    {
                        "binding_id": alice_binding.binding_id,
                        "expected_revision": alice_binding.revision,
                    },
                )
                assert_public_management_receipt(
                    alice_unbind, "account", private_values=private_markers
                )
                alice_after_unbind = await command("alice", "account list", {})
                assert_public_management_receipt(
                    alice_after_unbind, "account", private_values=private_markers
                )
                alice_rows_after_unbind = await runtime.repositories.bindings.list_for(
                    "principal-alice",
                    "offline_sample/status",
                    ConversationKey("offline-host", "dm-alice"),
                )
                bob_rows_after_unbind = await runtime.repositories.bindings.list_for(
                    "principal-bob",
                    "offline_sample/status",
                    ConversationKey("offline-host", "dm-bob"),
                )
                if alice_rows_after_unbind or bob_rows_after_unbind != (bob_binding,):
                    raise AssertionError(
                        "Gateway account unbind removed the wrong persisted row"
                    )

                no_grant_subscriptions_before = tuple(
                    await runtime.b04_repositories.subscriptions.list_for_owner(
                        "principal-alice", limit=50
                    )
                )
                no_grant_jobs_before = (
                    await runtime.b04_repositories.scheduler.list_due_jobs(
                        now=clock.current, limit=20
                    )
                )
                no_grant_events_before = (
                    await runtime.b04_repositories.deliveries.list_due_events(
                        now=clock.current, limit=20, after_cursor=None
                    )
                )
                no_grant_private_create = await command(
                    "alice",
                    "subscription create",
                    {
                        "type_id": "private_watch",
                        "region": "north",
                        "minimum": 1,
                        "mode": "instant",
                    },
                )
                assert_gateway_failure(
                    no_grant_private_create,
                    "unknown",
                    private_values=private_markers,
                )
                no_grant_subscriptions_after = tuple(
                    await runtime.b04_repositories.subscriptions.list_for_owner(
                        "principal-alice", limit=50
                    )
                )
                no_grant_jobs_after = (
                    await runtime.b04_repositories.scheduler.list_due_jobs(
                        now=clock.current, limit=20
                    )
                )
                no_grant_events_after = (
                    await runtime.b04_repositories.deliveries.list_due_events(
                        now=clock.current, limit=20, after_cursor=None
                    )
                )
                if (
                    no_grant_subscriptions_after != no_grant_subscriptions_before
                    or no_grant_jobs_after != no_grant_jobs_before
                    or no_grant_events_after != no_grant_events_before
                ):
                    raise AssertionError(
                        "missing-Grant private subscription changed persisted work"
                    )

                async def create_subscription(
                    user: str, parameters: dict[str, object], *, grant=None
                ):
                    outcome = await command(
                        user, "subscription create", parameters, grant=grant
                    )
                    assert_public_management_receipt(
                        outcome, "subscription", private_values=private_markers
                    )
                    principal = (
                        "principal-alice" if user == "alice" else "principal-bob"
                    )
                    records = (
                        await runtime.b04_repositories.subscriptions.list_for_owner(
                            principal, limit=50
                        )
                    )
                    region = parameters["region"]
                    matches = tuple(
                        record
                        for record in records
                        if record.module_id == "offline_sample/status"
                        and record.type_id == parameters["type_id"]
                        and record.notification_mode == parameters["mode"]
                        and record.collection_key.parameters.values.get("region")
                        == region
                        and record.grant == grant
                    )
                    if len(matches) != 1 or matches[0].revision != 1:
                        raise AssertionError(
                            "Gateway subscription create did not persist its row"
                        )
                    return matches[0]

                alice_public_record = await create_subscription(
                    "alice",
                    {
                        "type_id": "public_watch",
                        "region": "north",
                        "minimum": 1,
                        "mode": "instant",
                    },
                )
                bob_digest_record = await create_subscription(
                    "bob",
                    {
                        "type_id": "public_watch",
                        "region": "north",
                        "minimum": 1,
                        "mode": "digest",
                        "digest_time": digest_time,
                    },
                )
                alice_cross_owner_cancel = await command(
                    "alice",
                    "subscription cancel",
                    {
                        "subscription_id": bob_digest_record.subscription_id,
                        "expected_revision": bob_digest_record.revision,
                    },
                )
                assert_gateway_failure(
                    alice_cross_owner_cancel,
                    "unknown",
                    private_values=private_markers,
                )
                bob_digest_after_cross_owner_cancel = (
                    await runtime.b04_repositories.subscriptions.current(
                        bob_digest_record.subscription_id
                    )
                )
                if bob_digest_after_cross_owner_cancel != bob_digest_record:
                    raise AssertionError(
                        "cross-owner subscription cancel changed Bob's row"
                    )
                alice_public_after_cross_owner_cancel = (
                    await runtime.b04_repositories.subscriptions.current(
                        alice_public_record.subscription_id
                    )
                )
                if alice_public_after_cross_owner_cancel != alice_public_record:
                    raise AssertionError(
                        "cross-owner subscription cancel changed Alice's row"
                    )

                bob_stale_cancel = await command(
                    "bob",
                    "subscription cancel",
                    {
                        "subscription_id": bob_digest_record.subscription_id,
                        "expected_revision": bob_digest_record.revision + 1,
                    },
                )
                assert_gateway_failure(
                    bob_stale_cancel,
                    "unknown",
                    private_values=private_markers,
                )
                bob_digest_after_stale_cancel = (
                    await runtime.b04_repositories.subscriptions.current(
                        bob_digest_record.subscription_id
                    )
                )
                if bob_digest_after_stale_cancel != bob_digest_record:
                    raise AssertionError(
                        "stale subscription revision changed Bob's row"
                    )
                alice_public_after_stale_cancel = (
                    await runtime.b04_repositories.subscriptions.current(
                        alice_public_record.subscription_id
                    )
                )
                if alice_public_after_stale_cancel != alice_public_record:
                    raise AssertionError(
                        "stale subscription revision changed Alice's row"
                    )

                alice_private_record = await create_subscription(
                    "alice",
                    {
                        "type_id": "private_watch",
                        "region": "north",
                        "minimum": 1,
                        "mode": "instant",
                    },
                    grant=grant_ref,
                )
                bob_cancel_record = await create_subscription(
                    "bob",
                    {
                        "type_id": "public_watch",
                        "region": "cancel-only",
                        "minimum": 1,
                        "mode": "instant",
                    },
                )
                bob_before_cancel = await command("bob", "subscription list", {})
                assert_public_management_receipt(
                    bob_before_cancel, "subscription", private_values=private_markers
                )
                bob_cancel = await command(
                    "bob",
                    "subscription cancel",
                    {
                        "subscription_id": bob_cancel_record.subscription_id,
                        "expected_revision": bob_cancel_record.revision,
                    },
                )
                assert_public_management_receipt(
                    bob_cancel, "subscription", private_values=private_markers
                )
                bob_after_cancel = await command("bob", "subscription list", {})
                assert_public_management_receipt(
                    bob_after_cancel, "subscription", private_values=private_markers
                )
                bob_cancelled = await runtime.b04_repositories.subscriptions.current(
                    bob_cancel_record.subscription_id
                )
                bob_digest_current = (
                    await runtime.b04_repositories.subscriptions.current(
                        bob_digest_record.subscription_id
                    )
                )
                if (
                    bob_cancelled is None
                    or bob_cancelled.status.value != "cancelled"
                    or bob_cancelled.revision != bob_cancel_record.revision + 1
                    or bob_digest_current != bob_digest_record
                ):
                    raise AssertionError(
                        "Gateway subscription cancel changed an unrelated row"
                    )
                alice_subscriptions = await command(
                    "alice", "subscription list", {}, grant=grant_ref
                )
                assert_public_management_receipt(
                    alice_subscriptions, "subscription", private_values=private_markers
                )

                alice_public_id = alice_public_record.subscription_id
                bob_digest_id = bob_digest_record.subscription_id
                alice_private_id = alice_private_record.subscription_id
                bob_cancel_id = bob_cancel_record.subscription_id

                tool_send_count = len(message_port.calls)
                tool_bindings_before = (
                    await runtime.repositories.bindings.list_for(
                        "principal-alice",
                        "offline_sample/status",
                        ConversationKey("offline-host", "dm-alice"),
                    ),
                    await runtime.repositories.bindings.list_for(
                        "principal-bob",
                        "offline_sample/status",
                        ConversationKey("offline-host", "dm-bob"),
                    ),
                )
                tool_subscriptions_before = (
                    tuple(
                        await runtime.b04_repositories.subscriptions.list_for_owner(
                            "principal-alice", limit=50
                        )
                    ),
                    tuple(
                        await runtime.b04_repositories.subscriptions.list_for_owner(
                            "principal-bob", limit=50
                        )
                    ),
                )
                tool_denials = {}
                for tool_name in ("account_list", "subscription_list"):
                    try:
                        await runtime.invoke_tool(
                            "offline_sample/status",
                            tool_name,
                            {},
                            ingress=ingress("alice"),
                        )
                    except PermissionError:
                        tool_denials[tool_name] = True
                    else:
                        tool_denials[tool_name] = False
                tool_bindings_after = (
                    await runtime.repositories.bindings.list_for(
                        "principal-alice",
                        "offline_sample/status",
                        ConversationKey("offline-host", "dm-alice"),
                    ),
                    await runtime.repositories.bindings.list_for(
                        "principal-bob",
                        "offline_sample/status",
                        ConversationKey("offline-host", "dm-bob"),
                    ),
                )
                tool_subscriptions_after = (
                    tuple(
                        await runtime.b04_repositories.subscriptions.list_for_owner(
                            "principal-alice", limit=50
                        )
                    ),
                    tuple(
                        await runtime.b04_repositories.subscriptions.list_for_owner(
                            "principal-bob", limit=50
                        )
                    ),
                )
                if (
                    not all(tool_denials.values())
                    or len(message_port.calls) != tool_send_count
                    or tool_bindings_after != tool_bindings_before
                    or tool_subscriptions_after != tool_subscriptions_before
                ):
                    raise AssertionError(
                        "a denied management Tool call sent or changed persisted data"
                    )

                local_date = clock.current.date()
                await runtime.scheduler.create_digest_windows_page(
                    local_dates=tuple(sorted({local_date, digest_boundary.date()}))
                )
                due_jobs = await runtime.b04_repositories.scheduler.list_due_jobs(
                    now=clock.current, limit=20
                )
                if len(due_jobs) != 2:
                    raise AssertionError(
                        f"expected one shared and one private first-arm job, got {len(due_jobs)}"
                    )
                page = await runtime.scheduler.run_due_page()
                if page.processed != 2:
                    raise AssertionError(
                        "Core scheduler did not process both real collection jobs"
                    )

                alice_public_record = (
                    await runtime.b04_repositories.subscriptions.current(
                        alice_public_id
                    )
                )
                bob_digest_record = (
                    await runtime.b04_repositories.subscriptions.current(bob_digest_id)
                )
                private_record = await runtime.b04_repositories.subscriptions.current(
                    alice_private_id
                )
                if (
                    alice_public_record.owner_id != "principal-alice"
                    or bob_digest_record.owner_id != "principal-bob"
                    or private_record.owner_id != "principal-alice"
                    or alice_public_record.collection_key
                    != bob_digest_record.collection_key
                ):
                    raise AssertionError(
                        "real external actors were not isolated to their persisted principals"
                    )
                alice_evaluation = (
                    await runtime.b04_repositories.scheduler.current_evaluation(
                        alice_public_id, alice_public_record.collection_key
                    )
                )
                bob_evaluation = (
                    await runtime.b04_repositories.scheduler.current_evaluation(
                        bob_digest_id, bob_digest_record.collection_key
                    )
                )
                private_evaluation = (
                    await runtime.b04_repositories.scheduler.current_evaluation(
                        alice_private_id, private_record.collection_key
                    )
                )
                if (
                    alice_evaluation is None
                    or bob_evaluation is None
                    or private_evaluation is None
                    or alice_evaluation.cursor is None
                    or bob_evaluation.cursor is None
                    or private_evaluation.cursor is None
                    or alice_evaluation.cursor.observation_id
                    != bob_evaluation.cursor.observation_id
                    or alice_evaluation.cursor.observation_id
                    == private_evaluation.cursor.observation_id
                ):
                    raise AssertionError(
                        "public collection was not shared independently of private scope"
                    )

                async def capture_persisted_event(record, evaluation):
                    cursor = evaluation.cursor
                    if cursor is None:
                        raise AssertionError(
                            "scheduler evaluation did not persist an observation cursor"
                        )
                    # The installed sample's evaluator derives this event key
                    # from the persisted subscription and observation IDs.
                    event_key = (
                        f"sample-{record.subscription_id}-{cursor.observation_id}"
                    )
                    event = await runtime.b04_repositories.deliveries.current_event(
                        event_key,
                        1,
                        subscription_id=record.subscription_id,
                        subscription_revision=record.revision,
                    )
                    if event is None or event.state.value != "pending":
                        raise AssertionError(
                            "scheduler did not persist the exact pending delivery event"
                        )
                    return event

                alice_public_event = await capture_persisted_event(
                    alice_public_record, alice_evaluation
                )
                bob_digest_event = await capture_persisted_event(
                    bob_digest_record, bob_evaluation
                )
                private_event = await capture_persisted_event(
                    private_record, private_evaluation
                )

                module = runtime.registry.snapshot().module("offline_sample/status")
                revoke_view = runtime.issuer.issue(
                    origin=core_contexts.InvocationOrigin.COMMAND,
                    module_id="offline_sample/status",
                    module_epoch=module.epoch,
                    registry_revision=runtime.registry.snapshot().revision,
                    actor_id="external-alice",
                    adapter_id="offline-host",
                    conversation_id="dm-alice",
                    capability_id="account_unbind",
                )
                runtime.lifecycle.admission.admit(revoke_view, "account_unbind")
                try:
                    await runtime.module_services.for_module(
                        "offline_sample/status"
                    ).accounts.revoke(revoke_view, grant_ref)
                finally:
                    runtime.issuer.release(revoke_view)
                persisted_grant = (
                    await runtime.repositories.authorization.current_grant(
                        grant_ref.grant_id
                    )
                )
                if (
                    persisted_grant is None
                    or persisted_grant.status is not GrantStatus.REVOKED
                    or persisted_grant.revision != grant_ref.revision + 1
                    or persisted_grant.principal_id != grant.principal_id
                    or persisted_grant.module_id != grant.module_id
                    or persisted_grant.secret_ref != grant.secret_ref
                ):
                    raise AssertionError(
                        "Core authorization revoke did not persist the expected revision"
                    )

                before_dispatch = len(message_port.calls)
                immediate_queue = (
                    await runtime.b04_repositories.deliveries.list_due_events(
                        now=clock.current, limit=20, after_cursor=None
                    )
                )
                immediate_keys = {
                    (
                        event.event_key,
                        event.event_version,
                        event.subscription_id,
                        event.subscription_revision,
                    )
                    for event in immediate_queue
                }
                if immediate_keys != {
                    (
                        alice_public_event.event_key,
                        alice_public_event.event_version,
                        alice_public_event.subscription_id,
                        alice_public_event.subscription_revision,
                    )
                }:
                    raise AssertionError(
                        "immediate queue did not isolate the eligible public instant event"
                    )
                dispatched = await runtime.delivery.dispatch_due_events(
                    now=clock.current
                )
                after_events = message_port.calls[before_dispatch:]
                current_public_event = (
                    await runtime.b04_repositories.deliveries.current_event(
                        alice_public_event.event_key,
                        alice_public_event.event_version,
                        subscription_id=alice_public_event.subscription_id,
                        subscription_revision=alice_public_event.subscription_revision,
                    )
                )
                current_digest_event = (
                    await runtime.b04_repositories.deliveries.current_event(
                        bob_digest_event.event_key,
                        bob_digest_event.event_version,
                        subscription_id=bob_digest_event.subscription_id,
                        subscription_revision=bob_digest_event.subscription_revision,
                    )
                )
                private_after_revoke = (
                    await runtime.b04_repositories.deliveries.current_event(
                        private_event.event_key,
                        private_event.event_version,
                        subscription_id=private_event.subscription_id,
                        subscription_revision=private_event.subscription_revision,
                    )
                )
                if (
                    current_public_event is None
                    or current_public_event.state.value != "sent"
                    or current_digest_event is None
                    or current_digest_event.state.value != "pending"
                    or private_after_revoke is None
                    or private_after_revoke.state.value != "cancelled"
                ):
                    raise AssertionError(
                        "exact persisted public, digest, or revoked-private event state was wrong"
                    )
                early_digest_targets = tuple(
                    call[0].conversation_id
                    for call in after_events
                    if call[0].conversation_id == "dm-bob"
                )
                instant_send_targets = tuple(
                    call[0].conversation_id for call in after_events
                )
                if (
                    dispatched != 1
                    or instant_send_targets != ("dm-alice",)
                    or early_digest_targets
                ):
                    raise AssertionError(
                        "pre-digest immediate drain did not send only Alice's public instant event"
                    )

                before_digest = len(message_port.calls)
                if await runtime.delivery.dispatch_due_digests(now=clock.current) != 0:
                    raise AssertionError(
                        "digest was sent before its UTC schedule boundary"
                    )
                digest_routes_before = (
                    await runtime.b04_repositories.windows.list_due_routes(
                        now=clock.current, limit=20, after_cursor=None
                    )
                )
                if digest_routes_before:
                    raise AssertionError(
                        "digest route query returned a future window early"
                    )
                clock.advance(digest_boundary - clock.current)
                digest_routes_due = (
                    await runtime.b04_repositories.windows.list_due_routes(
                        now=clock.current, limit=20, after_cursor=None
                    )
                )
                if len(digest_routes_due) != 1:
                    raise AssertionError(
                        "digest route query did not return its due window"
                    )
                digest_sent = await runtime.delivery.dispatch_due_digests(
                    now=clock.current
                )
                digest_calls = message_port.calls[before_digest:]
                if (
                    digest_sent != 1
                    or len(digest_calls) != 1
                    or digest_calls[0][0].conversation_id != "dm-bob"
                ):
                    raise AssertionError(
                        "the due public digest did not reach Bob exactly once"
                    )

                sensitive_output_markers = (
                    "principal-alice",
                    "principal-bob",
                    "account-alice",
                    "account-bob",
                    secret_value.decode(),
                    grant_ref.grant_id,
                    alice_binding.binding_id,
                    bob_binding.binding_id,
                    alice_public_id,
                    bob_digest_id,
                    alice_private_id,
                    bob_cancel_id,
                )
                secret_leaked = any(
                    marker and marker in payload.text
                    for marker in sensitive_output_markers
                    for _target, payload in message_port.calls
                )

                return {
                    **activation_result,
                    "alice_principal": alice_public_record.owner_id,
                    "bob_principal": bob_digest_record.owner_id,
                    "account_binding_isolation": True,
                    "account_unbind_empty": not alice_rows_after_unbind,
                    "cross_owner_account_unbind_denied": True,
                    "stale_account_revision_denied": True,
                    "cross_owner_subscription_cancel_denied": True,
                    "stale_subscription_revision_denied": True,
                    "no_grant_private_command_denied": True,
                    "no_grant_private_subscription_denied": True,
                    "subscription_list_calls_and_owner_rows_checked": True,
                    "subscription_cancelled": bob_cancelled.status.value == "cancelled",
                    "account_tool_denied": tool_denials["account_list"],
                    "subscription_tool_denied": tool_denials["subscription_list"],
                    "shared_public_observation": alice_evaluation.cursor.observation_id,
                    "private_observation": private_evaluation.cursor.observation_id,
                    "revoke_private_event_state": private_after_revoke.state.value,
                    "grant_revoked": persisted_grant.status is GrantStatus.REVOKED,
                    "instant_dispatch_count": dispatched,
                    "instant_send_count": len(after_events),
                    "instant_send_targets": instant_send_targets,
                    "alice_public_event_state": current_public_event.state.value,
                    "bob_digest_event_state": current_digest_event.state.value,
                    "private_event_state": private_after_revoke.state.value,
                    "digest_sent_before_due": bool(early_digest_targets),
                    "early_digest_targets": early_digest_targets,
                    "digest_send_count": digest_sent,
                    "digest_target": digest_calls[0][0].conversation_id,
                    "total_send_calls": len(message_port.calls),
                    "secret_leaked": secret_leaked,
                }

            raise AssertionError("unexpected CoreHardening scenario")
        finally:
            if not runtime.closed:
                await runtime.close(timeout=1.0)

    result = asyncio.run(_run())
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    _isolated_probe_main()


__all__ = ["InstalledCoreHardeningWorkspace"]
