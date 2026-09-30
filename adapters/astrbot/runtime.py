"""One AstrBot-owned CoreRuntime and trusted command ingress per plugin instance."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ...api.contexts import InvocationOrigin
from ...api.display import DisplayLimits
from ...api.manifests import ModuleManifest, PrivacyFloor
from ...api.services import CapabilityHealth, HealthStatus
from ...api.subscriptions import ConversationKind
from ...extensions.discovery import discover_packages
from ...infrastructure.key_provider import EnvironmentKeyProvider
from ...infrastructure.secret_codec import AESGCMSecretCodec
from ...presentation.rendering import GenericDisplayRenderer, RenderingBounds
from ...services.core_runtime import (
    CoreRuntime,
    CoreRuntimeCleanupPending,
    HostIngress,
)
from ...services.source_credentials import SourceCredentialPolicy
from .bundled import install_bundled_ff14
from .command_bridge import AstrBotCommandBridge, CommandInvocation
from .message_port import AstrBotMessagePort, MessageChainFactory, PlainFactory

PLUGIN_NAME = "astrbot_plugin_yomihime_game_link"
BUNDLED_SOURCE_HOSTS = {
    "ff14/ff14": {
        "xivapi_items": "xivapi-v2.xivcdn.com",
        "garland_items": "garlandtools.cn",
        "fflogs_public_global": "www.fflogs.com",
        "fflogs_public_cn": "cn.fflogs.com",
        "fflogs_stats_global": "www.fflogs.com",
        "fflogs_stats_cn": "cn.fflogs.com",
        "ff14_calendar_primary": "calendar.google.com",
        "ff14_calendar_fallback": "p66-caldav.icloud.com",
    }
}
_BUNDLED_FFLOGS_CREDENTIAL_SOURCES = (
    ("fflogs_public_global", "credential_fflogs_global", "www.fflogs.com"),
    ("fflogs_public_cn", "credential_fflogs_cn", "cn.fflogs.com"),
)
_INITIALIZING = "服务正在初始化，请稍后重试。"
_UNTRUSTED_EVENT = "无法确认消息来源，命令未执行。"
_OWNER_DIRECT_HINT = "该本人管理命令仅支持私聊，请前往私聊继续。"


@dataclass(frozen=True, slots=True)
class _IngressEvidence:
    seal: object
    event: object
    adapter_id: str
    sender_id: str
    conversation_id: str
    conversation_kind: ConversationKind


class AstrBotRuntime:
    """Own a single Core runtime, its module pump and the AstrBot output port."""

    def __init__(
        self,
        context: object,
        *,
        plugin_root: str | Path,
        data_dir: str | Path,
        plain_factory: PlainFactory | None = None,
        chain_factory: MessageChainFactory | None = None,
        core_factory: Callable[..., CoreRuntime] = CoreRuntime,
        http_transport_factory: Callable[[], object] | None = None,
    ) -> None:
        if context is None:
            raise TypeError("AstrBot Context is required")
        self._context = context
        self._plugin_root = Path(plugin_root)
        self._data_dir = Path(data_dir)
        self._plain_factory = plain_factory
        self._chain_factory = chain_factory
        self._core_factory = core_factory
        if http_transport_factory is not None and not callable(http_transport_factory):
            raise TypeError("http_transport_factory must be callable")
        self._http_transport_factory = http_transport_factory
        self._lock = asyncio.Lock()
        self._evidence_seal = object()
        self._core: CoreRuntime | None = None
        self._bridge: AstrBotCommandBridge | None = None
        self._http_transport: object | None = None
        self._transport_closed = False
        self._trusted_bundle = False
        self._source_credential_policies: tuple[SourceCredentialPolicy, ...] = ()
        self._isolated_extension_dir: tempfile.TemporaryDirectory[str] | None = None
        self._ready = False

    @property
    def core_runtime(self) -> CoreRuntime | None:
        """Expose the owned instance for lifecycle diagnostics and host tests."""
        return self._core

    @property
    def ready(self) -> bool:
        return self._ready

    async def initialize(self) -> None:
        """Install the exact bundled module, then start the one CoreRuntime."""
        async with self._lock:
            if self._ready and self._core is not None:
                return
            if self._core is not None or self._http_transport is not None:
                await self._close_owned_runtime()
            else:
                self._cleanup_isolated_extension_dir()

            installation = install_bundled_ff14(self._plugin_root, self._data_dir)
            self._trusted_bundle = installation.trusted
            if installation.trusted:
                extension_root = installation.extension_root
                defaults = self._bundled_manifest_expectations(self._plugin_root)
                self._source_credential_policies = (
                    self._bundled_source_credential_policies(extension_root)
                )
            else:
                self._isolated_extension_dir = tempfile.TemporaryDirectory(
                    prefix="ygl-disabled-extensions-", dir=self._data_dir
                )
                extension_root = Path(self._isolated_extension_dir.name)
                defaults = {}
                self._source_credential_policies = ()
            message_port = AstrBotMessagePort(
                self._context,
                plain_factory=self._plain_factory,
                chain_factory=self._chain_factory,
            )
            try:
                self._http_transport = self._new_http_transport()
                self._transport_closed = False
                if not callable(getattr(self._http_transport, "request", None)):
                    raise TypeError("HTTP transport must implement request")
                if not callable(getattr(self._http_transport, "close", None)):
                    raise TypeError("owned HTTP transport must implement async close")
                core = self._core_factory(
                    database=self._data_dir / "runtime.sqlite3",
                    extension_root=extension_root,
                    file_root=self._data_dir / "files",
                    secret_root=self._data_dir / "secrets",
                    secret_codec=AESGCMSecretCodec(
                        EnvironmentKeyProvider("YGL_SECRET_KEY")
                    ),
                    http_transport=self._http_transport,
                    renderer=GenericDisplayRenderer(
                        RenderingBounds(
                            max_chars_per_page=3000,
                            max_lines_per_page=100,
                            max_fields_per_block=32,
                            max_rows_per_block=50,
                            max_asset_read_bytes=1,
                            max_image_dimension=1,
                            max_asset_reads=1,
                            max_blocks_per_document=32,
                            max_members_per_batch=8,
                        )
                    ),
                    display_limits=DisplayLimits(2, 4096),
                    message_port=message_port,
                    admin_context_validator=lambda *_args: False,
                    host_ingress_validator=self._validate_ingress,
                    config_principal_id=PLUGIN_NAME,
                    identity_namespace=PLUGIN_NAME,
                    trusted_bundled_manifests=defaults,
                    source_health=self._source_health,
                    source_credential_policies=self._source_credential_policies,
                )
            except BaseException:
                try:
                    await self._close_http_transport()
                finally:
                    self._cleanup_isolated_extension_dir()
                raise
            self._core = core
            try:
                await core.start()
            except BaseException:
                self._ready = False
                self._bridge = None
                try:
                    closed = await core.close()
                except BaseException:
                    # Keep the owner reference so terminate() can retry cleanup.
                    raise
                if closed:
                    self._core = None
                    self._cleanup_isolated_extension_dir()
                    await self._close_http_transport()
                else:
                    raise CoreRuntimeCleanupPending("core_runtime_close")
                raise
            self._bridge = AstrBotCommandBridge(core.registry)
            self._ready = True

    async def terminate(self) -> None:
        """Stop ingress and close the owned pump/modules/database once."""
        async with self._lock:
            self._ready = False
            await self._close_owned_runtime()

    async def _close_owned_runtime(self) -> None:
        self._ready = False
        self._bridge = None
        core = self._core
        if core is not None:
            if not await core.close():
                raise CoreRuntimeCleanupPending("core_runtime_close")
            self._core = None
        await self._close_http_transport()
        self._cleanup_isolated_extension_dir()

    def _new_http_transport(self) -> object:
        factory = self._http_transport_factory
        if factory is None:
            from ...infrastructure.http_transport import AioHttpTransport

            return AioHttpTransport(proxy_url=self._configured_http_proxy())
        return factory()

    def _configured_http_proxy(self) -> str | None:
        """Read only AstrBot's host config; chat input never selects a proxy."""
        getter = getattr(self._context, "get_config", None)
        if not callable(getter):
            return None
        try:
            config = getter()
        except Exception as exc:
            raise RuntimeError(
                "AstrBot HTTP proxy configuration is unavailable"
            ) from exc
        if not isinstance(config, Mapping):
            return None
        value = config.get("http_proxy")
        if value is None or value == "":
            return None
        if type(value) is not str:
            raise TypeError("AstrBot http_proxy must be a string")
        return value

    @staticmethod
    def _bundled_manifest_expectations(
        plugin_root: Path,
    ) -> dict[str, ModuleManifest]:
        """Return exact declarations from the reviewed packaged FF14 source."""
        packages = discover_packages(plugin_root / "modules")
        matching = tuple(
            item
            for item in packages
            if item.package_id == "ff14" and item.valid and item.manifest is not None
        )
        if len(packages) != 1 or len(matching) != 1:
            raise RuntimeError("trusted bundled manifest source is unavailable")
        module = next(
            (item for item in matching[0].manifest.modules if item.module_id == "ff14"),
            None,
        )
        if module is None:
            raise RuntimeError("trusted bundled FF14 module is unavailable")
        return {"ff14/ff14": module}

    @staticmethod
    def _bundled_source_credential_policies(
        extension_root: Path,
    ) -> tuple[SourceCredentialPolicy, ...]:
        """Bind only exact FFLogs source and sensitive-field declarations.

        Legacy M0/M1 manifests without FFLogs credential declarations receive
        no policies. Once a credentialed source or sensitive alias is present,
        its counterpart and fixed host/field contract must also match; a
        partial declaration fails closed instead of exposing an anonymous
        FFLogs source.
        """

        packages = discover_packages(extension_root)
        matching = tuple(
            item
            for item in packages
            if item.package_id == "ff14" and item.valid and item.manifest is not None
        )
        if len(matching) != 1:
            raise RuntimeError("trusted bundled FF14 manifest is unavailable")
        module = next(
            (item for item in matching[0].manifest.modules if item.module_id == "ff14"),
            None,
        )
        if module is None:
            raise RuntimeError("trusted bundled FF14 module is unavailable")
        sources = {item.source_id: item for item in module.sources}
        fields = {item.name: item for item in module.config_fields}
        policies: list[SourceCredentialPolicy] = []
        for source_id, alias, host in _BUNDLED_FFLOGS_CREDENTIAL_SOURCES:
            declaration = sources.get(source_id)
            field = fields.get(alias)
            if declaration is None and field is None:
                continue
            if (
                declaration is None
                or field is None
                or declaration.host != host
                or declaration.credential_ref is not None
                or field.sensitive is not True
                or field.required is not False
                or field.default is not None
            ):
                raise RuntimeError(
                    "trusted bundled FFLogs credential declaration is incomplete"
                )
            policies.append(
                SourceCredentialPolicy(
                    "ff14/ff14",
                    source_id,
                    alias,
                    host,
                    ("/api/v2/client",),
                    host,
                    "/oauth/token",
                )
            )
        return tuple(policies)

    async def _close_http_transport(self) -> None:
        transport = self._http_transport
        if transport is None:
            return
        close = getattr(transport, "close", None)
        if not callable(close):
            raise CoreRuntimeCleanupPending("http_transport_close")
        try:
            await close()
        except BaseException as exc:
            raise CoreRuntimeCleanupPending("http_transport_close") from exc
        self._transport_closed = True
        self._http_transport = None

    async def _source_health(self, module_id: str, source_id: str) -> CapabilityHealth:
        """Report local wiring readiness only; never probe remote endpoints."""
        unavailable = CapabilityHealth(HealthStatus.UNAVAILABLE, "source_unavailable")
        if not self._trusted_bundle or module_id not in BUNDLED_SOURCE_HOSTS:
            return unavailable
        transport = self._http_transport
        if transport is None or self._transport_closed:
            return unavailable
        try:
            if getattr(transport, "closed", False) is True:
                return unavailable
            module = self._core.registry.snapshot().module(module_id)
        except Exception:
            return unavailable
        declaration = next(
            (item for item in module.manifest.sources if item.source_id == source_id),
            None,
        )
        allowed_host = BUNDLED_SOURCE_HOSTS[module_id].get(source_id)
        credential_alias = next(
            (
                alias
                for expected_source, alias, _host in _BUNDLED_FFLOGS_CREDENTIAL_SOURCES
                if expected_source == source_id
            ),
            None,
        )
        credential_policy_ready = any(
            policy.module_id == module_id
            and policy.source_id == source_id
            and policy.credential_ref == credential_alias
            for policy in self._source_credential_policies
        )
        if (
            declaration is None
            or allowed_host is None
            or declaration.host != allowed_host
            or (credential_alias is None and declaration.credential_ref is not None)
            or (
                credential_alias is not None
                and (
                    declaration.credential_ref is not None
                    or not credential_policy_ready
                )
            )
        ):
            return unavailable
        # AVAILABLE means only that an exact bundled declaration is wired to
        # the open, owned transport. HealthResolver may normalize its reason;
        # this is deliberately not a claim about remote endpoint reachability.
        return CapabilityHealth(HealthStatus.AVAILABLE, "local_transport_ready")

    def _cleanup_isolated_extension_dir(self) -> None:
        directory = self._isolated_extension_dir
        if directory is not None:
            directory.cleanup()
            self._isolated_extension_dir = None

    async def handle_event(self, event: object) -> str | None:
        """Return host help/errors, or dispatch once through the Core output port."""
        if not self._ready or self._core is None or self._bridge is None:
            return _INITIALIZING
        try:
            action = self._bridge.parse(event)
        except Exception:
            return "命令暂时不可用。"
        if isinstance(action, str):
            return action
        if not isinstance(action, CommandInvocation):
            return "命令暂时不可用。"
        try:
            ingress = self._make_ingress(event)
        except Exception:
            return _UNTRUSTED_EVENT
        owner_group_command = (
            ingress.conversation_kind is ConversationKind.GROUP
            and self._is_owner_command(action)
        )
        if owner_group_command:
            if self._validate_ingress(InvocationOrigin.COMMAND, ingress):
                return _OWNER_DIRECT_HINT
            return _UNTRUSTED_EVENT
        try:
            await self._core.invoke_command(
                action.module_id,
                action.operation_path,
                action.parameters,
                ingress=ingress,
            )
        except Exception:
            # Core output may have started before an unexpected exception.
            # Never create a second reply that could duplicate an UNKNOWN send.
            return None
        return None

    def _is_owner_command(self, invocation: CommandInvocation) -> bool:
        core = self._core
        if core is None:
            return False
        try:
            module = core.registry.snapshot().module(invocation.module_id)
        except Exception:
            return False
        command = next(
            (
                item
                for item in module.manifest.commands
                if item.operation_path == invocation.operation_path
            ),
            None,
        )
        if command is None:
            return False
        capability = next(
            (
                item
                for item in module.manifest.capabilities
                if item.capability_id == command.capability_id
            ),
            None,
        )
        return capability is not None and capability.privacy_floor is PrivacyFloor.OWNER

    def _make_ingress(self, event: object) -> HostIngress:
        adapter, sender, conversation, kind = self._read_event_facts(event)
        evidence = _IngressEvidence(
            self._evidence_seal, event, adapter, sender, conversation, kind
        )
        return HostIngress(
            adapter_id=adapter,
            actor_id=f"{adapter}:{sender}",
            conversation_id=conversation,
            delivery_route=adapter,
            conversation_kind=kind,
            evidence=evidence,
        )

    def _validate_ingress(self, origin: InvocationOrigin, ingress: HostIngress) -> bool:
        if (
            origin is not InvocationOrigin.COMMAND
            or not isinstance(ingress, HostIngress)
            or ingress.grant_reference is not None
            or not isinstance(ingress.evidence, _IngressEvidence)
        ):
            return False
        evidence = ingress.evidence
        if evidence.seal is not self._evidence_seal:
            return False
        try:
            facts = self._read_event_facts(evidence.event)
        except Exception:
            return False
        adapter, sender, conversation, kind = facts
        return (
            facts
            == (
                evidence.adapter_id,
                evidence.sender_id,
                evidence.conversation_id,
                evidence.conversation_kind,
            )
            and ingress.adapter_id == adapter
            and ingress.actor_id == f"{adapter}:{sender}"
            and ingress.conversation_id == conversation
            and ingress.delivery_route == adapter
            and ingress.conversation_kind is kind
        )

    @staticmethod
    def _read_event_facts(
        event: object,
    ) -> tuple[str, str, str, ConversationKind]:
        adapter = _event_text(event, "get_platform_id")
        sender = _event_text(event, "get_sender_id")
        conversation = _event_text(event, "get_session_id")
        getter = getattr(event, "get_message_type", None)
        if not callable(getter):
            raise TypeError("event message type is unavailable")
        message_type = getter()
        message_type = getattr(message_type, "value", message_type)
        if message_type == "GroupMessage":
            kind = ConversationKind.GROUP
        elif message_type == "FriendMessage":
            kind = ConversationKind.DIRECT
        else:
            raise ValueError("event message type is unsupported")
        if ":" in adapter:
            raise ValueError("platform id is not a valid AstrBot session component")
        return adapter, sender, conversation, kind


def _event_text(event: object, method: str) -> str:
    getter = getattr(event, method, None)
    if not callable(getter):
        raise TypeError("event identity getter is unavailable")
    value = getter()
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("event identity is invalid")
    return value


__all__ = ["AstrBotRuntime"]
