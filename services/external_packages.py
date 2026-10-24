"""Instance-owned public Host bridge for complete captured extension packages."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.host import BRIDGE_VERSION, ExtensionHostError
from yomihime_game_link_sdk.version import MODULE_ABI_VERSION, __version__

from ..core.policy import supports_public_read_only
from ..extensions.discovery import EXTENSION_ROOT_MAX_PACKAGES, discover_packages
from ..extensions.loader import CandidateState, ExtensionCandidate
from .extension_runtime import ExtensionCleanupPending


@dataclass(frozen=True, slots=True)
class _ExternalPublicWebPage:
    target: tuple[str, str]
    manifest: object
    lease: object

    def current(self):
        return self.lease._complete and self.lease.active


class _PackageLease:
    def __init__(self, host, plugin_name, instance, candidate, digest):
        self._host = host
        self._plugin_name = plugin_name
        self._instance = instance
        self._candidate = candidate
        self._digest = digest
        self._connected = True
        self._complete = False
        self._cleanup_task = None

    @property
    def package_id(self):
        return self._candidate.package.package_id

    @property
    def generation(self):
        return self._host.generation

    @property
    def active(self):
        return (
            self._connected
            and self._host.current()
            and self._host._packages.get(self.package_id) is self
            and self._host._owner_current(self._plugin_name, self._instance)
        )


class ExternalPackageHost:
    """No ambient authority survives a Core or external Star instance change.

    Installation paths are checked against the public Star metadata. Source is
    captured by the same no-follow scanner and immutable importer as local
    packages. Stored records and durable enabled intents remain Core-owned.
    """

    bridge_version = BRIDGE_VERSION
    sdk_version = __version__
    module_abi_version = MODULE_ABI_VERSION

    def __init__(
        self,
        context,
        core,
        sources,
        *,
        generation,
        current,
        changed,
        timeout=5.0,
        registration_timeout=30.0,
        source_policies=None,
        installation_trust=None,
    ):
        self._context = context
        self._core = core
        self._sources = sources
        self.generation = generation
        self._current = current
        self._changed = changed
        self._timeout = timeout
        self._source_policies = source_policies
        self._installation_trust = installation_trust
        self._registration_timeout = registration_timeout
        self._packages = {}
        self._known_sources = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._tasks = set()
        self._requests = set()

    @asynccontextmanager
    async def _serialized(self):
        try:
            await asyncio.wait_for(self._lock.acquire(), self._timeout)
        except TimeoutError:
            raise ExtensionHostError("cleanup_pending") from None
        task = asyncio.current_task()
        self._requests.add(task)
        try:
            yield
        finally:
            self._requests.discard(task)
            self._lock.release()

    def current(self):
        return not self._closed and self._current() is True

    def page_manifest_bindings(self):
        """Known external owners stay external while a lease is being withdrawn."""
        return {
            package_id: fingerprint[0].hex()
            if (lease := self._packages.get(package_id)) is not None
            and lease.active
            and lease._complete
            else None
            for package_id, fingerprint in self._known_sources.items()
        }

    def public_web_page_bindings(self):
        """Core composition only: captured declarations from exact live leases."""
        bindings = {}
        for lease in tuple(self._packages.values()):
            if not lease._complete or not lease.active:
                continue
            manifest = lease._candidate.package.manifest
            for module in manifest.modules:
                owner = manifest.global_module_id(module.module_id)
                capabilities = {c.capability_id: c for c in module.capabilities}
                for page in module.pages:
                    capability = capabilities.get(page.capability_id)
                    if (
                        supports_public_read_only(capability)
                        and InvocationOrigin.WEB_PUBLIC in capability.effective_origins
                    ):
                        endpoint = (owner, page.route_id, page.capability_id)
                        bindings[endpoint] = _ExternalPublicWebPage(
                            (owner, page.capability_id), module, lease
                        )
        return bindings

    def page_projection(self):
        """Current immutable capture, independent of the Core build's UI list."""
        from ..extensions.static_page_projection import StaticPageProjection

        records = {}
        for key, lease in tuple(self._packages.items()):
            if lease._complete and lease.active:
                package = lease._candidate.package
                records[key] = (
                    package._provenance.manifest_sha256.hex(),
                    package.manifest,
                    b"",
                )
        projection = StaticPageProjection()
        from types import MappingProxyType

        projection.packages = MappingProxyType(records)
        return projection

    def page_assets(self, owner, page_id):
        import base64
        import hashlib

        package_id, separator, local_id = owner.partition("/")
        lease = self._packages.get(package_id)
        if not separator or lease is None or not lease._complete or not lease.active:
            raise ExtensionHostError("page_unavailable")
        package = lease._candidate.package
        module = next(
            (item for item in package.manifest.modules if item.module_id == local_id),
            None,
        )
        page = (
            next((item for item in module.pages if item.route_id == page_id), None)
            if module
            else None
        )
        if page is None:
            raise ExtensionHostError("page_unavailable")
        resources = []
        media = {
            ".js": "text/javascript",
            ".css": "text/css",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        for resource in module.resources:
            content = lease._page_resources[resource.path]
            if hashlib.sha256(content).hexdigest() != resource.sha256:
                raise ExtensionHostError("page_resource_changed")
            mime = media.get(Path(resource.path).suffix.lower())
            if mime is None:
                raise ExtensionHostError("page_resource_type_rejected")
            resources.append(
                {
                    "path": resource.path,
                    "sha256": resource.sha256,
                    "media_type": mime,
                    "base64": base64.b64encode(content).decode("ascii"),
                }
            )
        if not lease.active:
            raise ExtensionHostError("page_unavailable")
        return {
            "entry": page.entry,
            "styles": list(page.styles),
            "asset_version": package._provenance.manifest_sha256.hex(),
            "resources": resources,
        }

    def _metadata(self, name, instance):
        getter = getattr(self._context, "get_registered_star", None)
        metadata = getter(name) if callable(getter) else None
        if (
            metadata is None
            or metadata.name != name
            or metadata.activated is not True
            or metadata.star_cls is not instance
        ):
            raise ExtensionHostError("plugin_instance_unavailable")
        return metadata

    def _owner_current(self, name, instance):
        try:
            self._metadata(name, instance)
        except Exception:
            return False
        return True

    def _package_root(self, name, instance, path):
        if type(name) is not str or not name or type(path) is not str:
            raise ExtensionHostError("invalid_registration")
        metadata = self._metadata(name, instance)
        module_file = getattr(getattr(metadata, "module", None), "__file__", None)
        if type(module_file) is not str:
            raise ExtensionHostError("plugin_installation_unknown")
        try:
            installation = Path(module_file).resolve(strict=True).parent
            root = Path(path).resolve(strict=True)
            root.relative_to(installation)
            if not root.is_dir() or root == installation:
                raise ValueError()
        except (OSError, ValueError, RuntimeError):
            raise ExtensionHostError("package_root_rejected") from None
        return root

    def _discover(self, root):
        packages = tuple(
            p for p in discover_packages(root.parent) if p.package_dir == root
        )
        if len(packages) != 1 or not packages[0].valid:
            raise ExtensionHostError("package_manifest_invalid")
        package = packages[0]
        if (
            self._source_policies is not None
            and package.package_id in self._source_policies.permission_snapshots
            and not self._source_policies.permits(package)
        ):
            raise ExtensionHostError("installation_approval_required")
        for module in package.manifest.modules:
            if any(field.sensitive for field in module.config_fields) or any(
                source.credential_ref is not None for source in module.sources
            ):
                if self._source_policies is None or not self._source_policies.permits(
                    package
                ):
                    raise ExtensionHostError("external_credentials_unsupported")
        return ExtensionCandidate(package, CandidateState.DISABLED, "not_enabled")

    async def _discover_async(self, root):
        reader = asyncio.create_task(asyncio.to_thread(self._discover, root))
        try:
            return await asyncio.shield(reader)
        except asyncio.CancelledError:
            while not reader.done():
                try:
                    await asyncio.shield(reader)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            if reader.done() and not reader.cancelled():
                reader.exception()
            raise

    async def _bounded(self, coroutine, *, timeout=None):
        task = asyncio.create_task(coroutine, name="external-package-operation")
        self._tasks.add(task)
        try:
            return await self._await_task(task, timeout=timeout)
        finally:
            if task.done():
                self._tasks.discard(task)

    async def _await_task(self, task, *, timeout=None):
        try:
            done, _ = await asyncio.wait(
                {task}, timeout=self._timeout if timeout is None else timeout
            )
        except asyncio.CancelledError:
            task.cancel()
            raise
        if not done:
            task.cancel()
            raise ExtensionHostError("cleanup_pending")
        return task.result()

    async def register_package(self, *, plugin_name, plugin_instance, package_root):
        async with self._serialized():
            if not self.current():
                raise ExtensionHostError("core_unavailable")
            root = self._package_root(plugin_name, plugin_instance, package_root)
            # This task is tracked even if the caller is cancelled while a native
            # scanner holds handles; cleanup never abandons those handles.
            candidate = await self._bounded(self._discover_async(root))
            digest = await self._bounded(self._sources.inspect(candidate))
            key = candidate.package.package_id
            fingerprint = (candidate.package._provenance.manifest_sha256, digest)
            known = self._known_sources.get(key)
            if (
                known is None
                and len(self._known_sources) >= EXTENSION_ROOT_MAX_PACKAGES
            ):
                raise ExtensionHostError("package_budget_exceeded")
            if known is not None and known != fingerprint:
                raise ExtensionHostError("restart_required")
            if not self.current() or not self._owner_current(
                plugin_name, plugin_instance
            ):
                raise ExtensionHostError("plugin_instance_unavailable")
            previous = self._packages.get(key)
            if previous is not None:
                if previous._plugin_name != plugin_name:
                    raise ExtensionHostError("package_already_owned")
                if (
                    previous._instance is plugin_instance
                    and previous.active
                    and previous._complete
                ):
                    return previous
                await self._withdraw(previous)
            if self._core.extension_runtime.candidate(key) is not None:
                raise ExtensionHostError("package_already_owned")
            lease = _PackageLease(self, plugin_name, plugin_instance, candidate, digest)
            from ..extensions.page_resources import capture_page_resources

            lease._page_resources = await self._bounded(
                asyncio.to_thread(
                    capture_page_resources,
                    candidate.package._provenance,
                    candidate.package.manifest,
                )
            )
            if (
                self._installation_trust is not None
                and self._source_policies is not None
                and key in self._source_policies.permission_snapshots
            ):
                await self._bounded(
                    self._installation_trust.bind(
                        plugin_name,
                        root,
                        candidate.package,
                        current=lambda: self.current()
                        and self._owner_current(plugin_name, plugin_instance),
                    )
                )
            self._packages[key] = lease
            self._known_sources[key] = fingerprint
            self._sources.attach(candidate, digest)
            try:
                self._core.extension_runtime.attach_host_package(
                    candidate, live=lambda: lease.active
                )
                self._core.admin_operations.attach_external_package(
                    candidate.package.manifest
                )
                self._changed()

                async def prepare_and_restore():
                    await self._core._register_declared_subscription_gates(
                        {
                            f"{key}/{module.module_id}": module
                            for module in candidate.package.manifest.modules
                        }
                    )
                    await self._core.extension_runtime.restore_host_package(candidate)

                await self._bounded(
                    prepare_and_restore(), timeout=self._registration_timeout
                )
                if not lease.active:
                    raise ExtensionHostError("plugin_instance_unavailable")
                lease._complete = True
                self._changed()
                return lease
            except BaseException:
                lease._connected = False
                # Failed attach remains owned if cleanup cannot reach quiet.
                try:
                    await self._withdraw(lease)
                except BaseException:
                    pass
                raise

    async def _withdraw(self, lease):
        lease._connected = False
        previous = lease._cleanup_task
        complete = False
        if previous is not None:
            # Drain the exact previous owner before retrying a timed-out stop.
            done, _ = await asyncio.wait({previous}, timeout=self._timeout)
            if not done:
                raise ExtensionHostError("cleanup_pending")
            if not previous.cancelled():
                complete = previous.exception() is None
            self._tasks.discard(previous)
            lease._cleanup_task = None
        if not complete:
            task = asyncio.create_task(
                self._core.extension_runtime.detach_host_package(
                    lease._candidate, timeout=self._timeout
                ),
                name=f"external-package-detach:{lease.package_id}",
            )
            lease._cleanup_task = task
            self._tasks.add(task)
            await self._await_task(task)
            self._tasks.discard(task)
            lease._cleanup_task = None
        self._core.admin_operations.detach_external_package(
            lease._candidate.package.manifest
        )
        self._sources.detach(lease._candidate)
        if self._packages.get(lease.package_id) is lease:
            self._packages.pop(lease.package_id)
        self._changed()

    async def unregister_package(self, lease):
        if (
            type(lease) is not _PackageLease
            or lease._host is not self
            or self._packages.get(lease.package_id) is not lease
        ):
            return
        async with self._serialized():
            if (
                type(lease) is not _PackageLease
                or lease._host is not self
                or self._packages.get(lease.package_id) is not lease
            ):
                return
            if self._closed:
                return  # Core close owns all retained candidates and snapshots.
            await self._withdraw(lease)

    def stop_accepting(self):
        self._closed = True
        for lease in self._packages.values():
            lease._connected = False

    async def close(self):
        self.stop_accepting()
        tasks = {
            task
            for task in self._tasks | self._requests
            if not task.done() and task is not asyncio.current_task()
        }
        if tasks:
            for task in tasks:
                task.cancel()
            _, pending = await asyncio.wait(tasks, timeout=self._timeout)
            if pending:
                raise ExtensionCleanupPending(
                    "external bridge operations remain pending"
                )
        for task in self._tasks:
            if task.done() and not task.cancelled():
                task.exception()
        self._tasks.clear()

    def source_ready(self, module_id, source_id):
        package_id = module_id.split("/", 1)[0]
        lease = self._packages.get(package_id)
        if lease is None or not lease.active:
            return False
        manifest = lease._candidate.package.manifest
        return any(
            module_id == f"{package_id}/{module.module_id}"
            and any(
                source.source_id == source_id and source.credential_ref is None
                for source in module.sources
            )
            for module in manifest.modules
        )
