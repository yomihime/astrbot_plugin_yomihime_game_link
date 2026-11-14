"""Instance-owned public Host bridge for complete captured extension packages."""

from __future__ import annotations

import asyncio
import math
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..core.policy import supports_public_read_only
from ..extensions.discovery import EXTENSION_ROOT_MAX_PACKAGES, discover_packages
from ..extensions.loader import CandidateState, ExtensionCandidate
from ..yomihime_game_link_sdk.contexts import InvocationOrigin
from ..yomihime_game_link_sdk.host import BRIDGE_VERSION, ExtensionHostError
from ..yomihime_game_link_sdk.version import MODULE_ABI_VERSION, __version__
from .extension_runtime import ExtensionCleanupPending


def _manifest_business_root(manifest_path):
    # Manifest inventory paths are POSIX even on a Windows native Host.
    return PurePosixPath(manifest_path).parent.as_posix()


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
                        current=lambda: (
                            self.current()
                            and self._owner_current(plugin_name, plugin_instance)
                        ),
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


class _GuardedHandler:
    """Bind every callback and its child tasks to one immutable package lease."""

    def __init__(self, delegate, gate):
        self._delegate, self._gate = delegate, gate

    def __getattr__(self, name):
        import inspect

        from .runtime_effect_gate import invocation_guard

        value = getattr(self._delegate, name)
        if not callable(value):
            return value
        if inspect.iscoroutinefunction(value):

            async def invoke(*args, **kwargs):
                with invocation_guard(self._gate):
                    self._gate.check()
                    return await value(*args, **kwargs)

            return invoke

        def invoke(*args, **kwargs):
            with invocation_guard(self._gate):
                self._gate.check()
                return value(*args, **kwargs)

        return invoke


class _GuardedInstance:
    def __init__(self, delegate, gate):
        from dataclasses import fields, replace

        from ..yomihime_game_link_sdk.services import ModuleHandlers

        handlers = delegate.handlers()
        if type(handlers) is not ModuleHandlers:
            raise TypeError("cross_generation_object")
        self._delegate, self._gate = delegate, gate
        self._handlers = replace(
            handlers,
            **{
                field.name: {
                    name: _GuardedHandler(handler, gate)
                    for name, handler in getattr(handlers, field.name).items()
                }
                for field in fields(handlers)
            },
        )

    def handlers(self):
        return self._handlers

    async def start(self):
        from .runtime_effect_gate import invocation_guard

        with invocation_guard(self._gate):
            self._gate.check()
            await self._delegate.start()

    async def stop(self):
        from .runtime_effect_gate import invocation_guard

        # Cleanup may release owned resources, but a fenced stop gets no
        # general Core authority for business writes/network/messages.
        with invocation_guard(self._gate):
            await self._delegate.stop()

    async def check_health(self):
        from .runtime_effect_gate import invocation_guard

        with invocation_guard(self._gate):
            self._gate.check()
            return await self._delegate.check_health()


@dataclass(slots=True)
class _HotPackage:
    installation: object
    bundle: object
    receipt: object
    candidate: object
    gate: object
    epoch: int
    cache_key: str
    policy: object
    instances: dict
    lifetimes: dict
    transition_id: str
    cleanup_task: object = None
    policy_current: bool = True
    published: bool = False
    envelope: object = None
    fallback: bool = False
    installed_version: object = None
    factory_tasks: list = field(default_factory=list)
    prepare_cleanup_task: object = None

    @property
    def _complete(self):
        return self.published

    @property
    def active(self):
        try:
            self.gate.check()
            return self.published
        except Exception:
            return False


@dataclass(frozen=True, slots=True)
class _HostInstallReceipt:
    package_id: str
    artifact_digest: str
    grant_revision: int = 0


