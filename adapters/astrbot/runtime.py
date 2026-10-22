"""One AstrBot-owned CoreRuntime and trusted command ingress per plugin instance."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import weakref
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from secrets import token_urlsafe
from threading import RLock
from time import monotonic, time
from types import MappingProxyType
from typing import Callable

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.declarations import PrivacyFloor
from yomihime_game_link_sdk.display import DisplayLimits
from yomihime_game_link_sdk.errors import (
    AccessDenied,
    InvalidInvocation,
    OperationTimeout,
    ParameterError,
    ServiceUnavailable,
)
from yomihime_game_link_sdk.services import CapabilityHealth, HealthStatus
from yomihime_game_link_sdk.subscriptions import ConversationKind, ConversationRef

from ...core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
    ManagementRecoveryProjection,
)
from ...core.contracts.validation_boundary import validate_contract
from ...core.help_catalog import HelpCatalog
from ...core.ports import MessageTarget, RenderedMessage
from ...core.public_errors import public_boundary
from ...extensions.factory_resolver import (
    FilesystemFactorySource,
    PackageFactorySources,
    ReviewedInventoryFactorySource,
    ReviewedSupportLease,
)
from ...extensions.static_page_projection import StaticPageProjection
from ...infrastructure.key_provider import EnvironmentKeyProvider
from ...infrastructure.managed_key_manager import ManagedKeyManager
from ...infrastructure.private_storage import resolve_private_storage
from ...infrastructure.secret_codec import AESGCMSecretCodec
from ...infrastructure.sqlite.database import SQLiteDatabase
from ...presentation.command_card import CommandCardRenderer
from ...presentation.command_text import project_command_heading
from ...presentation.rendering import GenericDisplayRenderer, RenderingBounds
from ...services.admin_authorization import AdminAuthorizationService
from ...services.configuration.defaults import (
    CORE_CONFIG_FIELDS,
    core_config_target,
    presentation_choices,
)
from ...services.configuration.operations import ConfigurationValueError
from ...services.core_runtime import (
    CoreRuntime,
    CoreRuntimeCleanupPending,
    HostIngress,
    HostIngressUnavailable,
)
from ...services.diagnostics import DiagnosticStore
from ...services.external_packages import ExternalPackageHost
from ...services.managed_font import ManagedFont
from ...services.source_credentials import SourceCredentialPolicy
from ...services.trusted_assembly import assemble_reviewed
from .bundled import (
    DEFAULT_BUNDLE_SELECTION,
    BundledExtensionError,
    ReviewedBundleSelection,
    install_bundled_package,
)
from .command_bridge import AstrBotCommandBridge, CommandHelp, CommandInvocation
from .diagnostics import forward_diagnostic
from .message_port import (
    AstrBotMessagePort,
    ImageFactory,
    MessageChainFactory,
    PlainFactory,
)
from .page_sessions import HostModulePageSessions
from .web_public import (
    HostPublicWebValidator,
    WebPublicRejected,
    origin_configuration,
    project_result,
    validated_bearer,
)

PLUGIN_NAME = "astrbot_plugin_yomihime_game_link"


def _merge_resources(*policies):
    """Union only trusted server-selected resource maps."""
    merged = {}
    for policy in policies:
        for target, names in policy.items():
            merged[target] = frozenset(merged.get(target, ())) | frozenset(names)
    return merged


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


@dataclass(slots=True)
class _MessageReceipt:
    event: weakref.ReferenceType = field(repr=False)
    facts: tuple | None = field(repr=False)
    text: str | None = field(repr=False)
    correlation: str | None = field(repr=False)
    expires: float
    closed: bool = False
    closed_reason: str | None = field(default=None, repr=False)


class _MessageReceipts:
    """Host-private exact-object receipts, bounded including live tombstones."""

    def __init__(self):
        self._clock = monotonic
        self._records = {}
        self._lock = RLock()
        self._closed = False

    @staticmethod
    def _close(receipt, reason):
        if not receipt.closed:
            receipt.closed_reason = reason
        receipt.closed = True
        receipt.text = receipt.facts = receipt.correlation = None

    @staticmethod
    def _closed_failure(receipt):
        if receipt.closed_reason == "expired":
            raise OperationTimeout()
        if receipt.closed_reason == "unavailable":
            raise ServiceUnavailable()
        if receipt.closed_reason == "invalid":
            raise ParameterError("parameters are invalid")

    def _prune(self):
        now = self._clock()
        for key, receipt in tuple(self._records.items()):
            if receipt.event() is None:
                self._close(receipt, "collected")
                del self._records[key]
            elif receipt.expires <= now:
                self._close(receipt, "expired")

    @staticmethod
    @public_boundary("proof")
    def _text(event):
        failed = False
        try:
            value = event.get_message_str()
        except Exception:
            failed = True
        if failed:
            raise ServiceUnavailable()
        if type(value) is not str or len(value) > 16384:
            raise ParameterError("parameters are invalid")
        try:
            valid = len(value.encode("utf-8")) <= 65536
        except UnicodeError:
            valid = False
        if not valid:
            raise ParameterError("parameters are invalid")
        return value

    def invalidate_event(self, event, reason):
        with self._lock:
            self._prune()
            receipt = self._records.get(id(event))
            if receipt is not None and receipt.event() is event:
                self._close(receipt, reason)

    def invalidate(self, receipt, reason):
        with self._lock:
            self._prune()
            event = receipt.event()
            if event is not None and self._records.get(id(event)) is receipt:
                self._close(receipt, reason)

    @public_boundary("proof")
    def live(self, receipt):
        with self._lock:
            self._prune()
            if not isinstance(receipt, _MessageReceipt):
                return False
            event = receipt.event()
            if event is None or self._records.get(id(event)) is not receipt:
                return False
            if receipt.closed:
                self._closed_failure(receipt)
                return False
            return not self._closed

    def check_event(self, event):
        with self._lock:
            self._prune()
            receipt = self._records.get(id(event))
            if receipt is not None and receipt.event() is event:
                if not self.live(receipt):
                    raise AccessDenied("source_revoked")

    @public_boundary("proof")
    def capture(self, event, facts):
        with self._lock:
            self._prune()
            if self._closed:
                raise AccessDenied("source_revoked")
            key = id(event)
            receipt = self._records.get(key)
            if receipt is not None and receipt.event() is event:
                if receipt.closed:
                    self._closed_failure(receipt)
                    raise AccessDenied("source_revoked")
                if receipt.facts != facts:
                    self._close(receipt, "revoked")
                    raise AccessDenied("source_revoked")
                try:
                    text = self._text(event)
                except ParameterError:
                    self._prune()
                    self._close(receipt, "invalid")
                    raise
                except Exception:
                    self._prune()
                    self._close(receipt, "unavailable")
                    raise
                if receipt.text != text:
                    self._close(receipt, "revoked")
                    raise AccessDenied("source_revoked")
                if not self.live(receipt):
                    raise AccessDenied("source_revoked")
                return receipt
            if len(self._records) >= 128:
                raise ServiceUnavailable()
            text = self._text(event)

            def collected(reference):
                with self._lock:
                    current = self._records.get(key)
                    if current is not None and current.event is reference:
                        self._close(current, "collected")
                        del self._records[key]

            failed = False
            try:
                reference = weakref.ref(event, collected)
            except TypeError:
                failed = True
            if failed:
                raise ServiceUnavailable()
            receipt = _MessageReceipt(
                reference, facts, text, token_urlsafe(32), self._clock() + 300
            )
            self._records[key] = receipt
            return receipt

    @public_boundary("proof")
    def current(self, receipt, facts, text, correlation):
        with self._lock:
            if not self.live(receipt):
                return False
            event = receipt.event()
            try:
                valid = (
                    receipt.facts == facts
                    and receipt.text == text
                    and receipt.correlation == correlation
                    and self._text(event) == text
                    and _AstrBotMessageIngress._read_event_facts(event) == facts
                )
            except ParameterError:
                self._prune()
                self._close(receipt, "invalid")
                raise
            except Exception:
                self._prune()
                self._close(receipt, "unavailable")
                raise ServiceUnavailable()
            if not valid:
                self._close(receipt, "revoked")
            return self.live(receipt) and valid

    def close(self):
        with self._lock:
            self._closed = True
            for receipt in self._records.values():
                self._close(receipt, "closed")
            self._records.clear()


@dataclass(frozen=True, slots=True)
class _IngressEvidence:
    seal: object
    event: object = field(repr=False)
    adapter_id: str
    sender_id: str
    conversation_id: str
    conversation_kind: ConversationKind
    origin: InvocationOrigin = InvocationOrigin.COMMAND
    receipt: _MessageReceipt | None = field(default=None, repr=False)
    tool_handle: object | None = field(default=None, repr=False)
    command_text: str | None = field(default=None, repr=False)


class _AstrBotMessageIngress:
    """Private production receiver shared by controlled offline composition.

    Readiness and published tool ownership come only from its trusted owner.
    """

    def __init__(self, seal, receipts, owner_current):
        self._evidence_seal, self._message_receipts = seal, receipts
        self._owner_current = owner_current

    @public_boundary("proof")
    def make(
        self, event: object, *, origin=InvocationOrigin.COMMAND, tool_handle=None
    ) -> HostIngress:
        receipt = None
        if origin is InvocationOrigin.LLM_TOOL:
            from astrbot.core.platform.astr_message_event import AstrMessageEvent

            if not isinstance(event, AstrMessageEvent):
                raise InvalidInvocation()
            self._message_receipts.check_event(event)
            failed = False
            try:
                facts = self._read_event_facts(event)
            except Exception:
                failed = True
            if failed:
                self._message_receipts.invalidate_event(event, "unavailable")
                raise ServiceUnavailable()
            receipt = self._message_receipts.capture(event, facts)
            facts = receipt.facts
        else:
            facts = self._read_event_facts(event)
        adapter, sender, conversation, kind = facts
        evidence = _IngressEvidence(
            self._evidence_seal,
            receipt.event if receipt else event,
            adapter,
            sender,
            conversation,
            kind,
            origin,
            receipt,
            tool_handle,
            self._message_receipts._text(event)
            if origin is InvocationOrigin.COMMAND
            else None,
        )
        return HostIngress(
            adapter_id=adapter,
            actor_id=f"{adapter}:{sender}",
            conversation_id=conversation,
            delivery_route=adapter,
            conversation_kind=kind,
            evidence=evidence,
            message_text=receipt.text if receipt else None,
            message_correlation=receipt.correlation if receipt else None,
        )

    def validate(self, origin: InvocationOrigin, ingress: HostIngress) -> bool:
        validate_contract(origin)
        if (
            origin not in (InvocationOrigin.COMMAND, InvocationOrigin.LLM_TOOL)
            or not isinstance(ingress, HostIngress)
            or ingress.grant_reference is not None
            or not isinstance(ingress.evidence, _IngressEvidence)
        ):
            return False
        evidence = ingress.evidence
        if evidence.seal is not self._evidence_seal or evidence.origin is not origin:
            return False
        if origin is InvocationOrigin.LLM_TOOL:
            return self.current(origin, ingress)
        if ingress.message_text is not None or ingress.message_correlation is not None:
            return False
        try:
            facts = self._read_event_facts(evidence.event)
            if self._message_receipts._text(evidence.event) != evidence.command_text:
                return False
        except Exception:
            return False
        return self._identity_current(ingress, evidence, facts)

    @staticmethod
    def _identity_current(ingress, evidence, facts):
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

    def _owned(self, receipt, handle):
        failed = False
        try:
            owned = self._owner_current(handle) is True
        except Exception:
            failed = True
        if failed:
            self._message_receipts.invalidate(receipt, "unavailable")
            raise ServiceUnavailable()
        if not owned:
            self._message_receipts.invalidate(receipt, "revoked")
        return owned

    @public_boundary("proof")
    def current(self, origin, ingress):
        if (
            origin is not InvocationOrigin.LLM_TOOL
            or not isinstance(ingress, HostIngress)
            or ingress.grant_reference is not None
            or not isinstance(ingress.evidence, _IngressEvidence)
        ):
            return False
        evidence = ingress.evidence
        receipt = evidence.receipt
        if (
            evidence.seal is not self._evidence_seal
            or evidence.origin is not origin
            or not isinstance(receipt, _MessageReceipt)
            or evidence.event is not receipt.event
        ):
            return False
        if not self._message_receipts.live(receipt):
            return False
        if not self._owned(receipt, evidence.tool_handle):
            return False
        event = receipt.event()
        from astrbot.core.platform.astr_message_event import AstrMessageEvent

        if not isinstance(event, AstrMessageEvent):
            self._message_receipts.invalidate(receipt, "revoked")
            return False
        failed = False
        try:
            facts = self._read_event_facts(event)
        except Exception:
            failed = True
        if failed:
            self._message_receipts.invalidate(receipt, "unavailable")
            raise ServiceUnavailable()
        if not self._identity_current(ingress, evidence, facts):
            self._message_receipts.invalidate(receipt, "revoked")
            return False
        valid = self._message_receipts.current(
            receipt, facts, ingress.message_text, ingress.message_correlation
        )
        if not self._owned(receipt, evidence.tool_handle):
            return False
        return self._message_receipts.live(receipt) and valid

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


class AstrBotRuntime:
    """Own a single Core runtime, its module pump and the AstrBot output port."""

    def __init__(
        self,
        context: object,
        *,
        plugin_root: str | Path,
        data_dir: str | Path,
        config: Mapping[str, object] | None = None,
        plain_factory: PlainFactory | None = None,
        chain_factory: MessageChainFactory | None = None,
        image_factory: ImageFactory | None = None,
        core_factory: Callable[..., CoreRuntime] = CoreRuntime,
        http_transport_factory: Callable[[], object] | None = None,
        font_manager_factory=ManagedFont,
        bundled_selection: ReviewedBundleSelection | None = DEFAULT_BUNDLE_SELECTION,
    ) -> None:
        if context is None:
            raise TypeError("AstrBot Context is required")
        self._context = context
        if (
            bundled_selection is not None
            and type(bundled_selection) is not ReviewedBundleSelection
        ):
            raise TypeError("trusted reviewed bundle selection required")
        self._bundled_selection = bundled_selection
        self._web_state, self._web_origin = origin_configuration(config)
        self._web_validator: HostPublicWebValidator | None = None
        self._config_snapshot = None
        self._config_error: str | None = None
        # Injected Host config is hydrated and cannot establish raw key presence.
        self._plugin_root = Path(plugin_root)
        try:
            self._static_page_projection = StaticPageProjection.load(self._plugin_root)
        except (OSError, ValueError, TypeError):
            # A malformed optional UI composition cannot grant external pages.
            self._static_page_projection = StaticPageProjection()
        self._data_dir = Path(data_dir)
        self._plain_factory = plain_factory
        self._chain_factory = chain_factory
        self._image_factory = image_factory
        self._managed_font = font_manager_factory(
            self._data_dir / "resources" / "fonts"
        )
        self._command_cards = CommandCardRenderer(
            font_provider_factory=self._managed_font.provider
        )
        self._message_port = None
        self._help_image_count = 0
        self._help_tasks = set()
        self._core_factory = core_factory
        if http_transport_factory is not None and not callable(http_transport_factory):
            raise TypeError("http_transport_factory must be callable")
        self._http_transport_factory = http_transport_factory
        self._lock = asyncio.Lock()
        self._evidence_seal = object()
        self._message_receipts = _MessageReceipts()
        self._message_ingress = _AstrBotMessageIngress(
            self._evidence_seal, self._message_receipts, self._tool_owner_current
        )
        self._core: CoreRuntime | None = None
        self._bridge: AstrBotCommandBridge | None = None
        self._tool_publisher = None
        self._private_storage_guard = None
        self._page_sessions = HostModulePageSessions()
        self._http_transport: object | None = None
        self._transport_closed = False
        self._trusted_bundle = False
        self._assembly = None
        self._assembly_lease = None
        self._bundle_failure_reason: str | None = None
        self._source_credential_policies: tuple[SourceCredentialPolicy, ...] = ()
        self._isolated_extension_dir: tempfile.TemporaryDirectory[str] | None = None
        self._ready = False
        self._generation = 0
        self._closing = False
        self._admin_source = None
        self._credential_admin_source = None
        self._key_admin_source = None
        self._module_admin_source = None
        self._extension_host = None

    @property
    def extension_host(self):
        """Public outer-plugin bridge; unavailable until this Core is ready."""
        return self._extension_host if self.ready else None

    def _external_packages_changed(self):
        """Reissue exact management policies after Host-selected owners change."""
        core = self._core
        if core is None or self._closing:
            return
        for source in (self._admin_source, self._module_admin_source):
            if source is not None:
                core.admin_authorization.unregister_source(source)
        self._admin_source = core.admin_authorization.register_source(
            "astrbot-dashboard",
            resources=_merge_resources(
                core.admin_operations.ordinary_resources(),
                core.admin_operations.legacy_config_resources(),
            ),
            operations={
                AdminOperation.READ_CONFIG,
                AdminOperation.UPDATE_CONFIG,
                AdminOperation.RECOVER_CONFIG,
            },
        )
        try:
            resources = core.admin_operations.module_resources()
        except AdminAuthorizationDenied:
            self._module_admin_source = None
        else:
            self._module_admin_source = core.admin_authorization.register_source(
                "astrbot-dashboard-modules",
                resources=resources,
                operations={
                    AdminOperation.LIST_MODULES,
                    AdminOperation.SET_ENABLED,
                    AdminOperation.UNLOAD_MODULE,
                },
            )
        if self._tool_publisher is not None:
            self._tool_publisher.publish()

    def management_current(self, core, source):
        return (
            core is self._core
            and source is not None
            and (
                source is self._admin_source
                or source is self._credential_admin_source
                or source is self._key_admin_source
                or source is self._module_admin_source
            )
            and not self._closing
            and getattr(core, "management_available", False) is True
        )

    def _external_page_bindings(self):
        host = getattr(self, "_extension_host", None)
        return host.page_manifest_bindings() if host is not None else {}

    def _external_page_permitted(self, owner, manifest=None):
        projection = getattr(self, "_static_page_projection", None)
        bindings = self._external_page_bindings()
        return (projection or StaticPageProjection()).permits(owner, bindings, manifest)

    def management_entry(self, *, credentials=False, modules=False, master_key=False):
        source = (
            self._module_admin_source
            if modules
            else self._key_admin_source
            if master_key
            else self._credential_admin_source
            if credentials
            else self._admin_source
        )
        if not self.management_current(self._core, source) or source is None:
            raise AdminAuthorizationDenied
        return self._core, source

    async def module_page_operation(self, action, data, *, request):
        """Bind a real Dashboard session to a current, selected module page.

        AstrBot's parent bridge sends an ordinary Dashboard JWT, not a signed
        originating-DOM claim. The server selects its sole published root;
        body owner/page are selectors intersected with current trusted policy.
        No asset token, client pageName, Referer, or API key grants authority.
        """
        from yomihime_game_link_sdk import ConfigTarget

        from ...services.module_pages import ModulePageGrant, module_page_operation
        from .admin_pages import _DashboardRequestFence
        from .web_admin import verified_dashboard_request

        actions = {
            "module-config-current",
            "module-config-save",
            "module-config-clear",
            "module-credential-status",
            "module-credential-replace",
            "module-credential-clear",
            "module-page-session",
            "module-page-session-close",
        }
        if action not in actions or type(data) is not dict:
            raise AdminAuthorizationDenied
        path = f"/api/plug/{PLUGIN_NAME}/admin/{action}"
        raw, subject, expiry = verified_dashboard_request(request, path)
        dashboard_fence = _DashboardRequestFence(request, path, raw, subject, expiry)
        bearer_digest = self._page_sessions.bearer_digest(
            request.headers.getlist("authorization")[0].split(" ", 1)[1]
        )
        credential = action.startswith("module-credential-")
        core, source = self.management_entry(credentials=credential)
        generation = self._generation
        alive = True

        def request_current():
            return (
                alive
                and generation == self._generation
                and self._ready
                and self.management_current(core, source)
                and dashboard_fence.current(raw)
            )

        bootstrap = action == "module-page-session"
        if action == "module-page-session-close":
            if set(data) != {"page_session"}:
                raise ValueError("invalid page session close")
            self._page_sessions.revoke(
                data["page_session"], subject=subject, bearer_digest=bearer_digest
            )
            if not request_current():
                raise AdminAuthorizationDenied
            return {"closed": True}
        if bootstrap:
            if set(data) not in (
                {"owner", "page", "runtime_id", "module_epoch"},
                {"owner", "page", "runtime_id", "module_epoch", "previous_session"},
            ):
                raise ValueError("invalid page session request")
        elif type(data.get("page_session")) is not str:
            raise AdminAuthorizationDenied
        operation = (
            AdminOperation.READ_CONFIG
            if action in {"module-config-current", "module-credential-status"}
            else AdminOperation.UPDATE_CONFIG
        )

        def binding_now(owner, page, epoch):
            if (
                type(owner) is not str
                or type(page) is not str
                or type(epoch) is not int
            ):
                raise AdminAuthorizationDenied
            module = core.registry.snapshot().modules.get(owner)
            if (
                module is None
                or not self._external_page_permitted(owner, module.manifest)
                or not module.enabled
                or module.epoch != epoch
                or not core.registry.is_active(module)
            ):
                raise AdminAuthorizationDenied
            state = core.lifecycle.state(owner)
            identity = core.lifecycle.current_identity(owner)
            declared = next(
                (item for item in module.manifest.pages if item.route_id == page), None
            )
            if (
                identity is None
                or identity is not state.identity
                or identity.module_epoch != epoch
                or state.lifecycle.value != "active"
                or declared is None
                or declared.access != "public_web"
            ):
                raise AdminAuthorizationDenied
            target = ConfigTarget(core.admin_operations.config_principal_id, owner)
            ordinary = core.admin_operations.ordinary_resources().get(target, ())
            credentials = core.admin_operations.credential_resources(active=True).get(
                target, ()
            )
            return ModulePageGrant(
                owner,
                page,
                identity.runtime_id,
                epoch,
                frozenset(declared.config_fields) & frozenset(ordinary),
                frozenset(declared.credential_fields) & frozenset(credentials),
            )

        async def check_page_binding(owner, page, epoch, actual_action):
            if actual_action != action or not request_current():
                raise AdminAuthorizationDenied
            if await raw.is_disconnected() or not request_current():
                raise AdminAuthorizationDenied
            binding = binding_now(owner, page, epoch)
            if not bootstrap:
                self._page_sessions.require(
                    data["page_session"],
                    subject=subject,
                    bearer_digest=bearer_digest,
                    binding=binding,
                    generation=generation,
                )
            return binding

        binding = await check_page_binding(
            data.get("owner"), data.get("page"), data.get("module_epoch"), action
        )
        if data.get("runtime_id") != binding.runtime_id:
            raise AdminAuthorizationDenied
        target = ConfigTarget(core.admin_operations.config_principal_id, binding.owner)
        if bootstrap:
            # Authentication alone does not establish field rights. Mint only
            # after ordinary and credential resources independently authorize.
            for fields, source_credentials in (
                (binding.config_fields, False),
                (binding.credential_fields, True),
            ):
                if not fields:
                    continue
                current_core, field_source = self.management_entry(
                    credentials=source_credentials
                )
                if current_core is not core:
                    raise AdminAuthorizationDenied
                context = field_source.issue(
                    subject=subject,
                    request=raw,
                    expiry=expiry,
                    operations={
                        AdminOperation.READ_CONFIG,
                        AdminOperation.UPDATE_CONFIG,
                    },
                    resources={target: fields},
                    live=lambda actual: actual is raw
                    and alive
                    and generation == self._generation
                    and self._ready
                    and self.management_current(core, field_source),
                )
                try:
                    for granted_operation in (
                        AdminOperation.READ_CONFIG,
                        AdminOperation.UPDATE_CONFIG,
                    ):
                        await core.admin_authorization.authorize(
                            granted_operation,
                            invocation=None,
                            context=context,
                            resources={target: fields},
                        )
                    current_binding = await check_page_binding(
                        binding.owner, binding.page, binding.module_epoch, action
                    )
                    if current_binding != binding:
                        raise AdminAuthorizationDenied
                finally:
                    field_source.end(context)
            result = self._page_sessions.issue(
                subject=subject,
                bearer_digest=bearer_digest,
                binding=binding,
                generation=generation,
                expiry=expiry,
                previous_session=data.get("previous_session"),
            )
            try:
                if (
                    not request_current()
                    or binding_now(binding.owner, binding.page, binding.module_epoch)
                    != binding
                ):
                    raise AdminAuthorizationDenied
                self._page_sessions.require(
                    result["page_session"],
                    subject=subject,
                    bearer_digest=bearer_digest,
                    binding=binding,
                    generation=generation,
                )
            except BaseException:
                self._page_sessions.revoke(
                    result["page_session"], subject=subject, bearer_digest=bearer_digest
                )
                raise
            return result

        resources = {
            target: binding.credential_fields if credential else binding.config_fields
        }

        def still_current(actual):
            if actual is not raw or not request_current():
                return False
            try:
                current = binding_now(binding.owner, binding.page, binding.module_epoch)
                self._page_sessions.require(
                    data["page_session"],
                    subject=subject,
                    bearer_digest=bearer_digest,
                    binding=current,
                    generation=generation,
                )
                return current == binding
            except Exception:
                return False

        authorization = source.issue(
            subject=subject,
            request=raw,
            expiry=expiry,
            operations={operation},
            resources=resources,
            live=still_current,
        )
        task = asyncio.current_task()
        watcher_stop = False

        async def disconnect():
            nonlocal alive
            while alive and not watcher_stop:
                disconnected = await raw.is_disconnected()
                if watcher_stop:
                    return
                if disconnected:
                    alive = False
                    source.end(authorization)
                    task.cancel()
                    return
                await asyncio.sleep(0.02)

        watcher = asyncio.create_task(disconnect(), name="ygl:module-page-disconnect")
        try:
            result = await module_page_operation(
                core.admin_operations,
                action,
                {key: value for key, value in data.items() if key != "page_session"},
                authorization=authorization,
                runtime_id=binding.runtime_id,
                check_page_binding=check_page_binding,
                descriptor_choices=presentation_choices,
            )
            await check_page_binding(
                binding.owner, binding.page, binding.module_epoch, action
            )
            source.check(authorization, operation, resources)
            return result
        except Exception:
            # A coordinator may wrap a rejected SQLite CAS as a configuration
            # failure after rolling it back. Recheck the same Host proof on the
            # error path as well; its commit fence remains the write authority.
            if not still_current(raw):
                raise AdminAuthorizationDenied from None
            raise
        finally:
            # Joining can await after a result or a primary failure. Keep its
            # proof live until the final synchronous check, while stopping the
            # monitor independently when AnyIO consumes its cancellation.
            primary_error = sys.exception()
            watcher_stop = True
            cancellation_pending = False
            watcher_error = None
            try:
                watcher.cancel()
                try:
                    await watcher
                except asyncio.CancelledError:
                    cancellation_pending = task is not None and task.cancelling() > 0
                except Exception as error:
                    watcher_error = error
                cancellation_pending = cancellation_pending or (
                    task is not None and task.cancelling() > 0
                )
                if not cancellation_pending and not isinstance(
                    primary_error, asyncio.CancelledError
                ):
                    if not still_current(raw):
                        raise AdminAuthorizationDenied from None
                    source.check(authorization, operation, resources)
                    if primary_error is None and watcher_error is not None:
                        raise watcher_error
            finally:
                try:
                    alive = False
                    source.end(authorization)
                finally:
                    if cancellation_pending:
                        raise asyncio.CancelledError

    async def recover_management(
        self, core, revisions, *, authorization
    ) -> ManagementRecoveryProjection:
        async with self._lock:
            current, source = self.management_entry()
            if current is not core or not core.configuration_blocked:
                # Reject a recovery request against an already-running Core before
                # entering startup-failure cleanup; rejection must not stop it.
                raise AdminAuthorizationDenied
            try:
                result = await core.recover_management(
                    revisions,
                    authorization=authorization,
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
            self._bridge = AstrBotCommandBridge(
                core.registry,
                catalog=HelpCatalog(
                    health_query=core.health_resolver.current,
                    active_query=core.registry.is_active,
                ),
            )
            self._ready = True
            await self._publish_tools_or_close(core)
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

    def begin_public_web(
        self, request: object, endpoint, legacy_path: str, *, started_at=None
    ):
        started_at = monotonic() if started_at is None else started_at
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
            *validator._bindings[endpoint], parameters, proof=proof
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
        projection = getattr(self, "_static_page_projection", None)
        return (projection or StaticPageProjection()).catalog(
            data, self._external_page_bindings()
        )

    async def public_ff14_page_state(self, *, include_values: bool = False) -> dict:
        """Deprecated locator only; no module defaults, gates or credential state."""
        return {
            "schema_version": 1,
            "state": "retired",
            "reopen_required": True,
            "host_path": "/#/extension/plugins",
        }

    def public_page_bindings(self):
        if self._assembly is None:
            return {}
        bindings = dict(self._assembly.public_bindings)
        for owner, manifest in self._assembly.manifests.items():
            for page in manifest.pages:
                target = (owner, page.capability_id)
                if target in self._assembly.public_capabilities:
                    bindings[(owner, page.route_id, page.capability_id)] = target
        return bindings

    def compatibility_query_routes(self):
        return tuple(self._assembly.public_bindings) if self._assembly else ()

    def _cleanup_module_owner(self, owner):
        self._page_sessions.revoke_owner(owner)
        if self._web_validator is not None:
            self._web_validator.revoke_owner(owner)
        if self._tool_publisher is not None:
            self._tool_publisher.retire_owner(owner)

    def public_query_parameters(self, endpoint, body):
        if self._assembly is None or endpoint not in self._assembly.public_bindings:
            raise WebPublicRejected("entry_unavailable")
        try:
            return self._assembly.support.query_parameters(endpoint, body)
        except (ValueError, TypeError, KeyError):
            raise WebPublicRejected("request_rejected") from None

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
            self._page_sessions.close()
            self._page_sessions = HostModulePageSessions()
            if self._web_validator is not None:
                self._web_validator.close()
            generation = self._generation
            if self._core is not None or self._http_transport is not None:
                await self._close_owned_runtime()
            else:
                self._cleanup_isolated_extension_dir()

            self._message_receipts.close()
            self._message_receipts = _MessageReceipts()
            self._evidence_seal = object()
            self._message_ingress = _AstrBotMessageIngress(
                self._evidence_seal, self._message_receipts, self._tool_owner_current
            )
            self._trusted_bundle = False
            self._bundle_failure_reason = None
            try:
                installation = install_bundled_package(
                    self._plugin_root, self._data_dir, selection=self._bundled_selection
                )
            except BundledExtensionError as exc:
                self._bundle_failure_reason = self._safe_bundle_reason(exc.code)
                raise RuntimeError(self._bundle_failure_diagnostic()) from None
            self._trusted_bundle = installation.trusted
            self._bundle_failure_reason = (
                None
                if installation.trusted or not installation.selected
                else self._safe_bundle_reason(installation.reason)
            )
            if installation.trusted:
                extension_root = installation.extension_root
                self._assembly_lease = ReviewedSupportLease(
                    installation.inventory.files, selected=True
                )
                support = self._assembly_lease.support
                try:
                    self._assembly = assemble_reviewed(
                        installation.inventory,
                        principal_id=PLUGIN_NAME,
                        support=support,
                        selected=True,
                    )
                except BaseException as exc:
                    if self._assembly_lease is not None:
                        self._assembly_lease.release()
                        self._assembly_lease = None
                    if isinstance(exc, ValueError):
                        raise RuntimeError(
                            "trusted assembly credential declaration or reference is invalid"
                        ) from None
                    raise
                defaults, subscription_gates = (
                    self._assembly.manifests,
                    self._assembly.gates,
                )
                self._source_credential_policies = self._assembly.credential_policies
            else:
                self._isolated_extension_dir = tempfile.TemporaryDirectory(
                    prefix="ygl-disabled-extensions-", dir=self._data_dir
                )
                extension_root = Path(self._isolated_extension_dir.name)
                defaults = {}
                subscription_gates = MappingProxyType({})
                self._source_credential_policies = ()
                self._assembly = None
            message_port = AstrBotMessagePort(
                self._context,
                plain_factory=self._plain_factory,
                chain_factory=self._chain_factory,
                image_factory=self._image_factory,
                command_current=self._command_current,
            )
            self._message_port = message_port
            try:
                validator = HostPublicWebValidator(
                    self._generation,
                    self._web_current,
                    bindings=self.public_page_bindings(),
                    capability_timeouts=self._assembly.capability_timeouts
                    if self._assembly
                    else None,
                )
                self._http_transport = self._new_http_transport()
                self._transport_closed = False
                if not callable(getattr(self._http_transport, "request", None)):
                    raise TypeError("HTTP transport must implement request")
                if not callable(getattr(self._http_transport, "close", None)):
                    raise TypeError("owned HTTP transport must implement async close")
                rendering_bounds = RenderingBounds(
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
                layout = resolve_private_storage(self._data_dir)
                storage_guard = layout.open_guard(isolate_credentials=True)
                storage_guard.__enter__()
                self._private_storage_guard = storage_guard
                database = SQLiteDatabase(layout.database_path)
                managed_keys = ManagedKeyManager(
                    database,
                    layout.key_data_root,
                    layout.secret_root,
                    environment=EnvironmentKeyProvider("YGL_SECRET_KEY"),
                    storage_security=layout.security_status,
                    storage_write_guard=layout.assert_credential_writable,
                    storage_read_guard=getattr(
                        storage_guard, "assert_credentials_readable", None
                    ),
                    storage_layout=layout,
                )
                core = self._core_factory(
                    database=database,
                    extension_root=extension_root,
                    file_root=self._data_dir / "files",
                    secret_root=layout.secret_root,
                    secret_codec=AESGCMSecretCodec(managed_keys),
                    managed_key_manager=managed_keys,
                    http_transport=self._http_transport,
                    renderer=GenericDisplayRenderer(rendering_bounds),
                    command_card_renderer=self._command_cards,
                    command_heading_projector=partial(
                        project_command_heading,
                        max_heading_chars=rendering_bounds.max_chars_per_page,
                    ),
                    display_limits=validate_contract(DisplayLimits(2, 4096)),
                    message_port=message_port,
                    admin_context_validator=lambda *_args: False,
                    host_ingress_validator=self._validate_ingress,
                    host_message_current_validator=self._message_current,
                    config_principal_id=PLUGIN_NAME,
                    identity_namespace=PLUGIN_NAME,
                    factory_source=PackageFactorySources(
                        ReviewedInventoryFactorySource(
                            installation.inventory, selected=True
                        )
                    )
                    if installation.trusted
                    else PackageFactorySources(FilesystemFactorySource()),
                    trusted_bundled_manifests=defaults,
                    trusted_subscription_gates=subscription_gates,
                    source_health=self._source_health,
                    source_credential_policies=self._source_credential_policies,
                    managed_source_credentials=self._assembly.managed_credentials
                    if self._assembly
                    else (),
                    module_config_validators=self._assembly.validators
                    if self._assembly
                    else {},
                    ordinary_config_resources={
                        core_config_target(PLUGIN_NAME): frozenset(
                            field.name for field in CORE_CONFIG_FIELDS
                        ),
                        **(
                            self._assembly.ordinary_resources(PLUGIN_NAME)
                            if self._assembly
                            else {}
                        ),
                    },
                    managed_module_owners=tuple(self._assembly.manifests)
                    if self._assembly
                    else (),
                    owner_cleanup=self._cleanup_module_owner,
                    diagnostic_policies=self._assembly.diagnostic_policies
                    if self._assembly
                    else {},
                    diagnostics=DiagnosticStore(sink=forward_diagnostic),
                    legacy_config_policies=self._assembly.legacy_config_policies
                    if self._assembly
                    else (),
                    public_web_validator=validator,
                    public_web_capabilities=self._assembly.public_capabilities
                    if self._assembly
                    else (),
                    capability_timeouts=self._assembly.capability_timeouts
                    if self._assembly
                    else None,
                    collector_timeouts=self._assembly.collector_timeouts
                    if self._assembly
                    else None,
                )
            except BaseException:
                try:
                    await self._close_http_transport()
                finally:
                    if self._private_storage_guard is not None:
                        self._private_storage_guard.__exit__(None, None, None)
                        self._private_storage_guard = None
                    self._cleanup_isolated_extension_dir()
                    if self._assembly_lease is not None:
                        self._assembly_lease.release()
                        self._assembly_lease = None
                raise
            self._core = core
            if isinstance(
                getattr(core, "admin_authorization", None),
                AdminAuthorizationService,
            ):
                self._admin_source = core.admin_authorization.register_source(
                    "astrbot-dashboard",
                    resources=_merge_resources(
                        core.admin_operations.ordinary_resources(),
                        core.admin_operations.legacy_config_resources(),
                    ),
                    operations={
                        AdminOperation.READ_CONFIG,
                        AdminOperation.UPDATE_CONFIG,
                        AdminOperation.RECOVER_CONFIG,
                    },
                )
                self._key_admin_source = core.admin_authorization.register_source(
                    "astrbot-dashboard-master-key",
                    resources=core.admin_operations.master_key_resources(),
                    operations={
                        AdminOperation.READ_CONFIG,
                        AdminOperation.UPDATE_CONFIG,
                    },
                )
            if (
                isinstance(
                    getattr(core, "admin_authorization", None),
                    AdminAuthorizationService,
                )
                and self._assembly
            ):
                self._module_admin_source = core.admin_authorization.register_source(
                    "astrbot-dashboard-modules",
                    resources=core.admin_operations.module_resources(),
                    operations={
                        AdminOperation.LIST_MODULES,
                        AdminOperation.SET_ENABLED,
                        AdminOperation.UNLOAD_MODULE,
                    },
                )
            validator.attach(core)
            if (
                self._admin_source is not None
                and core.admin_operations.credential_resources()
            ):
                self._credential_admin_source = (
                    core.admin_authorization.register_source(
                        "astrbot-dashboard-source-credentials",
                        resources=core.admin_operations.credential_resources(),
                        operations={
                            AdminOperation.READ_CONFIG,
                            AdminOperation.UPDATE_CONFIG,
                        },
                    )
                )
            self._web_validator = validator
            self._managed_font.start()
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
                await self._managed_font.close()
                try:
                    closed = await core.close()
                except BaseException:
                    # Keep the owner reference so terminate() can retry cleanup.
                    raise
                if closed:
                    self._core = None
                    if self._private_storage_guard is not None:
                        self._private_storage_guard.__exit__(None, None, None)
                        self._private_storage_guard = None
                    if self._assembly_lease is not None:
                        self._assembly_lease.release()
                        self._assembly_lease = None
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
            self._bridge = AstrBotCommandBridge(
                core.registry,
                catalog=HelpCatalog(
                    health_query=core.health_resolver.current,
                    active_query=core.registry.is_active,
                ),
            )
            self._ready = True
            if isinstance(core, CoreRuntime):
                self._extension_host = ExternalPackageHost(
                    self._context,
                    core,
                    core.extension_runtime.factory_source,
                    generation=generation,
                    current=lambda: self._ready
                    and not self._closing
                    and self._core is core
                    and self._generation == generation,
                    changed=self._external_packages_changed,
                    registration_timeout=core._handler_timeout,
                )
            await self._publish_tools_or_close(core)

    async def _publish_tools_or_close(self, core) -> None:
        if not callable(getattr(self._context, "add_llm_tools", None)):
            return
        from .tool_publisher import AstrBotToolPublisher

        try:
            if self._tool_publisher is None:
                self._tool_publisher = AstrBotToolPublisher(
                    self._context, core, self.invoke_tool
                )
            self._tool_publisher.publish()
        except BaseException:
            self._ready = False
            self._closing = True
            core.stop_accepting_host_ingress()
            if self._tool_publisher is not None:
                self._tool_publisher.revoke()
            await self._close_owned_runtime()
            self._closing = False
            raise

    async def terminate(self) -> None:
        """Stop ingress and close the owned pump/modules/database once."""
        self._page_sessions.close()
        if self._extension_host is not None:
            self._extension_host.stop_accepting()
        self._generation += 1
        self._message_receipts.close()
        if self._tool_publisher is not None:
            self._tool_publisher.revoke()
        if self._core is not None and callable(
            getattr(self._core, "stop_accepting_host_ingress", None)
        ):
            self._core.stop_accepting_host_ingress()
        if self._admin_source is not None:
            self._admin_source.close()
        if self._module_admin_source is not None:
            self._module_admin_source.close()
        if self._credential_admin_source is not None:
            self._credential_admin_source.close()
        if self._key_admin_source is not None:
            self._key_admin_source.close()
        if self._web_validator is not None:
            self._web_validator.close()
        self._closing = True
        self._ready = False
        self._bridge = None
        async with self._lock:
            await self._close_owned_runtime()
            self._closing = False

    async def _close_owned_runtime(self) -> None:
        if self._extension_host is not None:
            await self._extension_host.close()
        await self._managed_font.close()
        self._message_receipts.close()
        pending = []
        help_tasks = {
            task
            for task in self._help_tasks
            if task is not asyncio.current_task() and not task.done()
        }
        for task in help_tasks:
            task.cancel()
        if help_tasks:
            _done, unfinished = await asyncio.wait(help_tasks, timeout=5.0)
            if unfinished:
                pending.append(
                    ("help_sends", RuntimeError("help send cleanup pending"))
                )
        self._ready = False
        self._bridge = None
        if self._tool_publisher is not None:
            self._tool_publisher.revoke()
            try:
                self._tool_publisher.cleanup()
            except BaseException as exc:
                pending.append(("tool_publication", exc))
            else:
                self._tool_publisher = None
        if self._admin_source is not None:
            try:
                self._admin_source.close()
            except BaseException as exc:
                pending.append(("admin_source", exc))
            else:
                self._admin_source = None
        if self._module_admin_source is not None:
            try:
                self._module_admin_source.close()
            except BaseException as exc:
                pending.append(("module_admin_source", exc))
            else:
                self._module_admin_source = None
        if self._credential_admin_source is not None:
            try:
                self._credential_admin_source.close()
            except BaseException as exc:
                pending.append(("credential_admin_source", exc))
            else:
                self._credential_admin_source = None
        if self._key_admin_source is not None:
            try:
                self._key_admin_source.close()
            except BaseException as exc:
                pending.append(("key_admin_source", exc))
            else:
                self._key_admin_source = None
        if self._web_validator is not None:
            try:
                self._web_validator.close()
            except BaseException as exc:
                pending.append(("web_validator", exc))
        core = self._core
        if core is not None:
            try:
                if not await core.close():
                    raise CoreRuntimeCleanupPending("core_runtime_close")
            except BaseException as exc:
                pending.append(("core_runtime_close", exc))
            else:
                self._core = None
                self._extension_host = None
        # A pending Core may still own workers using these resources. Preserve
        # its existing retry contract; failed tool removal alone does not block
        # transport/directory closure once Core is quiet.
        if self._core is None:
            if self._private_storage_guard is not None:
                try:
                    self._private_storage_guard.__exit__(None, None, None)
                except BaseException as exc:
                    pending.append(("private_storage_guard", exc))
                else:
                    self._private_storage_guard = None
            if self._assembly_lease is not None:
                self._assembly_lease.release()
                self._assembly_lease = None
            try:
                await self._close_http_transport()
            except BaseException as exc:
                pending.append(("http_transport_close", exc))
            try:
                self._cleanup_isolated_extension_dir()
            except BaseException as exc:
                pending.append(("isolated_extension_dir", exc))
        if pending:
            if len(pending) == 1 and isinstance(
                pending[0][1], CoreRuntimeCleanupPending
            ):
                raise pending[0][1]
            raise CoreRuntimeCleanupPending(
                ",".join(component for component, _ in pending)
            ) from pending[0][1]

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
        unavailable = validate_contract(
            CapabilityHealth(HealthStatus.UNAVAILABLE, "source_unavailable")
        )
        if self._extension_host is not None and self._extension_host.source_ready(
            module_id, source_id
        ):
            transport = self._http_transport
            if (
                transport is not None
                and not self._transport_closed
                and not getattr(transport, "closed", False)
            ):
                return validate_contract(
                    CapabilityHealth(HealthStatus.AVAILABLE, "local_transport_ready")
                )
            return unavailable
        if (
            not self._trusted_bundle
            or self._assembly is None
            or module_id != self._assembly.module_id
        ):
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
        allowed_host = self._assembly.source_hosts.get(source_id)
        credential_alias = next(
            (
                p.credential_ref
                for p in self._assembly.credential_policies
                if p.source_id == source_id
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
        return validate_contract(
            CapabilityHealth(HealthStatus.AVAILABLE, "local_transport_ready")
        )

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
            f"所选内置模块未能安全加载 [{reason}]。"
            + _BUNDLE_FAILURE_DIAGNOSTICS[reason]
        )

    async def handle_event(self, event: object) -> str | None:
        """Return host help/errors, or dispatch once through the Core output port."""
        if self._config_error is not None:
            return (
                f"插件普通配置无效 [{self._config_error}]，插件尚未启动。"
                "请在已登录的插件设置页修正对应字段，再到配置维护重新校验。"
                "已有订阅及其它数据保留；不会自动覆盖无效值。"
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
        except HostIngressUnavailable:
            # This narrow signal is emitted before Gateway/OutputService ran.
            # Recheck the exact event; later/unknown failures still never cause
            # a speculative second reply.
            if self._command_current(ingress):
                return "模块或依赖服务暂时不可用。"
            return None
        except Exception:
            # Core output may have started before an unexpected exception.
            # Never create a second reply that could duplicate an UNKNOWN send.
            return None
        return None

    async def send_help(self, event, text):
        """Send public help with at most three live PNG/base64 reservations.

        A slot reserves 1 MiB PNG plus 4,194,312 conversion bytes,
        held through event.send. Excess help requests choose text before send.
        """
        task = asyncio.current_task()
        reserved = False
        if task is not None:
            self._help_tasks.add(task)
        try:
            ingress = self._make_ingress(event)
            if not self._command_current(ingress):
                return
            reserved = self._help_image_count < 3
            if reserved:
                self._help_image_count += 1
            source = text.source if isinstance(text, CommandHelp) else None
            card = None
            theme = "warm_paper"
            if source is not None:
                if reserved:
                    card, theme = await self._core.prepare_help_card(source)
                else:
                    # Text-only overflow has no asset or raster reservation.
                    # Fence retained catalog facts without loading logo bytes.
                    self._core.require_help_source_current(source)
            presentation = self._command_cards.help(
                str(text),
                image=reserved and (source is None or card is not None),
                card=card,
                core_theme=theme,
            )
            if source is not None:
                self._core.require_help_source_current(source)
            route = ConversationRef(
                ingress.adapter_id,
                ingress.conversation_kind,
                ingress.conversation_id,
                ingress.delivery_route,
            )
            target = MessageTarget(
                ingress.conversation_id,
                recipient_id=(
                    ingress.actor_id if route.kind is ConversationKind.DIRECT else None
                ),
                conversation=route,
            )
            await self._message_port.send_command(
                ingress,
                target,
                RenderedMessage(
                    presentation.text,
                    png_bytes=(
                        presentation.bitmap.content
                        if presentation.bitmap is not None
                        else None
                    ),
                ),
            )
        except Exception:
            # The exact-event adapter may already have begun dispatch.
            return
        finally:
            if reserved:
                self._help_image_count -= 1
            if task is not None:
                self._help_tasks.discard(task)

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

    async def invoke_tool(
        self, module_id, tool_name, parameters, event, *, tool_handle=None
    ):
        if not self._ready or self._closing or self._core is None:
            raise PermissionError("Host tool ingress unavailable")
        from astrbot.core.platform.astr_message_event import AstrMessageEvent

        if not isinstance(event, AstrMessageEvent):
            raise PermissionError("official Host event required")
        if (
            tool_handle is None
            or tool_handle.module_id != module_id
            or tool_handle.descriptor.name != tool_name
        ):
            raise PermissionError("Host tool ingress unavailable")
        ingress = self._make_ingress(
            event, origin=InvocationOrigin.LLM_TOOL, tool_handle=tool_handle
        )
        if not self._validate_ingress(InvocationOrigin.LLM_TOOL, ingress):
            raise PermissionError("validated Host event required")
        unavailable = False
        try:
            outcome = await self._core.invoke_tool(
                module_id, tool_name, parameters, ingress=ingress
            )
        except HostIngressUnavailable:
            unavailable = True
        if not self._message_current(InvocationOrigin.LLM_TOOL, ingress):
            raise PermissionError("Host message unavailable")
        if unavailable:
            raise HostIngressUnavailable()
        return outcome

    def _tool_owner_current(self, handle):
        publisher = self._tool_publisher
        return (
            self._ready
            and not self._closing
            and self._core is not None
            and publisher is not None
            and any(item is handle for item in publisher.handles)
            and publisher._current(handle)
        )

    def _command_current(self, ingress):
        return (
            self._ready
            and not self._closing
            and self._core is not None
            and self._core.accepting
            and self._validate_ingress(InvocationOrigin.COMMAND, ingress)
        )

    def _make_ingress(
        self, event, *, origin=InvocationOrigin.COMMAND, tool_handle=None
    ):
        return self._message_ingress.make(event, origin=origin, tool_handle=tool_handle)

    def _validate_ingress(self, origin, ingress):
        return self._message_ingress.validate(origin, ingress)

    def _message_current(self, origin, ingress):
        return self._message_ingress.current(origin, ingress)

    _read_event_facts = staticmethod(_AstrBotMessageIngress._read_event_facts)


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
