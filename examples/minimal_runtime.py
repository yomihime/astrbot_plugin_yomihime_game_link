"""Run the packaged offline sample through one real CoreRuntime.

From the AstrBot checkout parent, run::

    python -m astrbot_plugin_yomihime_game_link.examples.minimal_runtime

First build and install the local SDK wheel using the offline instructions in
``docs/module-sdk.md``, then make that isolated SDK site available on
``PYTHONPATH``. The source checkout intentionally does not contain the wheel's
``yomihime_game_link_sdk._examples`` resource packages.

This is an offline integration example. Its reversible secret codec is for
demonstration only, its HTTP transport rejects every network attempt, and its
admin and ingress validators stand in for trusted local host adapters. It does
not model a production host, management UI, or production key provider.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yomihime_game_link_sdk
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.display import (
    DisplayAudience,
    DisplayLimits,
    DisplayOutput,
    Privacy,
)
from yomihime_game_link_sdk.subscriptions import ConversationKind

from ..core.contracts.administration import AdminOperation
from ..core.contracts.validation_boundary import validate_contract
from ..core.ports import MessageReceipt, MessageStatus
from ..infrastructure.sqlite.database import SQLiteDatabase
from ..services.b05_runtime import extract_installed_sdk_examples
from ..services.core_runtime import CoreRuntime, HostIngress
from ..services.output import OutputStatus

_ADMIN_ADAPTER_ID = "local-demo-admin"
_HOST_ADAPTER_ID = "local-demo-host"
_ACTOR_ID = "offline-demo-actor"
_CONVERSATION_ID = "offline-demo-conversation"
_DELIVERY_ROUTE = "offline-console"


@dataclass(frozen=True, slots=True)
class _LocalAdminContext:
    adapter_id: str
    request_id: str
    session_id: str


class _LocalDemoHost:
    """Small trusted local adapter used only by this offline demonstration."""

    def __init__(self, credential: str) -> None:
        self._credential = credential
        self._admin_context: _LocalAdminContext | None = None
        self._admin_generation: int | None = None
        self._command_evidence = object()

    def authenticate_local_admin(
        self, candidate: str, *, generation: int
    ) -> _LocalAdminContext:
        if not hmac.compare_digest(candidate, self._credential):
            raise PermissionError("local demo admin authentication failed")
        context = _LocalAdminContext(
            _ADMIN_ADAPTER_ID,
            f"request-{secrets.token_hex(8)}",
            f"session-{secrets.token_hex(8)}",
        )
        self._admin_context = context
        self._admin_generation = generation
        return context

    def validate_admin_context(
        self, operation, invocation, context, generation: int
    ) -> bool:
        return (
            operation is AdminOperation.SET_ENABLED
            and invocation is None
            and context is self._admin_context
            and generation == self._admin_generation
        )

    def validate_host_ingress(self, origin, ingress: HostIngress) -> bool:
        return (
            origin is InvocationOrigin.COMMAND
            and isinstance(ingress, HostIngress)
            and ingress.evidence is self._command_evidence
            and ingress.adapter_id == _HOST_ADAPTER_ID
            and ingress.actor_id == _ACTOR_ID
            and ingress.conversation_id == _CONVERSATION_ID
            and ingress.delivery_route == _DELIVERY_ROUTE
            and ingress.conversation_kind is ConversationKind.DIRECT
        )

    def command_ingress(self) -> HostIngress:
        return HostIngress(
            adapter_id=_HOST_ADAPTER_ID,
            actor_id=_ACTOR_ID,
            conversation_id=_CONVERSATION_ID,
            delivery_route=_DELIVERY_ROUTE,
            conversation_kind=ConversationKind.DIRECT,
            evidence=self._command_evidence,
        )


class _OfflineHttpTransport:
    """Deny network access even if an example command tries to fetch data."""

    def __init__(self) -> None:
        self.attempts = 0

    async def request(self, request) -> object:
        del request
        self.attempts += 1
        raise RuntimeError("network access is disabled in this offline demo")


class _DemoOnlySecretCodec:
    """Reversible test codec; never use this in a deployment."""

    _PREFIX = b"offline-demo-only:"

    def encrypt(self, value: bytes) -> bytes:
        return self._PREFIX + value[::-1]

    def decrypt(self, value: bytes) -> bytes:
        if not value.startswith(self._PREFIX):
            raise ValueError("invalid offline demo envelope")
        return value[len(self._PREFIX) :][::-1]


class _PlainTextRenderer:
    """Render public SDK documents as bounded, readable console text."""

    async def render(self, document, *, limits, audience=DisplayAudience.PUBLIC):
        del limits
        if audience is DisplayAudience.PUBLIC and document.privacy is Privacy.PRIVATE:
            raise ValueError("the demo renderer refuses private documents")
        lines = [document.title, document.subject]
        lines.extend(
            text
            for block in document.ordered_blocks
            if isinstance(text := getattr(block, "text", None), str)
        )
        return validate_contract(DisplayOutput("\n".join(lines)))

    async def render_batch(self, batch, limits):
        rendered = [
            await self.render(member.document, limits=limits, audience=batch.audience)
            for member in batch.members
        ]
        return validate_contract(
            DisplayOutput("\n\n".join(item.text for item in rendered))
        )


class _RecordingMessagePort:
    """Record and print the message that Core routes to the local console."""

    def __init__(self) -> None:
        self.messages: list[tuple[object, object]] = []

    async def send(self, target, payload) -> MessageReceipt:
        self.messages.append((target, payload))
        print(f"message-port[{target.conversation_id}]:\n{payload.text}")
        return MessageReceipt(
            MessageStatus.ACCEPTED, f"offline-demo-{len(self.messages)}"
        )


def _aware_utc_now() -> datetime:
    """Supply an actual timezone-aware UTC instant to Core and its services."""

    now = datetime.now(UTC)
    if now.utcoffset() is None:
        raise RuntimeError("the demo UTC clock returned a naive datetime")
    return now


async def _close_runtime_and_cleanup(runtime: CoreRuntime, work_root: Path) -> None:
    """Remove the demo tree only after Core has released its owned resources."""

    close_result = False
    close_error: BaseException | None = None
    try:
        close_result = await runtime.close(timeout=2.0)
    except BaseException as error:
        close_error = error

    if runtime.closed:
        try:
            shutil.rmtree(work_root)
        except OSError:
            print(
                "CoreRuntime is closed, but its temporary tree could not be "
                f"removed: {work_root}"
            )
            raise
        print("temporary runtime tree removed: True")
    else:
        print(
            "CoreRuntime cleanup is pending; temporary runtime tree retained at "
            f"{work_root}. The runtime can be retried only while its owner is "
            "still available; this standalone demo does not persist a retry "
            "handle after _run returns. Keep this tree intact until the runtime "
            "is confirmed stopped, then remove it manually."
        )

    if close_error is not None:
        raise close_error
    if not close_result or not runtime.closed:
        if runtime.closed:
            raise RuntimeError(
                "CoreRuntime reported an incomplete close despite "
                f"runtime.closed=True; its temporary tree was removed: {work_root}"
            )
        raise RuntimeError(
            "CoreRuntime did not close cleanly; its temporary tree was retained: "
            f"{work_root}"
        )


async def _run() -> None:
    work_root = Path(
        tempfile.mkdtemp(
            prefix="yomihime-core-demo-", dir=Path(__file__).resolve().parents[1]
        )
    )
    runtime: CoreRuntime | None = None
    runtime_construction_started = False
    try:
        extension_root = work_root / "extensions"
        file_root = work_root / "files"
        secret_root = work_root / "secrets"
        for path in (extension_root, file_root, secret_root):
            path.mkdir()

        examples = extract_installed_sdk_examples(extension_root)
        sample = next(item for item in examples if item.package_id == "offline_sample")
        print(f"SDK origin: {Path(yomihime_game_link_sdk.__file__).resolve()}")
        print(f"SDK resource: {sample.package_id} extracted into the temporary E root")

        credential_bytes = secrets.token_bytes(32)
        credential = (
            base64.urlsafe_b64encode(credential_bytes).rstrip(b"=").decode("ascii")
        )
        credential_digest = hashlib.sha256(credential_bytes).digest()
        local_host = _LocalDemoHost(credential)
        http_transport = _OfflineHttpTransport()
        message_port = _RecordingMessagePort()

        runtime_construction_started = True
        runtime = CoreRuntime(
            database=SQLiteDatabase(work_root / "runtime.sqlite3"),
            extension_root=extension_root,
            file_root=file_root,
            secret_root=secret_root,
            secret_codec=_DemoOnlySecretCodec(),
            http_transport=http_transport,
            renderer=_PlainTextRenderer(),
            display_limits=validate_contract(DisplayLimits(2, 4096)),
            message_port=message_port,
            admin_context_validator=local_host.validate_admin_context,
            host_ingress_validator=local_host.validate_host_ingress,
            config_principal_id="local-demo-config",
            identity_namespace="local-demo-host",
            utc_clock=_aware_utc_now,
            pump_interval=3600,
            cleanup_timeout=2.0,
        )

        report = await runtime.start()
        print(f"CoreRuntime started; SQLite schema={report.migration_version}")

        credential_state = await runtime.admin_credential_repository.bootstrap(
            credential_digest
        )
        admin_context = local_host.authenticate_local_admin(
            credential, generation=credential_state.generation
        )
        activated = await runtime.admin_operations.set_enabled(
            None,
            "offline_sample/status",
            True,
            expected_registry_revision=runtime.registry.snapshot().revision,
            authorization=admin_context,
        )
        if not activated.enabled or activated.lifecycle.value != "active":
            raise RuntimeError("offline sample activation did not become active")
        print(
            f"AdminOperations activated {activated.module_id} "
            f"(epoch={activated.epoch}, lifecycle={activated.lifecycle.value})"
        )

        outcome = await runtime.invoke_command(
            "offline_sample/status",
            "status",
            {},
            ingress=local_host.command_ingress(),
        )
        if outcome.result is None or outcome.result.status.value != "success":
            raise RuntimeError("offline sample command did not return success")
        if outcome.output.status is not OutputStatus.SENT:
            raise RuntimeError(
                f"offline sample output was not sent: {outcome.output.status.value}"
            )
        print(
            f"command status: {outcome.result.status.value}; "
            f"message output: {outcome.output.status.value}; "
            f"recorded sends: {len(message_port.messages)}"
        )
        print(f"offline HTTP attempts: {http_transport.attempts}")
    except BaseException as original_error:
        if runtime is not None:
            try:
                await _close_runtime_and_cleanup(runtime, work_root)
            except BaseException as cleanup_error:
                print(
                    "Cleanup also failed after the demo error; preserving the "
                    f"original error {original_error!r}. Cleanup error: "
                    f"{cleanup_error!r}. Temporary root: {work_root}"
                )
        elif runtime_construction_started:
            print(
                "CoreRuntime construction did not return an owner; retaining its "
                f"temporary tree for inspection: {work_root}. Remove it only "
                "after confirming no runtime resources were started."
            )
        else:
            try:
                shutil.rmtree(work_root)
            except OSError as cleanup_error:
                print(
                    "Could not remove the temporary tree after setup failed; "
                    f"preserving the original error {original_error!r}. "
                    f"Cleanup error: {cleanup_error!r}. Temporary root: {work_root}"
                )
        raise
    else:
        if runtime is None:
            raise RuntimeError("CoreRuntime was not constructed")
        await _close_runtime_and_cleanup(runtime, work_root)
        if work_root.exists():
            raise RuntimeError(
                f"temporary runtime tree still exists after close: {work_root}"
            )


def main() -> None:
    """Run the default-discovery, admin activation, and command-output path."""

    asyncio.run(_run())


if __name__ == "__main__":
    main()


__all__ = ["main"]
