from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as _dataclass_field
from enum import StrEnum
from types import MappingProxyType

from .contexts import InvocationOrigin
from .storage import CollectionDescriptor, JsonValue
from .subscriptions import ScheduleDescriptor, SubscriptionDescriptor


class InvocationPolicy(StrEnum):
    COMMAND_ONLY = "command_only"
    COMMAND_AND_PUBLIC_WEB = "command_and_public_web"
    NATURAL_LANGUAGE_ALLOWED = "natural_language_allowed"


class CapabilityEffect(StrEnum):
    READ_ONLY = "read_only"
    WRITE = "write"


class PrivacyFloor(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    OWNER = "owner"


class ModuleCategory(StrEnum):
    GAME = "game"
    PLATFORM = "platform"


@dataclass(frozen=True, slots=True)
class CapabilityReference:
    """A module-qualified capability dependency target."""

    module_id: str
    capability_id: str


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """A capability declaration and its dependency requirements.

    Legacy string requirements remain module-local.  A ``CapabilityReference``
    is required for a module-qualified dependency target.
    """

    capability_id: str
    input_schema: Mapping[str, object]
    invocation_policy: InvocationPolicy
    effect: CapabilityEffect
    output_version: str = "1.8.0"
    privacy_floor: PrivacyFloor = PrivacyFloor.PUBLIC
    required_config: tuple[str, ...] = ()
    required_sources: tuple[str, ...] = ()
    required_capabilities: tuple[str | CapabilityReference, ...] = ()
    invocation_origins: tuple[InvocationOrigin, ...] | None = None

    @property
    def effective_origins(self) -> tuple[InvocationOrigin, ...]:
        if self.invocation_origins is not None:
            return self.invocation_origins
        return {
            InvocationPolicy.COMMAND_ONLY: (InvocationOrigin.COMMAND,),
            InvocationPolicy.COMMAND_AND_PUBLIC_WEB: (
                InvocationOrigin.COMMAND,
                InvocationOrigin.WEB_PUBLIC,
            ),
            InvocationPolicy.NATURAL_LANGUAGE_ALLOWED: (
                InvocationOrigin.COMMAND,
                InvocationOrigin.LLM_TOOL,
            ),
        }[self.invocation_policy]

    def __post_init__(self) -> None:
        if isinstance(self.input_schema, Mapping):
            object.__setattr__(
                self, "input_schema", MappingProxyType(dict(self.input_schema))
            )
        if self.required_config is not None:
            object.__setattr__(self, "required_config", tuple(self.required_config))
        if self.required_sources is not None:
            object.__setattr__(self, "required_sources", tuple(self.required_sources))
        if self.required_capabilities is not None:
            object.__setattr__(
                self, "required_capabilities", tuple(self.required_capabilities)
            )
        if self.invocation_origins is not None:
            object.__setattr__(
                self, "invocation_origins", tuple(self.invocation_origins)
            )


@dataclass(frozen=True, slots=True)
class CommandDescriptor:
    operation_path: str
    capability_id: str
    parameter_mapping: Mapping[str, str]
    help_text: str
    parameter_mode: str = "structured"
    raw_tail_parameter: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.parameter_mapping, Mapping):
            object.__setattr__(
                self,
                "parameter_mapping",
                MappingProxyType(dict(self.parameter_mapping)),
            )


@dataclass(frozen=True, slots=True)
class PageResource:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class PageDescriptor:
    route_id: str
    title: str
    entry: str
    order: int = 0
    access: str = "public_web"
    capability_id: str | None = None
    styles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.styles is not None:
            object.__setattr__(self, "styles", tuple(self.styles))


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    name: str
    capability_id: str
    parameter_mapping: Mapping[str, str]
    description: str

    def __post_init__(self) -> None:
        if isinstance(self.parameter_mapping, Mapping):
            object.__setattr__(
                self,
                "parameter_mapping",
                MappingProxyType(dict(self.parameter_mapping)),
            )


@dataclass(frozen=True, slots=True)
class ModuleDisplay:
    """Optional translated plain-text names; never identity or authority."""

    default_name: str
    localized_names: Mapping[str, str] = _dataclass_field(default_factory=dict)
    short_name: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.localized_names, Mapping):
            object.__setattr__(
                self, "localized_names", MappingProxyType(dict(self.localized_names))
            )


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    module_id: str
    route: str
    category: ModuleCategory
    factory_entry: str
    module_version: str
    capabilities: tuple[CapabilityDescriptor, ...]
    commands: tuple[CommandDescriptor, ...] = ()
    tools: tuple[ToolDescriptor, ...] = ()
    schedules: tuple[ScheduleDescriptor, ...] = ()
    subscriptions: tuple[SubscriptionDescriptor, ...] = ()
    config_fields: tuple["ConfigField", ...] = ()
    sources: tuple["SourceDeclaration", ...] = ()
    collections: tuple[CollectionDescriptor, ...] = ()
    pages: tuple[PageDescriptor, ...] = ()
    resources: tuple[PageResource, ...] = ()
    display: ModuleDisplay | None = None

    def __post_init__(self) -> None:
        if self.capabilities is not None:
            object.__setattr__(self, "capabilities", tuple(self.capabilities))
        if self.commands is not None:
            object.__setattr__(self, "commands", tuple(self.commands))
        if self.tools is not None:
            object.__setattr__(self, "tools", tuple(self.tools))
        if self.schedules is not None:
            object.__setattr__(self, "schedules", tuple(self.schedules))
        if self.subscriptions is not None:
            object.__setattr__(self, "subscriptions", tuple(self.subscriptions))
        if self.config_fields is not None:
            object.__setattr__(self, "config_fields", tuple(self.config_fields))
        if self.sources is not None:
            object.__setattr__(self, "sources", tuple(self.sources))
        if self.collections is not None:
            object.__setattr__(self, "collections", tuple(self.collections))
        if self.pages is not None:
            object.__setattr__(self, "pages", tuple(self.pages))
        if self.resources is not None:
            object.__setattr__(self, "resources", tuple(self.resources))


@dataclass(frozen=True, slots=True)
class PackageManifest:
    package_id: str
    package_version: str
    contract_version: str
    modules: tuple[ModuleManifest, ...]
    author: str
    license: str
    source: str

    def global_module_id(self, module_id: str) -> str:
        """Return the package-qualified ID for one declared local module."""
        if module_id not in {module.module_id for module in self.modules}:
            raise ValueError("module_id is not declared by this package")
        return f"{self.package_id}/{module_id}"

    def __post_init__(self) -> None:
        if self.modules is not None:
            object.__setattr__(self, "modules", tuple(self.modules))


class ConfigUpdateMode(StrEnum):
    KEEP = "keep"
    REPLACE = "replace"
    CLEAR = "clear"


@dataclass(frozen=True, slots=True)
class ConfigField:
    """A declared configuration field; secret values are kept in SecretStore."""

    name: str
    sensitive: bool = False
    required: bool = False
    default: JsonValue | None = None
    description: str = ""
    value_schema: Mapping[str, object] | None = None
    group: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.value_schema, Mapping):
            object.__setattr__(
                self, "value_schema", MappingProxyType(dict(self.value_schema))
            )


@dataclass(frozen=True, slots=True)
class SourceDeclaration:
    """A bounded source target; credentials and paths are never supplied by modules."""

    source_id: str
    host: str
    credential_ref: str | None = None
    timeout_seconds: float = 10.0
    requests_per_minute: int = 60
