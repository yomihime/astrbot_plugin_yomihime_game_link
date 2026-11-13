"""Closed-artifact cache and retained Core lifecycle for the thin Star.

Only validated artifacts enter the cache. Reading a cache entry always rechecks
its exact release, signature, payload and current authority before returning a
runtime lease. Cache contents are never imported directly from disk.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from ..extensions.hot_update_protocol import (
    HOST_RELEASE_SCHEMA,
    parse_plugin_descriptor,
    parse_release,
)
from ..extensions.runtime_bundle import RuntimeBundleError
from .artifact_cache import ArtifactCache as ArtifactCache
from .coordinator import get_coordinator

FEATURES = (
    "source.policies/1",
    "source.http/1",
    "storage.records/1",
    "storage.cache/1",
    "resources.cache/1",
    "output.cards/1",
    "jobs.durable/1",
    "subscriptions.events/1",
    "source.authorization/1",
    "pages.resources/1",
)


class CoreGeneration:
    """Keep exact owner, token and cleanup task through Host module purges."""

    def __init__(
        self,
        owner,
        context,
        data_root,
        bundle,
        *,
        host_is_current=None,
        close_host_pages=None,
    ):
        self.owner = owner
        self.context = context
        self.scope = (Path(data_root), context)
        self.bundle = bundle
        self.kernel = get_coordinator()
        self.receipt = None
        self.runtime = None
        self.cleanup_task = None
        self.cleanup_complete = False
        self.durable_fenced = False
        self.state = "parked"
        self._released = False
        self._host_is_current = host_is_current
        self._close_host_pages = close_host_pages
        self._installation_lost = False
        self._loop = asyncio.get_running_loop() if host_is_current is not None else None

    @property
    def installation_guard(self):
        return self.installation_current if self._host_is_current is not None else None

    def installation_current(self):
        if self._host_is_current is None:
            return True
        with self.kernel.lock:
            if not self._installation_lost and not self._host_is_current():
                self._installation_lost = True
                if self.receipt is not None:
                    # SQL workers detect revocation too. Fence admission there,
                    # and leave asynchronous cleanup with this generation's loop.
                    self._loop.call_soon_threadsafe(self._fence_lost_installation)
            return not self._installation_lost

    def require_installation(self):
        if not self.installation_current():
            raise RuntimeBundleError("core_installation_changed")

    def _fence_lost_installation(self):
        self.sync_fence()
        if self._close_host_pages is not None:
            self._close_host_pages()

    async def acquire(self):
        """Acquire lifecycle ownership only; production uses acquire_admitted."""
        if self.receipt is not None:
            return self.receipt
        expected = self.kernel.epoch_watermarks.get(
            str(Path(self.scope[0]).resolve()), 0
        )
        result = await self.kernel.acquire_core(
            self.scope, self.owner, expected, self.bundle.digest
        )
        if result["state"] == "acquired":
            self.receipt = result
            self.state = "booting"
        return result

    async def acquire_admitted(self, cache):
        """Strict production admission, independent of cache attachment order."""
        self.require_installation()
        cache.check_core_admission(self.bundle)
        result = await self.acquire()
        if result["state"] == "acquired":
            self.require_installation()
            cache.check_core_admission(self.bundle)
        return result

    def mount_admitted_core(self, cache, *, fallback):
        if self.receipt is None:
            raise RuntimeBundleError("core_owner_missing")
        self.require_installation()
        cache.check_core_admission(self.bundle, fallback=fallback)
        return self.bundle.mount_core()

    def attach(self, runtime):
        if self.receipt is None or self.runtime is not None:
            raise RuntimeError("Core ownership unavailable")
        self.runtime = runtime
        runtime._hot_generation = self
        if not self.kernel.register_gate(
            self.scope, self.receipt["owner_token"], runtime.sync_fence
        ):
            raise RuntimeError("Core ownership stale")

    def sync_fence(self):
        if self.receipt is None or self._released:
            return
        self.state = "cleanup_pending"
        self.kernel.sync_fence(
            self.scope,
            self.receipt["owner_token"],
            self._cleanup,
            self._durable_fence,
            self._quiet_proof,
        )
        task = self.kernel._record(self.scope).cleanup
        if self.cleanup_task is not task:
            self.cleanup_task = task
            task.add_done_callback(self._release_after_quiet)

    def _release_after_quiet(self, completed):
        if completed.cancelled() or completed.exception() is not None:
            return
        try:
            released = self.kernel.release_scope(
                self.scope, self.receipt["owner_token"]
            )
        except Exception:
            released = False
        # A successor may already have acquired the exact drained record.
        # A same-token failed close proof must remain parked and owned.
        with self.kernel.lock:
            current = self.kernel.scopes.get(str(Path(self.scope[0]).resolve()))
            replaced = current is None or current.token != self.receipt["owner_token"]
        if released or replaced:
            self._released = True

    async def _durable_fence(self):
        if self.runtime is not None:
            await self.runtime.fence_durable_owner()
        self.durable_fenced = True
        return True

    async def _cleanup(self):
        if self.runtime is not None:
            await self.runtime.cleanup_until_quiet()
        # Existing external I/O tickets can outlive their canceled waiters. Keep
        # code/resources until those exact operations have drained as well.
        while self.kernel._record(self.scope).tickets:
            await asyncio.sleep(0.01)
        # Releasing code/resources is the last action after actual runtime quiet.
        if self.bundle.release() != "released":
            raise RuntimeBundleError("core_bundle_still_owned")
        cache = getattr(self, "cache", None)
        if cache is not None:
            cache.close()
        self.cleanup_complete = True
        self.state = "quiet"

    def _quiet_proof(self, token, owner):
        record = self.kernel._record(self.scope)
        return {
            "owner_token": token,
            "tracked_inflight": len(record.tickets),
            "cleanup_complete": self.cleanup_complete,
            "durable_fence": self.durable_fenced,
            "resources_closed": self.cleanup_complete and owner is self.owner,
        }

    async def terminate(self, timeout=5.0):
        if self._released:
            return {"state": "quiet"}
        self.sync_fence()
        if self.receipt is None:
            return {"state": "quiet"}
        result = await self.kernel.await_quiet(
            self.scope, self.receipt["owner_token"], timeout
        )
        if self._released:
            return {"state": "quiet"}
        if result["state"] == "quiet":
            # Exact-token release cannot close a successor's lock. Watermarks
            # remain in the coordinator after the owner/Context is forgotten.
            self.kernel.release_scope(self.scope, self.receipt["owner_token"])
            self._released = True
        elif result["state"] == "rejected" and self.cleanup_complete:
            self._released = True
            return {"state": "quiet"}
        return result


def validate_core_descriptor(bundle):
    descriptor = parse_plugin_descriptor(bundle.read_resource("ygl-plugin.json"))
    if descriptor["role"] != "core" or bundle.release_doc["package_id"] != "ygl_core":
        raise RuntimeBundleError("core_identity_mismatch")
    return descriptor


def validate_core_release(raw_release, signature_bytes, bundle):
    """Verify installed Core integrity under native administrator bootstrap trust.

    The inventory-covered public key authenticates the signed artifact's bytes.
    It is deliberately not an independent trust root against malicious ZIPs.
    The native Host administrator's installation selects the Core publisher.
    """
    descriptor = validate_core_descriptor(bundle)
    if bundle.release_doc["schema"] == HOST_RELEASE_SCHEMA:
        if signature_bytes is not None or parse_release(raw_release) != dict(
            bundle.release_doc
        ):
            raise RuntimeBundleError("core_release_invalid")
        return descriptor

    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from ..extensions.hot_update_protocol import _load

    if signature_bytes is None:
        raise RuntimeBundleError("core_signature_missing")
    try:
        signature = _load(signature_bytes, 16384, depth=4, nodes=32)
        public = _load(
            bundle.read_resource("release-key.json"), 16384, depth=4, nodes=32
        )
        if (
            set(signature) != {"algorithm", "key_id", "signature_base64"}
            or set(public) != {"algorithm", "key_id", "public_key_base64"}
            or signature["algorithm"] != "ed25519"
            or public["algorithm"] != "ed25519"
            or signature["key_id"] != public["key_id"]
            or type(public["public_key_base64"]) is not str
            or type(signature["signature_base64"]) is not str
        ):
            raise ValueError("invalid Core signature envelope")
        public_bytes = base64.b64decode(public["public_key_base64"], validate=True)
        signature_raw = base64.b64decode(signature["signature_base64"], validate=True)
        if (
            len(public_bytes) != 32
            or len(signature_raw) != 64
            or hashlib.sha256(public_bytes).hexdigest() != public["key_id"]
            or base64.b64encode(public_bytes).decode() != public["public_key_base64"]
            or base64.b64encode(signature_raw).decode() != signature["signature_base64"]
        ):
            raise ValueError("invalid Core signature identity")
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(
            signature_raw, raw_release
        )
        if parse_release(raw_release) != dict(bundle.release_doc):
            raise ValueError("Core release changed")
    except Exception as exc:
        raise RuntimeBundleError("core_signature_invalid") from exc
    return descriptor


def core_migration_inventory(bundle):
    """Conservative fallback compatibility proof from a verified closed release."""
    return tuple(
        sorted(
            (row["path"], row["size"], row["sha256"])
            for row in bundle.release_doc["files"]
            if row["path"].startswith("infrastructure/sqlite/migrations/")
            and row["path"].endswith(".sql")
        )
    )
