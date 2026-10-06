"""Compatibility re-exports for the canonical Yomihime SDK API."""

from yomihime_sdk.api.manifests import (
    _IDENTIFIER as _IDENTIFIER,
)
from yomihime_sdk.api.manifests import (
    _VERSION as _VERSION,
)
from yomihime_sdk.api.manifests import (
    COMPATIBLE_CONTRACT_VERSIONS as COMPATIBLE_CONTRACT_VERSIONS,
)
from yomihime_sdk.api.manifests import (
    CONTRACT_VERSION as CONTRACT_VERSION,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_DESCRIPTOR_FIELDS as EXTENSION_DESCRIPTOR_FIELDS,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_FACTORY_ABI_VERSION as EXTENSION_FACTORY_ABI_VERSION,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_MANIFEST_ABI as EXTENSION_MANIFEST_ABI,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_MANIFEST_FILENAME as EXTENSION_MANIFEST_FILENAME,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_MANIFEST_MAX_ARRAY_ITEMS as EXTENSION_MANIFEST_MAX_ARRAY_ITEMS,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_MANIFEST_MAX_BYTES as EXTENSION_MANIFEST_MAX_BYTES,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_MANIFEST_MAX_STRING_LENGTH as EXTENSION_MANIFEST_MAX_STRING_LENGTH,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_MANIFEST_ROOT_POLICY as EXTENSION_MANIFEST_ROOT_POLICY,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_MANIFEST_SCHEMA_VERSION as EXTENSION_MANIFEST_SCHEMA_VERSION,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_MODULE_MAX_DECLARATIONS as EXTENSION_MODULE_MAX_DECLARATIONS,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_PACKAGE_FIELDS as EXTENSION_PACKAGE_FIELDS,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_PACKAGE_MAX_MODULES as EXTENSION_PACKAGE_MAX_MODULES,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_SCHEMA_MAX_DEPTH as EXTENSION_SCHEMA_MAX_DEPTH,
)
from yomihime_sdk.api.manifests import (
    EXTENSION_SCHEMA_MAX_NODES as EXTENSION_SCHEMA_MAX_NODES,
)
from yomihime_sdk.api.manifests import (
    CapabilityDescriptor as CapabilityDescriptor,
)
from yomihime_sdk.api.manifests import (
    CapabilityEffect as CapabilityEffect,
)
from yomihime_sdk.api.manifests import (
    CapabilityReference as CapabilityReference,
)
from yomihime_sdk.api.manifests import (
    CollectionDeclaration as CollectionDeclaration,
)
from yomihime_sdk.api.manifests import (
    CollectionDescriptor as CollectionDescriptor,
)
from yomihime_sdk.api.manifests import (
    CommandDescriptor as CommandDescriptor,
)
from yomihime_sdk.api.manifests import (
    ConfigField as ConfigField,
)
from yomihime_sdk.api.manifests import (
    ConfigFieldDescriptor as ConfigFieldDescriptor,
)
from yomihime_sdk.api.manifests import (
    ConfigUpdateMode as ConfigUpdateMode,
)
from yomihime_sdk.api.manifests import (
    ExtensionManifestABI as ExtensionManifestABI,
)
from yomihime_sdk.api.manifests import (
    InvocationPolicy as InvocationPolicy,
)
from yomihime_sdk.api.manifests import (
    JsonValue as JsonValue,
)
from yomihime_sdk.api.manifests import (
    Mapping as Mapping,
)
from yomihime_sdk.api.manifests import (
    MappingProxyType as MappingProxyType,
)
from yomihime_sdk.api.manifests import (
    ModuleCategory as ModuleCategory,
)
from yomihime_sdk.api.manifests import (
    ModuleManifest as ModuleManifest,
)
from yomihime_sdk.api.manifests import (
    PackageManifest as PackageManifest,
)
from yomihime_sdk.api.manifests import PageDescriptor as PageDescriptor
from yomihime_sdk.api.manifests import PageResource as PageResource
from yomihime_sdk.api.manifests import (
    PrivacyFloor as PrivacyFloor,
)
from yomihime_sdk.api.manifests import (
    ScheduleDescriptor as ScheduleDescriptor,
)
from yomihime_sdk.api.manifests import (
    SourceDeclaration as SourceDeclaration,
)
from yomihime_sdk.api.manifests import (
    SourceDescriptor as SourceDescriptor,
)
from yomihime_sdk.api.manifests import (
    SourceSpec as SourceSpec,
)
from yomihime_sdk.api.manifests import (
    StrEnum as StrEnum,
)
from yomihime_sdk.api.manifests import (
    SubscriptionDescriptor as SubscriptionDescriptor,
)
from yomihime_sdk.api.manifests import (
    ToolDescriptor as ToolDescriptor,
)
from yomihime_sdk.api.manifests import (
    _capability_dependency_tuple as _capability_dependency_tuple,
)
from yomihime_sdk.api.manifests import (
    _descriptor_tuple as _descriptor_tuple,
)
from yomihime_sdk.api.manifests import (
    _entry_point as _entry_point,
)
from yomihime_sdk.api.manifests import (
    _field_name as _field_name,
)
from yomihime_sdk.api.manifests import (
    _freeze_value as _freeze_value,
)
from yomihime_sdk.api.manifests import (
    _frozen_mapping as _frozen_mapping,
)
from yomihime_sdk.api.manifests import (
    _global_module_identifier as _global_module_identifier,
)
from yomihime_sdk.api.manifests import (
    _identifier as _identifier,
)
from yomihime_sdk.api.manifests import (
    _identifier_tuple as _identifier_tuple,
)
from yomihime_sdk.api.manifests import (
    _operation_path as _operation_path,
)
from yomihime_sdk.api.manifests import (
    _route as _route,
)
from yomihime_sdk.api.manifests import (
    _safe_token as _safe_token,
)
from yomihime_sdk.api.manifests import (
    _text as _text,
)
from yomihime_sdk.api.manifests import (
    _unique as _unique,
)
from yomihime_sdk.api.manifests import (
    _validate_parameter_mapping as _validate_parameter_mapping,
)
from yomihime_sdk.api.manifests import (
    _version as _version,
)
from yomihime_sdk.api.manifests import (
    annotations as annotations,
)
from yomihime_sdk.api.manifests import (
    dataclass as dataclass,
)
from yomihime_sdk.api.manifests import (
    freeze_input_schema as freeze_input_schema,
)
from yomihime_sdk.api.manifests import (
    is_compatible_contract_version as is_compatible_contract_version,
)
from yomihime_sdk.api.manifests import (
    math as math,
)
from yomihime_sdk.api.manifests import (
    re as re,
)
from yomihime_sdk.api.manifests import (
    unicodedata as unicodedata,
)