class IndependentPackageRuntime:
    """Core-owned trust, prepare, drain and closed-publication composition."""

    def __init__(self, runtime, generation, cache, *, prepare_timeout=30.0):
        # One budget for all factories in a package, never per module.
        if (
            isinstance(prepare_timeout, bool)
            or not isinstance(prepare_timeout, (int, float))
            or not math.isfinite(prepare_timeout)
            or prepare_timeout <= 0
        ):
            raise ValueError("prepare_timeout must be finite and positive")
        self._prepare_timeout = float(prepare_timeout)
        self._replacement_tasks = set()
        self._cancelled_replacements = set()
        from ..bootstrap.loader import FEATURES
        from ..infrastructure.sqlite.repositories_extension_policy import (
            ExtensionPolicyRepository,
        )
        from .extension_discovery import ExtensionDiscovery
        from .extension_transition import ExtensionControlEndpoint
        from .publisher_trust import PublisherTrust

        self.runtime, self.generation, self.cache = runtime, generation, cache
        self.core = runtime._core
        self.discovery = ExtensionDiscovery(runtime._context)
        self.repository = ExtensionPolicyRepository(
            self.core.database, before_mutation=self.before_mutation
        )
        self.trust = PublisherTrust(self.repository)
        self.packages, self.operations, self._locks = {}, {}, {}
        self._closed = False
        self._reconcile_task = None
        self._prepared = {}
        self._retired_prepared = {}
        self._failed_candidate_cleanup = False
        self._page_digests = {}
        self._transition_lock = asyncio.Lock()
        self.endpoint = ExtensionControlEndpoint(
            self.discovery,
            core_epoch=generation.receipt["core_epoch"],
            activate=self.replace,
            withdraw=self.withdraw_instance,
            fence=self.fence_instance,
            features=FEATURES,
            installation_guard=generation.installation_guard,
            retirement_lock=generation.kernel.lock,
        )

    def read_module_resource(self, module_id, manifest, path):
        """Read a declared resource from the current, gated package bundle."""
        import hashlib

        package_id, _, local_id = module_id.partition("/")
        package = self.packages.get(package_id)
        if self._closed or package is None or not package.active:
            raise ExtensionHostError("package_unavailable")
        declared = next(
            (
                row
                for row in package.candidate.package.manifest.modules
                if row.module_id == local_id
            ),
            None,
        )
        if declared is None or declared != manifest:
            raise ExtensionHostError("module_unavailable")
        resource = next((row for row in declared.resources if row.path == path), None)
        if resource is None:
            raise ExtensionHostError("page_resource_unavailable")
        content = package.bundle.read_resource(
            (
                package.candidate.package.package_dir.relative_to(
                    package.bundle.resource_root
                )
                / path
            ).as_posix()
        )
        if (
            type(content) is not bytes
            or hashlib.sha256(content).hexdigest() != resource.sha256
            or self.packages.get(package_id) is not package
            or self._closed
            or not package.active
        ):
            raise ExtensionHostError("page_resource_changed")
        return content

    def source_ready(self, module_id, source_id):
        """Check current package wiring without reading secrets or probing HTTP."""
        package_id, _, local_id = module_id.partition("/")
        package = self.packages.get(package_id)
        if self._closed or package is None or not package.active:
            return False
        module = next(
            (
                row
                for row in package.candidate.package.manifest.modules
                if row.module_id == local_id
            ),
            None,
        )
        source = (
            next((row for row in module.sources if row.source_id == source_id), None)
            if module is not None
            else None
        )
        if source is None or source.credential_ref is not None:
            return False
        expected = tuple(
            policy
            for policy in package.policy.credential_policies
            if policy.module_id == module_id and policy.source_id == source_id
        )
        published = tuple(
            policy
            for policy in self.core.module_services._source_credential_policies
            if policy.module_id == module_id and policy.source_id == source_id
        )
        return expected == published and all(
            policy.resource_host == source.host for policy in expected
        )

    def page_manifest_bindings(self):
        return {
            key: digest
            if (package := self.packages.get(key)) is not None and package.active
            else None
            for key, digest in self._page_digests.items()
        }

    def page_projection(self):
        from types import MappingProxyType

        from ..extensions.static_page_projection import StaticPageProjection

        projection = StaticPageProjection()
        projection.packages = MappingProxyType(
            {
                key: (self._page_digests[key], package.candidate.package.manifest, b"")
                for key, package in tuple(self.packages.items())
                if package.active
            }
        )
        return projection

    def public_web_page_bindings(self):
        bindings = {}
        for package in tuple(self.packages.values()):
            if not package.active:
                continue
            manifest = package.candidate.package.manifest
            for module in manifest.modules:
                owner = manifest.global_module_id(module.module_id)
                capabilities = {row.capability_id: row for row in module.capabilities}
                for page in module.pages:
                    capability = capabilities.get(page.capability_id)
                    if (
                        supports_public_read_only(capability)
                        and InvocationOrigin.WEB_PUBLIC in capability.effective_origins
                    ):
                        bindings[(owner, page.route_id, page.capability_id)] = (
                            _ExternalPublicWebPage(
                                (owner, page.capability_id), module, package
                            )
                        )
        return bindings

    def page_assets(self, owner, page_id):
        import base64
        import hashlib

        package_id, _, local_id = owner.partition("/")
        package = self.packages.get(package_id)
        if package is None or not package.active:
            raise ExtensionHostError("page_unavailable")
        module = next(
            (
                row
                for row in package.candidate.package.manifest.modules
                if row.module_id == local_id
            ),
            None,
        )
        page = (
            next((row for row in module.pages if row.route_id == page_id), None)
            if module
            else None
        )
        if page is None:
            raise ExtensionHostError("page_unavailable")
        media = {
            ".js": "text/javascript",
            ".css": "text/css",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        resources = []
        for resource in module.resources:
            content = package.bundle.read_resource(
                (
                    package.candidate.package.package_dir.relative_to(
                        package.bundle.resource_root
                    )
                    / resource.path
                ).as_posix()
            )
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
        package.gate.check()
        return {
            "entry": page.entry,
            "styles": list(page.styles),
            "asset_version": self._page_digests[package_id],
            "resources": resources,
        }

    def before_mutation(self, kind, key):
        for package in tuple(self.packages.values()):
            receipt = package.receipt
            if isinstance(receipt, _HostInstallReceipt):
                continue
            if (
                (kind == "package_grant" and receipt.package_id == key)
                or (kind == "publisher" and receipt.publisher_key_id == key)
                or (kind == "artifact_approval" and receipt.artifact_digest == key)
            ):
                package.policy_current = False
                self.fence_instance(package.installation.instance)
                self.endpoint.retire_instance(package.installation.instance)

    def fence_instance(self, instance):
        for package in tuple(self.packages.values()):
            if package.installation.instance is instance:
                package.gate.close()
                self.core.jobs.fence_modules(
                    f"{package.receipt.package_id}/{module.module_id}"
                    for module in package.candidate.package.manifest.modules
                )
                for module in package.candidate.package.manifest.modules:
                    owner = f"{package.receipt.package_id}/{module.module_id}"
                    identity = self.core.lifecycle.current_identity(owner)
                    if identity is not None:
                        self.core.lifecycle.admission.close(identity, "package_fenced")
                for module in package.candidate.package.manifest.modules:
                    self.runtime._source_authorizations.invalidate(
                        owner_id=f"{package.receipt.package_id}/{module.module_id}"
                    )

    def guard_for_owner(self, owner):
        """Resolve the exact currently published package generation for an owner."""
        for package in tuple(self.packages.values()):
            if any(
                owner == f"{package.receipt.package_id}/{module.module_id}"
                for module in package.candidate.package.manifest.modules
            ):
                package.gate.check()
                return package.gate
        package_id = owner.partition("/")[0]
        candidate = self.core.extension_runtime.candidate(package_id)
        if (
            candidate is not None
            and candidate.package is not None
            and candidate.package.package_dir.is_relative_to(
                self.generation.bundle.resource_root
            )
            and owner in self.core.registry.snapshot().modules
        ):
            return self.runtime._hot_gate
        raise RuntimeError("package_owner_unavailable")

    def _current(self, package):
        if (
            self._closed
            or not package.policy_current
            or not self.runtime.ready
            or self.discovery.is_tombstoned(package.installation.instance)
        ):
            return False
        metadata = package.installation.metadata
        if (
            metadata.activated is not True
            or metadata.star_cls is not package.installation.instance
            or (
                isinstance(package.receipt, _HostInstallReceipt)
                and not package.installation.metadata_current()
            )
        ):
            return False
        # Public registry snapshot is synchronous and never performs SQL/IO.
        if not any(
            item is metadata for item in tuple(self.runtime._context.get_all_stars())
        ):
            return False
        return self.packages.get(package.receipt.package_id) is package

    def _assert_installed_effect(self, unit, package):
        from .runtime_effect_gate import EffectRejected

        def check():
            if not self._current(package):
                raise EffectRejected()

        @contextmanager
        def commit_guard():
            with self.generation.kernel.lock:
                check()
                package.gate._check_locked()
                yield

        check()
        unit.add_commit_guard(commit_guard)

    async def _prepare(self, installation, *, cache_key=None):
        if self._failed_candidate_cleanup:
            raise ExtensionHostError("cleanup_pending")
        import hashlib
        import secrets
        from uuid import uuid4

        from ..bootstrap.loader import FEATURES
        from ..core.lifecycle import _ServiceLifetime
        from ..extensions.discovery import DiscoveredPackage
        from ..extensions.disk_manifest import parse_manifest
        from ..extensions.hot_update_protocol import (
            HOST_RELEASE_SCHEMA,
            _load,
            b05_manifest_bytes,
            negotiate_manifest,
            parse_package_manifest,
            parse_plugin_descriptor,
        )
        from ..infrastructure.sqlite.repositories_extension_policy import (
            assert_execution_policy,
        )
        from .extension_permissions import permission_snapshot
        from .external_source_policies import (
            ExternalSourcePolicies,
            parse_package_source_policy,
        )
        from .runtime_effect_gate import RuntimeEffectGate, invocation_guard

        checked = {}

        async def validate(raw, signature_bytes, bundle):
            descriptor_bytes = bundle.read_resource("ygl-plugin.json")
            descriptor = parse_plugin_descriptor(descriptor_bytes)
            if (
                (
                    cache_key is None
                    and (
                        hashlib.sha256(descriptor_bytes).hexdigest()
                        != installation.descriptor_digest
                        or descriptor != installation.descriptor
                    )
                )
                or descriptor["role"] != "dlc"
                or descriptor["package_id"] != installation.package_id
                or descriptor["package_id"] != bundle.release_doc["package_id"]
            ):
                raise ExtensionHostError("artifact_tampered")
            manifest_path = descriptor["manifest"]
            host_install = bundle.release_doc["schema"] == HOST_RELEASE_SCHEMA
            if cache_key is not None and host_install:
                raise ExtensionHostError("artifact_tampered")
            envelope = parse_package_manifest(
                bundle.read_resource(manifest_path), host_install=host_install
            )
            negotiate_manifest(envelope, 0, FEATURES)
            raw_b05 = b05_manifest_bytes(envelope)
            manifest = parse_manifest(raw_b05)
            if (
                manifest.package_id != descriptor["package_id"]
                or manifest.package_version != bundle.release_doc["release"]
            ):
                raise ExtensionHostError("artifact_tampered")
            if "source_policy" in envelope:
                policy, projection = parse_package_source_policy(
                    bundle.read_resource(envelope["source_policy"]),
                    b05_manifest_bytes=raw_b05,
                    source_policy_path=envelope["source_policy"],
                    required_features=envelope["required_features"],
                    release_inventory=bundle.release_doc["files"],
                )
            else:
                policy, projection = (
                    ExternalSourcePolicies(),
                    permission_snapshot(manifest),
                )
            signature = (
                _load(signature_bytes, 16384, depth=4, nodes=16)
                if signature_bytes is not None
                else None
            )
            metadata = installation.metadata
            if cache_key is not None and cache_key not in {
                self.cache.current.get(installation.package_id),
                self.cache.last_good.get(installation.package_id),
            }:
                raise ExtensionHostError("cache_pin_changed")
            if host_install:
                if not self.discovery.current(installation):
                    raise ExtensionHostError("stale_instance")
                if (
                    signature is not None
                    or bundle.release_doc["plugin_id"] != installation.plugin_id
                    or type(installation.version) is not str
                    or installation.version.removeprefix("v")
                    != bundle.release_doc["release"]
                ):
                    raise ExtensionHostError("artifact_tampered")
                self.cache.check_package_identity(
                    installation.package_id, installation.plugin_id
                )
                receipt = _HostInstallReceipt(
                    installation.package_id, hashlib.sha256(raw).hexdigest()
                )
            else:
                arguments = dict(
                    host_plugin_id={"author": metadata.author, "name": metadata.name},
                    permission_projection=projection,
                )
                if cache_key is None:
                    receipt = await self.trust.evaluate(raw, signature, **arguments)
                else:
                    receipt = await self.trust.evaluate_cached(
                        raw, signature, pinned_digest=cache_key, **arguments
                    )
            await self.repository.check_package_owner(
                installation.package_id,
                installation.plugin_id,
                host_install=host_install,
            )
            checked.update(
                envelope=envelope,
                manifest=manifest,
                policy=policy,
                business_root=_manifest_business_root(manifest_path),
            )
            return receipt

        fallback = cache_key is not None
        if fallback:
            bundle, receipt, cache_key = await self.cache.load(
                cache_key, kind="dlc", validate=validate
            )
        else:
            bundle, receipt, cache_key = await self.cache.capture(
                installation.root, kind="dlc", validate=validate
            )
        package = None
        try:
            if not self.discovery.current(installation):
                raise ExtensionHostError("stale_instance")
            old = self.packages.get(receipt.package_id)
            if (
                isinstance(receipt, _HostInstallReceipt)
                and old is not None
                and old.bundle.release_doc["plugin_id"] != installation.plugin_id
            ):
                raise ExtensionHostError("artifact_tampered")
            if (
                not isinstance(receipt, _HostInstallReceipt)
                and old is not None
                and bundle.files.get("requirements.txt")
                != old.bundle.files.get("requirements.txt")
            ):
                raise ExtensionHostError("environment_change_required")
            bundle.mount_package(
                self.generation.bundle, receipt.package_id, checked["business_root"]
            )
            candidate = ExtensionCandidate(
                DiscoveredPackage(
                    receipt.package_id,
                    bundle.resource_root / checked["business_root"],
                    checked["manifest"],
                ),
                CandidateState.DISABLED,
                "not_enabled",
            )
            persisted_epoch = (
                await self.core.database.executor.run_internal_transaction(
                    lambda unit: [
                        tuple(row)
                        for row in unit.connection.execute(
                            "SELECT package_epoch FROM runtime_owners WHERE scope=?",
                            ("package:" + receipt.package_id,),
                        ).fetchall()
                    ]
                )
            )
            epoch = (
                max(
                    old.epoch if old else 0,
                    persisted_epoch[0][0] if persisted_epoch else 0,
                )
                + 1
            )
            token = secrets.token_hex(32)
            package = _HotPackage(
                installation,
                bundle,
                receipt,
                candidate,
                None,
                epoch,
                cache_key,
                checked["policy"],
                {},
                {},
                str(uuid4()),
            )
            gate = RuntimeEffectGate(
                self.generation.kernel,
                self.generation.scope,
                self.generation.receipt["owner_token"],
                self.generation.receipt["core_epoch"],
                package_id=receipt.package_id,
                package_token=token,
                package_epoch=epoch,
                grant_revision=receipt.grant_revision,
                initially_open=False,
                installation_guard=self.generation.installation_guard,
                authority_check=lambda: self._current(package),
                policy_assert=(
                    lambda unit: self._assert_installed_effect(unit, package)
                )
                if isinstance(receipt, _HostInstallReceipt)
                else (lambda unit: assert_execution_policy(unit, receipt)),
            )
            package.gate = gate
            self._prepared[id(installation.instance)] = package
            deadline = asyncio.get_running_loop().time() + self._prepare_timeout
            # Candidate-scoped credential policies are supplied only while
            # constructing its service objects; existing services keep theirs.
            services_factory = self.core.module_services
            original_policies = services_factory._source_credential_policies
            for module in checked["manifest"].modules:
                owner = f"{receipt.package_id}/{module.module_id}"
                lifetime = _ServiceLifetime(
                    (receipt.package_id, owner, package.transition_id)
                )
                services_factory._source_credential_policies = (
                    tuple(
                        p
                        for p in original_policies
                        if not p.module_id.startswith(receipt.package_id + "/")
                    )
                    + package.policy.credential_policies
                )
                try:
                    services = services_factory.for_candidate(
                        owner, module, service_lifetime=lifetime
                    )
                finally:
                    services_factory._source_credential_policies = original_policies
                package.lifetimes[owner] = lifetime

                async def create_owned(
                    entry=module.factory_entry, services=services, owner=owner
                ):
                    with invocation_guard(gate):
                        factory = bundle.resolve(entry)
                        instance = await factory.create(services)
                        # No await between return and ownership adoption. Even a
                        # cancellation-resistant late result belongs to cleanup.
                        package.instances[owner] = instance
                        guarded = _GuardedInstance(instance, gate)
                        package.instances[owner] = guarded
                        return guarded

                task = asyncio.create_task(create_owned(), name="ygl-package-factory")
                package.factory_tasks.append(task)
                try:
                    done, _ = await asyncio.wait(
                        (task,),
                        timeout=max(0, deadline - asyncio.get_running_loop().time()),
                    )
                except asyncio.CancelledError:
                    if not task.done():
                        task.cancel()
                    raise
                if not done:
                    task.cancel()
                    self.endpoint.mark_instance_error(
                        installation.instance, "prepare_timeout"
                    )
                    raise ExtensionHostError("prepare_timeout")
                task.result()
                if gate.prepare_violation:
                    from .runtime_effect_gate import EffectRejected

                    raise EffectRejected("prepare_side_effect_forbidden")
            package.envelope = checked["envelope"]
            package.fallback = fallback
            package.installed_version = (
                installation.version if fallback else bundle.release_doc["release"]
            )
            self._prepared[id(installation.instance)] = package
            return package
        except BaseException:
            if package is not None:
                violation = package.gate is not None and package.gate.prepare_violation
                # Keep the candidate slot until *actual* factory/instance
                # cleanup completes. A shutdown cancel arriving after a
                # preparation timeout must not unwind the transition lock.
                while True:
                    try:
                        await self._discard_prepared(package)
                        break
                    except asyncio.CancelledError:
                        continue
                if violation:
                    from .runtime_effect_gate import EffectRejected

                    raise EffectRejected("prepare_side_effect_forbidden") from None
            else:
                bundle.release()
                self.cache.discard(cache_key)
            raise

    async def _discard_prepared(self, package):
        task = package.prepare_cleanup_task
        if task is None:
            if package.gate is not None:
                package.gate.close()
            task = asyncio.create_task(
                self._discard_prepared_owned(package), name="ygl-candidate-cleanup"
            )
            package.prepare_cleanup_task = task
            self._retired_prepared[package.transition_id] = task
        try:
            await asyncio.shield(task)
        except BaseException:
            if task.done() and (task.cancelled() or task.exception() is not None):
                # A completed-but-failed stop is not quiet. Keep its exact
                # future, candidate and cache hold; block execution candidates
                # without retrying a partially failed instance.stop.
                self._failed_candidate_cleanup = True
                self.endpoint.mark_instance_error(
                    package.installation.instance, "cleanup_pending"
                )
                raise ExtensionHostError("cleanup_pending") from None
            raise

    async def _discard_prepared_owned(self, package):
        from .runtime_effect_gate import invocation_guard

        # These exact tasks may still adopt an instance after cancellation.
        # Cleanup itself is independently owned and is never cancelled by an
        # observer or by repeated shutdown.
        if package.factory_tasks:
            await asyncio.gather(*package.factory_tasks, return_exceptions=True)
        for instance in package.instances.values():
            stop = getattr(instance, "stop", None)
            if callable(stop):
                with invocation_guard(package.gate):
                    await stop()
        for lifetime in package.lifetimes.values():
            lifetime.revoke()
        if package.bundle.release() != "released":
            raise ExtensionHostError("cleanup_pending")
        self.cache.discard(package.cache_key)
        self._prepared.pop(id(package.installation.instance), None)
        self._retired_prepared.pop(package.transition_id, None)

    def _cancel_replacement(self, task):
        if not task.done() and task not in self._cancelled_replacements:
            self._cancelled_replacements.add(task)
            task.cancel()

    def _replacement_done(self, task):
        self._replacement_tasks.discard(task)
        self._cancelled_replacements.discard(task)
        if not task.cancelled():
            task.exception()  # Observe detached failures; awaiters still see them.

    async def replace(self, installation):
        if self._closed or not self.generation.installation_current():
            raise ExtensionHostError("core_unavailable")
        if self._failed_candidate_cleanup:
            raise ExtensionHostError("cleanup_pending")
        task = asyncio.create_task(
            self._replace_owned(installation), name="ygl-package-replacement"
        )
        self._replacement_tasks.add(task)
        task.add_done_callback(self._replacement_done)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self._cancel_replacement(task)
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))
            raise

    async def _replace_owned(self, installation):
        try:
            return await self._replace(installation)
        except BaseException as original:
            package = self._prepared.get(id(installation.instance))
            if package is None:
                raise
            was_published = self.packages.get(installation.package_id) is package
            if was_published:
                self.fence_instance(installation.instance)
                # Exact owned cleanup is shielded and retained by the package;
                # recovery cannot begin until its real resources are quiet.
                await self.withdraw_instance(installation.instance)
                self._prepared.pop(id(installation.instance), None)
            else:
                await self._discard_prepared(package)
            if (
                isinstance(original, asyncio.CancelledError)
                or not was_published
                or isinstance(package.receipt, _HostInstallReceipt)
                or self._closed
                or not self.discovery.current(installation)
            ):
                raise
            for pin in dict.fromkeys(
                (
                    self.cache.current.get(installation.package_id),
                    self.cache.last_good.get(installation.package_id),
                )
            ):
                if pin is None or pin == package.cache_key:
                    continue
                try:
                    return await self._replace(installation, cache_key=pin)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failed = self._prepared.get(id(installation.instance))
                    if failed is not None:
                        if self.packages.get(installation.package_id) is failed:
                            self.fence_instance(installation.instance)
                            await self.withdraw_instance(installation.instance)
                            self._prepared.pop(id(installation.instance), None)
                        else:
                            await self._discard_prepared(failed)
            raise original

    async def _replace(self, installation, *, cache_key=None):
        # One 8 MiB execution candidate across all package identities; active
        # sibling modules keep serving while this transition drains its owner.
        async with self._transition_lock:
            if self._failed_candidate_cleanup:
                raise ExtensionHostError("cleanup_pending")
            return await self._replace_serial(installation, cache_key=cache_key)

    async def _replace_serial(self, installation, *, cache_key=None):
        from ..extensions.hot_update_protocol import check_data_compatibility
        from ..infrastructure.sqlite.runtime_ownership import RuntimeOwnership
        from .extension_runtime import _CandidatePackage

        lock = self._locks.setdefault(installation.package_id, asyncio.Lock())
        async with lock:
            old = self.packages.get(installation.package_id)
            if (
                cache_key is None
                and old is not None
                and old.installation.instance is installation.instance
                and old.installation.descriptor_digest == installation.descriptor_digest
                and old.active
                and self.discovery.current(installation)
            ):
                return self._status(old)
            package = await self._prepare(installation, cache_key=cache_key)
            active_bytes = sum(
                current.bundle.total_bytes
                for key, current in self.packages.items()
                if key != installation.package_id
            )
            if active_bytes + package.bundle.total_bytes > 32 * 1024 * 1024:
                raise ExtensionHostError("active_package_budget_exceeded")
            executor = self.core.database.executor
            scope = "package:" + installation.package_id

            def prepare(unit):
                prior = unit.connection.execute(
                    "SELECT * FROM runtime_owners WHERE scope=?", (scope,)
                ).fetchone()
                stored = unit.connection.execute(
                    "SELECT * FROM runtime_data_contracts WHERE package_id=?",
                    (installation.package_id,),
                ).fetchone()
                if stored is not None:
                    check_data_compatibility(
                        package.envelope,
                        stored["schema_version"],
                        stored["writes_version"],
                        stored["writes_version"],
                    )
                if prior is not None:
                    package.epoch = max(package.epoch, prior["package_epoch"] + 1)
                    # gate identity is immutable; construct before prepare using
                    # persisted epoch instead of mutating it after issuance.
                    if package.epoch != package.gate.identity.package_epoch:
                        raise ExtensionHostError("stale_epoch")
                RuntimeOwnership.prepare(
                    unit,
                    package.transition_id,
                    scope,
                    package.bundle.digest,
                    boot_id=self.generation.kernel.boot_id,
                    from_boot_id=prior["boot_id"] if prior else None,
                    from_digest=prior["digest"] if prior else None,
                    from_epoch=prior["package_epoch"] if prior else 0,
                    to_epoch=package.epoch,
                )
                return dict(prior) if prior else None

            prior = await executor.run_internal_transaction(prepare)
            if old is not None:
                self.fence_instance(old.installation.instance)
            if prior is not None:
                await executor.run_internal_transaction(
                    lambda unit: RuntimeOwnership.fence(
                        unit,
                        scope,
                        prior["token"],
                        expected_revision=prior["revision"],
                        transition_id=package.transition_id,
                    )
                )
                revision = prior["revision"] + 1
            else:
                await executor.run_internal_transaction(
                    lambda unit: RuntimeOwnership.phase(
                        unit, package.transition_id, "PREPARED", "OLD_FENCED"
                    )
                )
                revision = 0
            if old is not None:
                try:
                    await asyncio.wait_for(self._cleanup_package(old), 0.1)
                except asyncio.TimeoutError:
                    await executor.run_internal_transaction(
                        lambda unit: RuntimeOwnership.phase(
                            unit,
                            package.transition_id,
                            "OLD_FENCED",
                            "WAITING_CLEANUP",
                            error_code="cleanup_pending",
                        )
                    )
                    self.endpoint.mark_instance_state(
                        installation.instance, "cleanup_pending"
                    )
                    await self._cleanup_package(old)
                    await executor.run_internal_transaction(
                        lambda unit: RuntimeOwnership.resume_waiting(
                            unit, package.transition_id, "OLD_FENCED"
                        )
                    )
            if not self.discovery.current(installation) or self._closed:
                await self._discard_prepared(package)
                raise ExtensionHostError("stale_instance")
            await executor.run_internal_transaction(
                lambda unit: RuntimeOwnership.phase(
                    unit, package.transition_id, "OLD_FENCED", "OLD_QUIET"
                )
            )

            from .runtime_effect_gate import EffectRejected

            def current_owner(*, revalidate=True):
                if (
                    self._closed
                    or not self.generation.installation_current()
                    or self.discovery.is_tombstoned(installation.instance)
                    or installation.metadata.activated is not True
                    or installation.metadata.star_cls is not installation.instance
                    or (
                        isinstance(package.receipt, _HostInstallReceipt)
                        and not installation.metadata_current()
                    )
                    or (revalidate and not self.discovery.current(installation))
                ):
                    raise ExtensionHostError("stale_instance")
                record = self.generation.kernel._record(self.generation.scope)
                if (
                    record.fenced
                    or record.token != self.generation.receipt["owner_token"]
                ):
                    raise EffectRejected()

            @contextmanager
            def commit_fence():
                current_owner()
                with self.generation.kernel.lock:
                    current_owner(revalidate=False)
                    yield

            def commit(unit):
                from ..infrastructure.sqlite.repositories_extension_policy import (
                    advance_release_head,
                    assert_execution_policy,
                    check_package_owner,
                )

                current_owner()
                unit.add_commit_guard(commit_fence)
                release_commit = None
                receipt = package.receipt
                host_install = isinstance(receipt, _HostInstallReceipt)
                check_package_owner(
                    unit,
                    installation.package_id,
                    installation.plugin_id,
                    host_install=host_install,
                    bind=True,
                )
                if not host_install:
                    assert_execution_policy(unit, receipt)
                    if not package.fallback:
                        row = unit.connection.execute(
                            "SELECT * FROM extension_release_heads WHERE package_id=? AND publisher_key_id=? AND channel=?",
                            (
                                receipt.package_id,
                                receipt.publisher_key_id,
                                receipt.channel,
                            ),
                        ).fetchone()

                        def release_commit(transaction):
                            advance_release_head(
                                transaction,
                                package_id=receipt.package_id,
                                publisher_key_id=receipt.publisher_key_id,
                                channel=receipt.channel,
                                sequence=receipt.release_sequence,
                                artifact_digest=receipt.artifact_digest,
                                expected_sequence=row["release_sequence"]
                                if row
                                else None,
                                expected_digest=row["artifact_digest"] if row else None,
                            )

                closed = RuntimeOwnership.commit_closed(
                    unit,
                    package.transition_id,
                    expected_token=prior["token"] if prior else None,
                    expected_revision=revision,
                    core_epoch=self.generation.receipt["core_epoch"],
                    package_epoch=package.epoch,
                    grant_revision=package.receipt.grant_revision,
                    boot_id=self.generation.kernel.boot_id,
                    new_token=package.gate.identity.package_token,
                    release_commit=release_commit,
                )
                RuntimeOwnership.check_data_contract(
                    unit,
                    package.receipt.package_id,
                    package.envelope["data"]["schema"],
                    package.envelope["data"]["writes"],
                    package.envelope["data"]["readable"],
                )
                return closed

            closed = await executor.run_internal_transaction(commit)
            import hashlib

            from ..extensions.hot_update_protocol import b05_manifest_bytes

            self._page_digests[installation.package_id] = hashlib.sha256(
                b05_manifest_bytes(package.envelope)
            ).hexdigest()
            self.packages[installation.package_id] = package
            try:
                ext = self.core.extension_runtime
                ext.attach_host_package(
                    package.candidate,
                    live=lambda: (
                        self._current(package) and self.discovery.current(installation)
                    ),
                )
                self._publish_policy(package)
                self.core.admin_operations.attach_external_package(
                    package.candidate.package.manifest
                )
                handlers = {
                    module.module_id: package.instances[
                        f"{installation.package_id}/{module.module_id}"
                    ].handlers()
                    for module in package.candidate.package.manifest.modules
                }
                base = self.core.registry.snapshot().revision
                registered = self.core.registry.register_package(
                    package.candidate.package.manifest, handlers
                )
                built = _CandidatePackage(
                    package.candidate,
                    package.candidate.package.manifest,
                    package.transition_id,
                    package.bundle,
                    {},
                    base,
                    registered.revision,
                    complete=True,
                )
                ext._built[installation.package_id] = built
                for module in package.candidate.package.manifest.modules:
                    owner = f"{installation.package_id}/{module.module_id}"
                    instance = package.instances[owner]
                    self.core.lifecycle.adopt_candidate(
                        installation.package_id,
                        module,
                        package.transition_id,
                        instance,
                        service_lifetime=package.lifetimes[owner],
                    )
                    self.core.lifecycle.install_dormant(
                        installation.package_id,
                        owner,
                        package.transition_id,
                        instance,
                        instance.handlers(),
                    )
                    built.installation_operation_ids[owner] = package.transition_id
                package.published = True
                await executor.run_internal_transaction(
                    lambda unit: RuntimeOwnership.phase(
                        unit, package.transition_id, "COMMITTED_CLOSED", "PUBLISHED"
                    )
                )
                if (
                    not self.discovery.current(installation)
                    or self._closed
                    or not self.generation.installation_current()
                ):
                    raise ExtensionHostError("stale_instance")

                def open_owner(unit):
                    current_owner()
                    unit.add_commit_guard(commit_fence)
                    return RuntimeOwnership.open_active(
                        unit,
                        package.transition_id,
                        package.gate.identity.package_token,
                        closed["revision"],
                        boot_id=self.generation.kernel.boot_id,
                    )

                await executor.run_internal_transaction(open_owner)
                package.gate.open()
                from .runtime_effect_gate import invocation_guard

                with invocation_guard(package.gate):
                    await self.core._register_declared_subscription_gates(
                        {
                            f"{installation.package_id}/{m.module_id}": m
                            for m in package.candidate.package.manifest.modules
                        }
                    )
                    await ext.restore_host_package(package.candidate)
                package.gate.check()
                self.core.jobs.resume_modules(
                    f"{package.receipt.package_id}/{module.module_id}"
                    for module in package.candidate.package.manifest.modules
                )
                if not package.fallback:
                    self.cache.publish(installation.package_id, package.cache_key)
                self._prepared.pop(id(installation.instance), None)
                self.runtime._external_packages_changed()
                return self._status(package)
            except BaseException:
                self.fence_instance(installation.instance)
                raise

    def _publish_policy(self, package):
        from types import MappingProxyType

        prefix = package.receipt.package_id + "/"
        factory = self.core.module_services
        factory._source_credential_policies = (
            tuple(
                p
                for p in factory._source_credential_policies
                if not p.module_id.startswith(prefix)
            )
            + package.policy.credential_policies
        )
        admin = self.core.admin_operations
        admin._managed_source_credentials = (
            tuple(
                p
                for p in admin._managed_source_credentials
                if not p.module_id.startswith(prefix)
            )
            + package.policy.managed_credentials
        )
        policy = self.runtime._external_source_policies
        policy.authorizations = MappingProxyType(
            {
                **{
                    owner: rows
                    for owner, rows in policy.authorizations.items()
                    if not owner.startswith(prefix)
                },
                **package.policy.authorizations,
            }
        )
        self.runtime._source_authorizations._policy = policy
        from types import MappingProxyType

        from ..core.invocation_budget import InvocationBudgetPolicy

        previous = self.core._budget_policy
        timeouts = {
            key: value
            for key, value in previous.capability_timeouts.items()
            if not key[0].startswith(prefix)
        }
        timeouts.update(package.policy.capability_timeouts)
        self.core._budget_policy = InvocationBudgetPolicy(
            previous.command_timeout, MappingProxyType(timeouts)
        )
        self.core.gateway._budget_policy = self.core._budget_policy

    async def _cleanup_package(self, package):
        if package.cleanup_task is None:
            package.cleanup_task = asyncio.create_task(
                self._cleanup_loop(package), name="ygl-package-cleanup"
            )
        await asyncio.shield(package.cleanup_task)

    async def _cleanup_loop(self, package):
        while True:
            try:
                await self.core.extension_runtime.detach_host_package(
                    package.candidate, owner_drain=self.core.jobs.drain_modules
                )
                break
            except ExtensionCleanupPending:
                await asyncio.sleep(0.1)
        self.core.admin_operations.detach_external_package(
            package.candidate.package.manifest
        )
        prefix = package.receipt.package_id + "/"
        for owner in tuple(self.runtime._source_authorizations._managers):
            if owner.startswith(prefix):
                manager = self.runtime._source_authorizations._managers.pop(owner)
                await manager.close()
        if self.packages.get(package.receipt.package_id) is package:
            self.packages.pop(package.receipt.package_id)
        self._revoke_policy(package)
        self.cache.discard(package.cache_key)
        self.runtime._external_packages_changed()

    def _revoke_policy(self, package):
        from types import MappingProxyType

        from ..core.invocation_budget import InvocationBudgetPolicy

        prefix = package.receipt.package_id + "/"
        factory = self.core.module_services
        factory._source_credential_policies = tuple(
            p
            for p in factory._source_credential_policies
            if not p.module_id.startswith(prefix)
        )
        admin = self.core.admin_operations
        admin._managed_source_credentials = tuple(
            p
            for p in admin._managed_source_credentials
            if not p.module_id.startswith(prefix)
        )
        policy = self.runtime._external_source_policies
        policy.authorizations = MappingProxyType(
            {
                owner: rows
                for owner, rows in policy.authorizations.items()
                if not owner.startswith(prefix)
            }
        )
        previous = self.core._budget_policy
        self.core._budget_policy = InvocationBudgetPolicy(
            previous.command_timeout,
            {
                key: value
                for key, value in previous.capability_timeouts.items()
                if not key[0].startswith(prefix)
            },
        )
        self.core.gateway._budget_policy = self.core._budget_policy

    async def withdraw_instance(self, instance):
        self.endpoint.retire_instance(instance, "cleanup_pending")
        prepared = self._prepared.get(id(instance))
        if (
            prepared is not None
            and prepared.installation.instance is instance
            and prepared.prepare_cleanup_task is not None
        ):
            await self._discard_prepared(prepared)
        for package in tuple(self.packages.values()):
            if package.installation.instance is instance:
                self.fence_instance(instance)
                await self.core.database.executor.run_internal_transaction(
                    lambda unit: (
                        unit.connection.execute(
                            "UPDATE runtime_owners SET accepting=0,revision=revision+1 WHERE scope=? AND token=?",
                            (
                                "package:" + package.receipt.package_id,
                                package.gate.identity.package_token,
                            ),
                        ).rowcount
                    )
                )
                await self._cleanup_package(package)
                if self._prepared.get(id(instance)) is package:
                    self._prepared.pop(id(instance))
        self.endpoint.retire_instance(instance)

    def _status(self, package):
        return {
            "state": "active",
            "package_epoch": package.epoch,
            "runtime_state": "fallback" if package.fallback else "active",
            "installed_version": package.installed_version,
            "running_version": package.bundle.release_doc["release"],
            "grant_revision": package.receipt.grant_revision,
        }

    def schedule_reconcile(self):
        if self._closed:
            return
        if self._reconcile_task is None or self._reconcile_task.done():
            self._reconcile_task = asyncio.create_task(
                self.reconcile(), name="ygl-discovery-reconcile"
            )

    async def reconcile(self):
        import hashlib
        from uuid import UUID

        self.discovery.prune_tombstones()
        snapshot = self.discovery.scan()
        for package in tuple(self.packages.values()):
            if not any(
                item.instance is package.installation.instance for item in snapshot
            ):
                self.fence_instance(package.installation.instance)
                await self.withdraw_instance(package.installation.instance)
        for installation in snapshot:
            if installation.descriptor["role"] != "dlc":
                continue
            request_id = str(
                UUID(
                    hashlib.sha256(
                        (
                            str(id(installation.instance))
                            + installation.descriptor_digest
                        ).encode()
                    ).hexdigest()[:32]
                )
            )
            await self.endpoint.request(
                installation.instance,
                {
                    "protocol": "ygl.control/1",
                    "op": "connect",
                    "request_id": request_id,
                    "descriptor_digest": installation.descriptor_digest,
                },
            )

    def stop_accepting(self):
        self._closed = True
        self.endpoint.stop_accepting()
        for task in tuple(self._replacement_tasks):
            self._cancel_replacement(task)

    async def close(self):
        self.stop_accepting()
        await self.endpoint.close()
        if self._replacement_tasks:
            await asyncio.shield(
                asyncio.gather(*self._replacement_tasks, return_exceptions=True)
            )
        for package in tuple(self.packages.values()):
            await self.withdraw_instance(package.installation.instance)
        for package in tuple(self._prepared.values()):
            await self._discard_prepared(package)
        if self._retired_prepared:
            await asyncio.shield(asyncio.gather(*self._retired_prepared.values()))
