"""Public outer-plugin bridge contracts; module business never needs this API.

Implementations and leases belong to one live Core instance.  No process-global
registry or SDK bootstrap is provided here. Import this module only after the
Core plugin has loaded the canonical, compatible SDK.
"""

from typing import Protocol, runtime_checkable

BRIDGE_VERSION = 1


class ExtensionHostError(RuntimeError):
    """A bounded diagnostic for a rejected outer-plugin operation."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@runtime_checkable
class PackageLease(Protocol):
    """Opaque instance ownership. Callers cannot mint or transfer authority."""

    @property
    def package_id(self) -> str: ...

    @property
    def generation(self) -> int: ...

    @property
    def active(self) -> bool: ...


@runtime_checkable
class ExtensionHost(Protocol):
    @property
    def bridge_version(self) -> int: ...

    @property
    def sdk_version(self) -> str: ...

    @property
    def module_abi_version(self) -> str: ...

    @property
    def generation(self) -> int: ...

    async def register_package(
        self, *, plugin_name: str, plugin_instance: object, package_root: str
    ) -> PackageLease: ...

    async def unregister_package(self, lease: PackageLease) -> None: ...
