"""One AstrBot-owned CoreRuntime and trusted command ingress per plugin instance."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, time
from types import MappingProxyType
from typing import Callable

from ...api.administration import AdminAuthorizationDenied, AdminOperation
from ...api.contexts import InvocationOrigin
from ...api.display import DisplayLimits
from ...api.manifests import ModuleManifest, PrivacyFloor
from ...api.services import CapabilityHealth, ConfigTarget, HealthStatus
from ...api.subscriptions import ConversationKind
from ...extensions.discovery import discover_packages
from ...extensions.source_snapshot import PackageProvenance
from ...infrastructure.key_provider import EnvironmentKeyProvider
from ...infrastructure.secret_codec import AESGCMSecretCodec
from ...modules.ff14.config import CALENDAR_VALUE_VALIDATORS, FF14ConfigSnapshot
from ...presentation.rendering import GenericDisplayRenderer, RenderingBounds
from ...scripts.prepare_ff14_config_migration import INPUT_FILENAME, load_prepared_input
from ...services.admin_authorization import AdminAuthorizationService
from ...services.configuration import ConfigurationValueError
from ...services.core_runtime import (
    CoreRuntime,
    CoreRuntimeCleanupPending,
    HostIngress,
    TrustedSubscriptionGate,
)
from ...services.source_credentials import SourceCredentialPolicy
from .bundled import BundledExtensionError, install_bundled_ff14
from .command_bridge import AstrBotCommandBridge, CommandInvocation
from .config_adapter import ordinary_migration_fields
from .message_port import AstrBotMessagePort, MessageChainFactory, PlainFactory
from .web_public import (
    DEPLOYED_CAPABILITIES,
    QUERY_CAPABILITIES,
    HostPublicWebValidator,
    WebPublicRejected,
    origin_configuration,
    project_result,
    validated_bearer,
)

PLUGIN_NAME = "astrbot_plugin_yomihime_game_link"
BUNDLED_SOURCE_HOSTS = {
    "ff14/ff14": {
        "universalis_market": "universalis.app",
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
_BUNDLE_FAILURE_DIAGNOSTICS = {
    "existing_manifest_mismatch": (
        "现有内置模块清单与发行包不一致。请由管理员核对发行包和受信内容，"
        "恢复完整模块后重载插件。"
    ),
    "unsupported_environment": (
        "当前 Windows/Python 环境未通过安全扫描器资格验证。请使用已支持的运行环境并重启宿主，"
        "或向维护者提供脱敏的环境信息进行核验。"
    ),
    "package_permission_denied": "插件数据目录访问被拒绝。请由管理员检查目录权限后重载插件。",
    "extension_root_permission_denied": "内置模块目录访问被拒绝。请由管理员检查目录权限后重载插件。",
    "package_root_unavailable": (
        "无法创建内置模块暂存目录。请检查 Windows 长路径支持、目录可用性和磁盘空间后重载插件。"
    ),
    "extension_root_unavailable": (
        "无法创建内置模块目录。请检查目录可用性和磁盘空间后重载插件。"
    ),
    "package_write_failed": (
        "内置模块写入或完整性检查失败。请由管理员核对发行包和目录状态后重载插件。"
    ),
    "bundle_unavailable": "安装原因未能安全确认。请由管理员检查宿主安装日志和发行包后重载插件。",
}


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
        config: Mapping[str, object] | None = None,
        raw_legacy_config: Mapping[str, object] | None = None,
        plain_factory: PlainFactory | None = None,
        chain_factory: MessageChainFactory | None = None,
        core_factory: Callable[..., CoreRuntime] = CoreRuntime,
        http_transport_factory: Callable[[], object] | None = None,
    ) -> None:
        if context is None:
            raise TypeError("AstrBot Context is required")
        self._context = context
        self._web_state, self._web_origin = origin_configuration(config)
        self._web_validator: HostPublicWebValidator | None = None
        self._config_snapshot = None
        self._config_error: str | None = None
        # Injected Host config is hydrated and cannot establish raw key presence.
        # raw_legacy_config is a trusted offline fixture input, never chat input.
        self._raw_legacy_config = (
            dict(raw_legacy_config) if raw_legacy_config is not None else None
        )
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
        self._bundle_failure_reason: str | None = None
        self._source_credential_policies: tuple[SourceCredentialPolicy, ...] = ()
        self._isolated_extension_dir: tempfile.TemporaryDirectory[str] | None = None
        self._ready = False
        self._generation = 0
        self._closing = False
        self._admin_source = None

    def management_current(self, core, source):
        return (
            core is self._core
            and source is self._admin_source
            and not self._closing
            and getattr(core, "management_available", False) is True
        )

    def management_entry(self):
        if (
            not self.management_current(self._core, self._admin_source)
            or self._admin_source is None
        ):
            raise AdminAuthorizationDenied
        return self._core, self._admin_source

    async def recover_management(
        self, core, revisions, *, authorization, complete_from_current
    ):
        async with self._lock:
            current, source = self.management_entry()
            if current is not core:
                raise AdminAuthorizationDenied
            try:
                result = await core.recover_management(
                    revisions,
                    authorization=authorization,
                    complete_from_current=complete_from_current,
                )
            except BaseException:
                # A preparatory failure retains only a healthy foundation;
                # failures after business startup require complete cleanup.
                if not core.configuration_blocked:
                    source.close()
                    await self._close_owned_runtime()
                raise
            if not self.management_current(core, source):
                raise AdminAuthorizationDenied
            self._config_error = None
            self._bridge = AstrBotCommandBridge(core.registry)
            self._ready = True
            return result

    @property
    def core_runtime(self) -> CoreRuntime | None:
        """Expose the owned instance for lifecycle diagnostics and host tests."""
        return self._core

    @property
    def ready(self) -> bool:
        return self._ready

    def public_web_status(self) -> dict:
        return {
            "schema_version": 1,
            "configuration": self._web_state,
            "origin": self._web_origin,
            "entry_ready": self._web_origin is not None
            and self._web_current(self._core, self._generation),
        }

    def _web_current(self, core: object, generation: int) -> bool:
        return (
            core is not None
            and core is self._core
            and generation == self._generation
            and self._ready
            and not self._closing
        )

    def begin_public_web(self, request: object, endpoint: str, legacy_path: str):
        started_at = monotonic()
        validator = self._web_validator
        if validator is None or not self.public_web_status()["entry_ready"]:
            raise WebPublicRejected("entry_unavailable")
        token, expiry = validated_bearer(request, self._web_origin, legacy_path)
        ticket = validator.begin(endpoint, token, expiry, started_at=started_at)
        return (
            validator,
            ticket,
            self._core,
            self._generation,
            validator.deadline_facts(ticket),
        )

    def public_web_remaining(self, request_state) -> float:
        validator, ticket, core, generation, _facts = request_state
        if validator is not self._web_validator or not self._web_current(
            core, generation
        ):
            raise WebPublicRejected("entry_unavailable")
        return validator.remaining(ticket)

    async def invoke_public_web(
        self, request_state, endpoint: str, parameters: dict
    ) -> dict:
        validator, ticket, core, generation, facts = request_state
        self.public_web_remaining(request_state)
        proof = validator.mint(ticket)
        result = await core.invoke_public_web(
            "ff14/ff14", QUERY_CAPABILITIES[endpoint], parameters, proof=proof
        )
        # Core deliberately revokes proof after result publication. Keep the
        # independent Host request deadline/generation facts for this final hop.
        if validator is not self._web_validator or not self._web_current(
            core, generation
        ):
            raise WebPublicRejected("entry_unavailable")
        if facts[0] <= monotonic() or facts[1] <= time():
            raise WebPublicRejected("session_expired")
        data = project_result(result)
        if (
            not self._web_current(core, generation)
            or facts[0] <= monotonic()
            or facts[1] <= time()
        ):
            raise WebPublicRejected("entry_unavailable")
        return data

    def finish_public_web(self, request_state) -> None:
        request_state[0].revoke(request_state[1])

    @property
    def bundle_failure_reason(self) -> str | None:
        """Return the installer's bounded reason, without paths or payloads."""
        return self._bundle_failure_reason

    def public_module_catalog(self) -> dict:
        """Expose Core's metadata directory without reading module configuration."""
        generation, core, was_ready = self._generation, self._core, self._ready
        if self._closing:
            raise RuntimeError("page state generation changed")
        if core is None or self._config_error is not None and not was_ready:
            return {
                "schema_version": 1,
                "catalog_revision": None,
                "runtime": {
                    "state": "invalid_config" if self._config_error else "not_ready"
                },
                "modules": None,
            }
        data = core.public_module_catalog()
        if (
            self._closing
            or generation != self._generation
            or core is not self._core
            or was_ready != self._ready
        ):
            raise RuntimeError("page state generation changed")
        if not was_ready:
            data["runtime"] = {"state": "not_ready"}
            for module in data["modules"]:
                if module["state"] == "loaded":
                    module["state"] = "unavailable"
                    module["reason"] = "runtime_not_ready"
        return data

    async def public_ff14_page_state(self, *, include_values: bool = False) -> dict:
        """Project only global metadata; never read credentials or private data."""
        generation, core, was_ready = self._generation, self._core, self._ready
        config_snapshot, config_error = self._config_snapshot, self._config_error
        if core is not None and was_ready:
            try:
                async with core.lifecycle.admission.mutation(
                    "host-ordinary-defaults-read"
                ):
                    module_config = await core.config_repository.current(
                        ConfigTarget(PLUGIN_NAME, "ff14/ff14")
                    )
                    defaults = await core.core_defaults.current()
                    config_snapshot = FF14ConfigSnapshot.from_values(
                        {**module_config.values, "core_defaults": defaults.values}
                    )
                config_error = None
            except ConfigurationValueError as exc:
                config_snapshot = None
                config_error = exc.field
            except Exception:
                config_snapshot = None
        gate = {
            "supported": False,
            "enabled": None,
            "can_run": None,
            "reason": "unsupported",
        }
        if core is not None:
            try:
                state = await core.subscription_gate_state("ff14/ff14")
                gate = {
                    "supported": state.supported,
                    "enabled": state.enabled,
                    "can_run": state.can_run,
                    "reason": state.reason,
                }
            except Exception:
                gate = {
                    "supported": True,
                    "enabled": None,
                    "can_run": None,
                    "reason": "state_unknown",
                }
        if (
            self._closing
            or generation != self._generation
            or core is not self._core
            or was_ready != self._ready
        ):
            raise RuntimeError("page state generation changed")
        if not was_ready and gate["can_run"] is True:
            gate = {
                "supported": True,
                "enabled": True,
                "can_run": False,
                "reason": "runtime_not_ready",
            }
        # Current-read errors belong to this projection, not the startup latch.
        self._config_snapshot = config_snapshot
        invalid = config_error is not None
        ready = self._ready and self._core is not None
        state = "invalid_config" if invalid else "ready" if ready else "not_ready"
        reason = (
            "ordinary_config_invalid"
            if invalid
            else self._safe_bundle_reason(self._bundle_failure_reason)
            if self._bundle_failure_reason is not None
            else None
            if ready
            else "runtime_not_ready"
        )
        registered = enabled = None
        declared = None
        if self._core is not None and not self._config_error:
            try:
                snapshot = self._core.registry.snapshot()
                module = snapshot.modules.get("ff14/ff14")
                registered = module is not None
                enabled = module.enabled if module is not None else False
                declared = (
                    {source.source_id for source in module.manifest.sources}
                    if module is not None
                    else set()
                )
            except Exception:
                # Failure to read a Registry is not proof of non-registration.
                registered = enabled = None
                declared = None
        ordinary = {
            "state": "invalid"
            if invalid
            else "unknown"
            if config_snapshot is None
            else "applied"
            if ready
            else "valid_not_ready",
            "invalid_field": config_error,
        }
        if include_values:
            ordinary["values"] = (
                dict(config_snapshot.as_values())
                if not invalid and config_snapshot is not None
                else None
            )
        sources = (
            "universalis_market",
            "xivapi_items",
            "garland_items",
            "fflogs_public_cn",
            "fflogs_public_global",
            "ff14_calendar_primary",
            "ff14_calendar_fallback",
        )
        return {
            "schema_version": 2,
            "runtime": {"state": state, "reason": reason},
            "module": {"registered": registered, "enabled": enabled},
            "ordinary_config": ordinary,
            "credentials": {
                region: {"configured": None, "state": "unknown"}
                for region in ("cn", "global")
            },
            "subscription_gate": gate,
            "sources": [
                {
                    "id": source_id,
                    "declared": source_id in declared if declared is not None else None,
                    "freshness": "unknown",
                    "last_success_at": None,
                }
                for source_id in sources
            ],
        }

    async def initialize(self) -> None:
        """Install the exact bundled module, then start the one CoreRuntime."""
        generation = self._generation
        async with self._lock:
            if generation != self._generation:
                return
            if self._closing:
                await self._close_owned_runtime()
                self._closing = False
            if self._config_error is not None:
                # No install, transport, Core, secret service or pump may start.
                return
            if self._ready and self._core is not None:
                return
            self._generation += 1
            if self._web_validator is not None:
                self._web_validator.close()
            generation = self._generation
            if self._core is not None or self._http_transport is not None:
                await self._close_owned_runtime()
            else:
                self._cleanup_isolated_extension_dir()

            self._trusted_bundle = False
            self._bundle_failure_reason = None
            try:
                installation = install_bundled_ff14(self._plugin_root, self._data_dir)
            except BundledExtensionError as exc:
                self._bundle_failure_reason = self._safe_bundle_reason(exc.code)
                raise RuntimeError(self._bundle_failure_diagnostic()) from None
            self._trusted_bundle = installation.trusted
            self._bundle_failure_reason = (
                None
                if installation.trusted
                else self._safe_bundle_reason(installation.reason)
            )
            if installation.trusted:
                extension_root = installation.extension_root
                defaults, subscription_gates = self._bundled_manifest_expectations(
                    self._plugin_root
                )
                self._source_credential_policies = (
                    self._bundled_source_credential_policies(extension_root)
                )
            else:
                self._isolated_extension_dir = tempfile.TemporaryDirectory(
                    prefix="ygl-disabled-extensions-", dir=self._data_dir
                )
                extension_root = Path(self._isolated_extension_dir.name)
                defaults = {}
                subscription_gates = MappingProxyType({})
                self._source_credential_policies = ()
            message_port = AstrBotMessagePort(
                self._context,
                plain_factory=self._plain_factory,
                chain_factory=self._chain_factory,
            )
            try:
                validator = HostPublicWebValidator(self._generation, self._web_current)
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
                    trusted_subscription_gates=subscription_gates,
                    source_health=self._source_health,
                    source_credential_policies=self._source_credential_policies,
                    module_config_validators={"ff14/ff14": CALENDAR_VALUE_VALIDATORS},
                    ordinary_migration_fields=(
                        ordinary_migration_fields(PLUGIN_NAME)
                        if self._trusted_bundle
                        else ()
                    ),
                    ordinary_migration_id="ff14-core-defaults-v1",
                    ordinary_migration_source=lambda: (
                        self._raw_legacy_config
                        if self._raw_legacy_config is not None
                        else load_prepared_input(self._data_dir / INPUT_FILENAME)
                    ),
                    public_web_validator=validator,
                    public_web_capabilities=DEPLOYED_CAPABILITIES,
                )
            except BaseException:
                try:
                    await self._close_http_transport()
                finally:
                    self._cleanup_isolated_extension_dir()
                raise
            self._core = core
            if (
                isinstance(
                    getattr(core, "admin_authorization", None),
                    AdminAuthorizationService,
                )
                and core.ordinary_config_migration is not None
            ):
                self._admin_source = core.admin_authorization.register_source(
                    "astrbot-dashboard",
                    resources=core.admin_operations.ordinary_resources(),
                    operations={
                        AdminOperation.READ_CONFIG,
                        AdminOperation.UPDATE_CONFIG,
                        AdminOperation.ROLLBACK_CONFIG,
                        AdminOperation.RECOVER_CONFIG,
                    },
                )
            validator.attach(core)
            self._web_validator = validator
            try:
                await core.start()
            except BaseException as exc:
                self._ready = False
                if isinstance(exc, ConfigurationValueError):
                    self._config_error = exc.field
                self._bridge = None
                if (
                    getattr(core, "configuration_blocked", False) is True
                    and not self._closing
                    and generation == self._generation
                ):
                    if self._config_error is None:
                        self._config_error = "ordinary_configuration"
                    return
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
                if isinstance(exc, ConfigurationValueError):
                    return
                raise
            if (
                self._closing
                or generation != self._generation
                or core is not self._core
            ):
                return
            self._bridge = AstrBotCommandBridge(core.registry)
            self._ready = True

    async def terminate(self) -> None:
        """Stop ingress and close the owned pump/modules/database once."""
        self._generation += 1
        if self._admin_source is not None:
            self._admin_source.close()
        if self._web_validator is not None:
            self._web_validator.close()
        self._closing = True
        self._ready = False
        self._bridge = None
        async with self._lock:
            await self._close_owned_runtime()
            self._closing = False

    async def _close_owned_runtime(self) -> None:
        if self._admin_source is not None:
            self._admin_source.close()
            self._admin_source = None
        if self._web_validator is not None:
            self._web_validator.close()
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
    ) -> tuple[Mapping[str, ModuleManifest], Mapping[str, TrustedSubscriptionGate]]:
        """Return exact declarations from the reviewed packaged FF14 source."""
        packages = discover_packages(plugin_root / "modules")
        matching = tuple(
            item
            for item in packages
            if item.package_id == "ff14" and item.valid and item.manifest is not None
        )
        if len(packages) != 1 or len(matching) != 1:
            raise RuntimeError("trusted bundled manifest source is unavailable")
        package = matching[0]
        modules = tuple(
            item for item in package.manifest.modules if item.module_id == "ff14"
        )
        if len(modules) != 1:
            raise RuntimeError("trusted bundled FF14 module is unavailable")
        module = modules[0]
        expectations = MappingProxyType({"ff14/ff14": module})
        fields = tuple(
            field
            for field in module.config_fields
            if field.name == "ff14_subscriptions_enabled"
        )
        if not fields:
            return expectations, MappingProxyType({})
        provenance = package._provenance
        if (
            len(fields) != 1
            or fields[0].sensitive
            or fields[0].default is not True
            or type(provenance) is not PackageProvenance
            or not provenance.trusted
            or provenance.package_id != package.package_id
            or type(provenance.manifest_sha256) is not bytes
            or len(provenance.manifest_sha256) != 32
        ):
            raise RuntimeError("trusted bundled subscription declaration is invalid")
        return expectations, MappingProxyType(
            {
                "ff14/ff14": TrustedSubscriptionGate(
                    module, fields[0].name, provenance.manifest_sha256
                )
            }
        )

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

    @staticmethod
    def _safe_bundle_reason(reason: str | None) -> str:
        return (
            reason
            if type(reason) is str and reason in _BUNDLE_FAILURE_DIAGNOSTICS
            else "bundle_unavailable"
        )

    def _bundle_failure_diagnostic(self) -> str:
        reason = self._safe_bundle_reason(self._bundle_failure_reason)
        return (
            f"FF14 内置模块未能安全加载 [{reason}]。"
            + _BUNDLE_FAILURE_DIAGNOSTICS[reason]
        )

    async def handle_event(self, event: object) -> str | None:
        """Return host help/errors, or dispatch once through the Core output port."""
        if self._config_error is not None:
            return (
                f"FF14 普通配置无效 [{self._config_error}]，插件尚未启动。"
                "请通过合法 Core 配置管理权限修复；迁移输入保留原始值，"
                "不要用 AstrBot 自动补默认的表单覆盖迁移材料。已有订阅记录保留。"
            )
        if not self._trusted_bundle and self._bundle_failure_reason is not None:
            return self._bundle_failure_diagnostic()
        if not self._ready or self._core is None or self._bridge is None:
            return _INITIALIZING
        try:
            action = self._bridge.parse(event)
        except Exception:
            return "命令暂时不可用。"
        if isinstance(action, str):
            tokens = AstrBotCommandBridge._tokens(event)
            if tokens is not None and (
                not tokens or tokens == ["help"] or tokens[0] == "ff14"
            ):
                try:
                    region = (await self._core.core_defaults.current()).values[
                        "default_region"
                    ]
                except ConfigurationValueError as exc:
                    return (
                        f"FF14 普通配置无效 [{exc.field}]。"
                        "请通过合法 Core 配置管理权限修复；已有订阅记录保留。"
                    )
                except Exception:
                    return "命令暂时不可用。"
                return (
                    action + f"\n默认区域提示：{region}；"
                    "查询命令仍需明确填写区域，显式参数优先。"
                )
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
