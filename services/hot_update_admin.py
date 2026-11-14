"""Authenticated, inert inspection and exact-plan approval for independent DLCs.

Frontend contract: request(action, data, grant=Core-issued grant). Every data object
has core_epoch; unknown fields are rejected. Read actions use LIST_MODULES, writes
SET_ENABLED. Actions and additional fields:
 status: none; inspect: package_id; retry: package_id;
 package-approve/artifact-approve: package_id,plan_id,expected_revision;
 package-revoke: package_id,expected_revision;
 artifact-revoke: package_id,artifact_digest,expected_revision;
 publisher-enroll: public_key_base64,channels,expected_revision;
 publisher-revoke: key_id,expected_revision.

Replies are {ok,core_epoch,...}; errors contain only stable error.code.
Status also returns publishers[{key_id,public_key_base64,channels,revision,revoked}]
independently of installed DLCs, and core{installed_version,running_version,
installed_digest,running_digest,phase,ready}. Missing Core fixture facts are null;
all digests use exact raw release bytes, never payload digests. Runtime resource
inspection failure preserves the actual active/fallback state plus its reason code. Inspection
returns installed/running version and digest independently, state, reason_code,
operation_id, grant/publisher/artifact revisions, permission_digest, permissions_delta
and opaque plan_id. Integrity-verified but not yet authorized captures may enter
cache as inert candidates; inspection discards its hold for controlled GC, and
subsequent admission always rechecks current trust. Delta entries contain declaration paths and requested/approved
values, never saved credential values. Filesystem roots, owner tokens, signatures,
sessions and private keys are never returned. Publisher scope enrollment defaults
to no capability ceiling; package permissions always come from verified captures.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from inspect import isawaitable, iscoroutine
from time import monotonic

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..core.contracts.administration import AdminAuthorizationDenied, AdminOperation
from ..extensions.disk_manifest import ManifestError, parse_manifest
from ..extensions.hot_update_protocol import (
    HOST_RELEASE_SCHEMA,
    ProtocolError,
    _load,
    b05_manifest_bytes,
    negotiate_manifest,
    parse_package_manifest,
    parse_plugin_descriptor,
)
from ..extensions.runtime_bundle import RuntimeBundleError
from ..infrastructure.sqlite.repositories_extension_policy import (
    PolicyError,
    _digest,
    _identity,
    _package_id,
    _revision,
)
from .admin_authorization import _grant_effect
from .extension_permissions import (
    permission_delta,
    permission_digest,
    permission_snapshot,
)
from .external_source_policies import parse_package_source_policy


class HotUpdateAdminError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _Plan:
    package_id: str
    installation: object
    artifact_digest: str
    permission_json: str
    publisher_key_id: str
    plugin_json: str
    descriptor_digest: str
    signature_digest: str | None
    epoch: int
    signature_state: str
    grant_revision: int
    artifact_revision: int
    publisher_revision: int
    reason: str | None
    public: dict
    expires: float

    def binding(self):
        return (
            self.package_id,
            self.artifact_digest,
            self.permission_json,
            self.publisher_key_id,
            self.plugin_json,
            self.descriptor_digest,
            self.signature_digest,
            self.epoch,
            self.signature_state,
            self.grant_revision,
            self.artifact_revision,
            self.publisher_revision,
            self.reason,
        )


class HotUpdateAdmin:
    def __init__(self, manager, authorization, *, management_source=None):
        """management_source is an exact Core-owned issuer or synchronous provider.

        Resource coverage alone never promotes a local issuer to global trust
        authority. An unbound source denies source-backed grants; independently
        authenticated native credential grants retain their existing global scope.
        """
        self.manager, self.authorization = manager, authorization
        self.management_source = management_source
        self.repository = manager.repository
        self._plans = OrderedDict()

    def _epoch(self):
        return self.manager.generation.receipt["core_epoch"]

    def _guard(self, epoch, grant, operation):
        if type(epoch) is not int or epoch != self._epoch():
            raise HotUpdateAdminError("stale_generation")
        manager = self.manager
        if (
            manager._closed
            or not manager.runtime.ready
            or manager.runtime._core is not manager.core
            or getattr(manager.runtime, "_hot_generation", None)
            is not manager.generation
            or getattr(manager.runtime, "_independent_packages", None) is not manager
        ):
            raise HotUpdateAdminError("stale_generation")
        effect = _grant_effect(grant)
        if effect.service is not self.authorization or grant.operation is not operation:
            raise AdminAuthorizationDenied
        effect.check_lifetime()
        for target, fields in manager.core.admin_operations.module_resources().items():
            effect.check_resource(target, fields)
        if effect.source is not None:
            bound = (
                self.management_source()
                if callable(self.management_source)
                else self.management_source
            )
            if isawaitable(bound):
                if iscoroutine(bound):
                    bound.close()
                raise AdminAuthorizationDenied
            if effect.source is not bound:
                raise AdminAuthorizationDenied
            effect.source.check(effect.context, operation, effect.resources)
        return effect

    async def _check(self, epoch, grant, operation):
        self._guard(epoch, grant, operation)
        await self.authorization.validate_generation(grant, operation=operation)
        return self._guard(epoch, grant, operation)

    def _plan_current(self, plan):
        if (
            not self.manager.discovery.current(plan.installation)
            or _identity(
                {
                    "author": plan.installation.metadata.author,
                    "name": plan.installation.metadata.name,
                }
            )
            != plan.plugin_json
        ):
            raise HotUpdateAdminError("plan_changed")
        raw, signature = self.manager.cache._metadata(plan.installation.root)
        signature_digest = (
            hashlib.sha256(signature).hexdigest() if signature is not None else None
        )
        if (
            hashlib.sha256(raw).hexdigest() != plan.artifact_digest
            or signature_digest != plan.signature_digest
        ):
            raise HotUpdateAdminError("plan_changed")

    def _worker_check(self, epoch, grant, operation, plan=None):
        def check(unit):
            effect = self._guard(epoch, grant, operation)
            effect.check(unit, operation)
            if plan is not None:
                self._plan_current(plan)

            @contextmanager
            def fence():
                self._guard(epoch, grant, operation)
                if plan is not None:
                    self._plan_current(plan)
                yield

            unit.add_commit_guard(fence)

        return check

    def _core_status(self):
        """Read only real thin-owner facts; absent lower-level fixture facts stay null.

        Digests are exact raw-release SHA256/cache keys, never payload digests.
        Phase is the real thin-owner state (e.g. active/fallback/cleanup_pending),
        not a success inferred from installed/running version equality.
        """
        owner = getattr(self.manager.generation, "owner", None)
        result = {
            field: getattr(owner, "_" + field, None)
            for field in (
                "installed_version",
                "running_version",
                "installed_digest",
                "running_digest",
            )
        }
        for field in ("installed_digest", "running_digest"):
            if result[field] is not None:
                _digest(result[field])
        for field in ("installed_version", "running_version"):
            if result[field] is not None and type(result[field]) is not str:
                raise ValueError("invalid Core version")
        phase = getattr(owner, "_state", None)
        if phase is None:
            phase = getattr(self.manager.generation, "state", None)
        if phase not in {
            None,
            "parked",
            "starting",
            "booting",
            "initializing",
            "active",
            "ready",
            "fallback",
            "cleanup_pending",
            "quiet",
            "failed",
            "rejected",
            "closed",
            "stopped",
        }:
            phase = None
        result.update(phase=phase, ready=self.manager.runtime.ready is True)
        return result

    async def _publishers(self, epoch, grant):
        check = self._worker_check(epoch, grant, AdminOperation.LIST_MODULES)

        def read(unit):
            check(unit)
            result = []
            for row in unit.execute(
                "SELECT key_id,public_key,scope_policy,revision,revoked FROM extension_publisher_trust ORDER BY key_id"
            ).fetchall():
                public = row["public_key"]
                if (
                    type(public) is not bytes
                    or len(public) != 32
                    or hashlib.sha256(public).hexdigest() != row["key_id"]
                ):
                    raise ValueError("invalid stored publisher")
                scope = json.loads(row["scope_policy"])
                channels = scope["channels"]
                if (
                    type(channels) is not list
                    or not channels
                    or any(
                        type(channel) is not str or channel not in {"stable", "preview"}
                        for channel in channels
                    )
                ):
                    raise ValueError("invalid publisher channels")
                _revision(row["revision"])
                if (
                    row["revision"] == 0
                    or type(row["revoked"]) is not int
                    or row["revoked"] not in (0, 1)
                ):
                    raise ValueError("invalid stored publisher state")
                result.append(
                    {
                        "key_id": row["key_id"],
                        "public_key_base64": base64.b64encode(public).decode(),
                        "channels": sorted(channels),
                        "revision": row["revision"],
                        "revoked": bool(row["revoked"]),
                    }
                )
            return result

        return await self.repository.database.executor.run_read(read)

    @staticmethod
    def _inspection_reason(error):
        if isinstance(error, (HotUpdateAdminError, PolicyError, RuntimeBundleError)):
            return error.code
        if isinstance(error, ManifestError):
            return "manifest_invalid"
        if isinstance(error, (ProtocolError, OSError)):
            return "artifact_invalid"
        return "inspection_failed"

    def _unavailable_status(self, package_id, error):
        running = self.manager.packages.get(package_id)
        if running and self.manager._current(running):
            state = (
                "fallback" if getattr(running, "fallback", False) is True else "active"
            )
        elif running and getattr(running, "cleanup_task", None) is not None:
            state = "cleanup_pending"
        else:
            state = "rejected"
        return {
            "package_id": package_id,
            "installed_version": getattr(running, "installed_version", None),
            "installed_digest": None,
            "running_version": running.bundle.release_doc["release"]
            if running
            else None,
            "running_digest": running.receipt.artifact_digest if running else None,
            "state": state,
            "reason_code": self._inspection_reason(error),
        }

    @staticmethod
    def _delta(candidate, approved):
        def flat(value):
            return {
                f"{owner}/{kind}/{name}": item
                for owner, kinds in value.items()
                for kind, entries in kinds.items()
                for name, item in entries.items()
            }

        current, old = flat(candidate), flat(approved)
        paths = permission_delta(candidate, approved)
        return {
            kind: [
                {
                    "path": path,
                    **({"approved": old[path]} if path in old else {}),
                    **({"requested": current[path]} if path in current else {}),
                }
                for path in paths[kind]
            ]
            for kind in ("added", "removed", "changed")
        }

    async def _signature(self, raw, encoded):
        if encoded is None:
            return "", "unsigned", 0
        signature = _load(encoded, 16384, depth=4, nodes=16)
        if (
            set(signature) != {"algorithm", "key_id", "signature_base64"}
            or signature["algorithm"] != "ed25519"
        ):
            raise HotUpdateAdminError("artifact_tampered")
        key_id = _digest(signature["key_id"])
        try:
            value = signature["signature_base64"]
            if type(value) is not str or len(value) > 128:
                raise ValueError
            sig = base64.b64decode(value, validate=True)
            if len(sig) != 64:
                raise ValueError
        except (TypeError, ValueError):
            raise HotUpdateAdminError("artifact_tampered") from None
        publisher = await self.repository.publisher(key_id)
        if publisher is None:
            return key_id, "publisher_untrusted", 0
        try:
            public = publisher["public_key"]
            if hashlib.sha256(public).hexdigest() != key_id:
                raise ValueError
            Ed25519PublicKey.from_public_bytes(public).verify(sig, raw)
        except (ValueError, TypeError, InvalidSignature):
            raise HotUpdateAdminError("artifact_tampered") from None
        return (
            key_id,
            "publisher_untrusted" if publisher["revoked"] else "verified",
            publisher["revision"],
        )

    async def _inspect(self, package_id, epoch):
        from ..bootstrap.loader import FEATURES

        installations = [
            item
            for item in self.manager.discovery.scan()
            if item.package_id == package_id and item.descriptor["role"] == "dlc"
        ]
        if len(installations) != 1:
            raise HotUpdateAdminError("package_unavailable")
        installation = installations[0]
        checked = {}

        async def validate(raw, signature, bundle):
            descriptor_raw = bundle.read_resource("ygl-plugin.json")
            descriptor = parse_plugin_descriptor(descriptor_raw)
            if (
                hashlib.sha256(descriptor_raw).hexdigest()
                != installation.descriptor_digest
                or descriptor != installation.descriptor
                or descriptor["package_id"] != bundle.release_doc["package_id"]
            ):
                raise HotUpdateAdminError("artifact_tampered")
            envelope = parse_package_manifest(
                bundle.read_resource(descriptor["manifest"]),
                host_install=bundle.release_doc["schema"] == HOST_RELEASE_SCHEMA,
            )
            negotiate_manifest(envelope, 0, FEATURES)
            b05 = b05_manifest_bytes(envelope)
            manifest = parse_manifest(b05)
            plugin_id = {
                "author": installation.metadata.author,
                "name": installation.metadata.name,
            }
            if (
                manifest.package_id != package_id
                or manifest.package_version != bundle.release_doc["release"]
                or bundle.release_doc["plugin_id"] != plugin_id
            ):
                raise HotUpdateAdminError("artifact_tampered")
            if "source_policy" in envelope:
                _, projection = parse_package_source_policy(
                    bundle.read_resource(envelope["source_policy"]),
                    b05_manifest_bytes=b05,
                    source_policy_path=envelope["source_policy"],
                    required_features=envelope["required_features"],
                    release_inventory=bundle.release_doc["files"],
                )
            else:
                projection = permission_snapshot(manifest)
            reason = None
            if bundle.release_doc["schema"] == HOST_RELEASE_SCHEMA:
                if (
                    signature is not None
                    or type(installation.version) is not str
                    or installation.version.removeprefix("v")
                    != manifest.package_version
                ):
                    raise HotUpdateAdminError("artifact_tampered")
                self.manager.cache.check_package_identity(package_id, plugin_id)
                await self.repository.check_package_owner(
                    package_id, plugin_id, host_install=True
                )
                key_id, state, publisher_revision = "", "host_install", 0
            else:
                await self.repository.check_package_owner(
                    package_id, plugin_id, host_install=False
                )
                key_id, state, publisher_revision = await self._signature(
                    raw, signature
                )
                try:
                    await self.manager.trust.evaluate(
                        raw,
                        None if signature is None else _load(signature, 16384),
                        host_plugin_id=plugin_id,
                        permission_projection=projection,
                    )
                except PolicyError as exc:
                    reason = exc.code
            checked.update(
                raw=raw,
                signature_digest=hashlib.sha256(signature).hexdigest()
                if signature is not None
                else None,
                projection=projection,
                plugin_id=plugin_id,
                key_id=key_id,
                signature_state=state,
                publisher_revision=publisher_revision,
                reason=reason,
                version=manifest.package_version,
            )
            return True  # Inert inspection is never admission or factory execution.

        bundle, _, cache_key = await self.manager.cache.capture(
            installation.root, kind="dlc", validate=validate
        )
        try:
            if not self.manager.discovery.current(installation):
                raise HotUpdateAdminError("plan_changed")
            digest = hashlib.sha256(checked["raw"]).hexdigest()
            grant = approval = None
            if checked["signature_state"] != "host_install":
                grant = await self.repository.grant(package_id)
                approval = await self.repository.approval(digest)
            grant_revision = grant["revision"] if grant else 0
            artifact_revision = approval["revision"] if approval else 0
            projection = checked["projection"]
            running = self.manager.packages.get(package_id)
            operation = next(
                (
                    op
                    for op in reversed(
                        tuple(self.manager.endpoint._operations.values())
                    )
                    if op.instance is installation.instance
                ),
                None,
            )
            state = (
                operation.reply.get("state", "installed") if operation else "installed"
            )
            reason = checked["reason"] or (
                operation.reply.get("error", {}).get("code") if operation else None
            )
            if running is not None and self.manager._current(running):
                state = (
                    "fallback"
                    if getattr(running, "fallback", False) is True
                    else "active"
                )
            elif reason and state != "cleanup_pending":
                state = (
                    "approval_required" if reason == "approval_required" else "rejected"
                )
            public = {
                "package_id": package_id,
                "plugin_id": checked["plugin_id"],
                "installed_version": checked["version"],
                "installed_digest": digest,
                "running_version": running.bundle.release_doc["release"]
                if running
                else None,
                "running_digest": running.receipt.artifact_digest if running else None,
                "state": state,
                "reason_code": reason,
                "operation_id": operation.operation_id if operation else None,
                "grant_revision": grant_revision,
                "artifact_revision": artifact_revision,
                "publisher_revision": checked["publisher_revision"],
                "publisher_key_id": checked["key_id"],
                "signature_state": checked["signature_state"],
                "permission_digest": permission_digest(projection),
                "permissions_delta": self._delta(
                    projection, json.loads(grant["permission_json"]) if grant else {}
                ),
            }
            return _Plan(
                package_id,
                installation,
                digest,
                json.dumps(projection, sort_keys=True, separators=(",", ":")),
                checked["key_id"],
                _identity(checked["plugin_id"]),
                installation.descriptor_digest,
                checked["signature_digest"],
                epoch,
                checked["signature_state"],
                grant_revision,
                artifact_revision,
                checked["publisher_revision"],
                reason,
                public,
                monotonic() + 120,
            )
        finally:
            bundle.release()
            discard = getattr(self.manager.cache, "discard", None)
            if discard is not None:
                discard(cache_key)
            else:
                self.manager.cache.candidates.discard(cache_key)

    def _remember(self, plan):
        for key, old in tuple(self._plans.items()):
            if old.expires < monotonic():
                del self._plans[key]
        while len(self._plans) >= 128:
            self._plans.popitem(last=False)
        key = secrets.token_hex(24)
        self._plans[key] = plan
        return {**plan.public, "plan_id": key}

    async def request(self, action, data, *, grant):
        try:
            return await self._request(action, data, grant)
        except AdminAuthorizationDenied:
            return {"ok": False, "error": {"code": "admin_authorization_denied"}}
        except (HotUpdateAdminError, PolicyError) as exc:
            return {"ok": False, "error": {"code": exc.code}}
        except (ValueError, TypeError, KeyError, RecursionError):
            return {"ok": False, "error": {"code": "invalid_request"}}
        except Exception:
            return {"ok": False, "error": {"code": "hot_update_failed"}}

    async def _request(self, action, data, grant):
        fields = {
            "status": set(),
            "inspect": {"package_id"},
            "retry": {"package_id"},
            "package-approve": {"package_id", "plan_id", "expected_revision"},
            "artifact-approve": {"package_id", "plan_id", "expected_revision"},
            "package-revoke": {"package_id", "expected_revision"},
            "artifact-revoke": {"package_id", "artifact_digest", "expected_revision"},
            "publisher-enroll": {"public_key_base64", "channels", "expected_revision"},
            "publisher-revoke": {"key_id", "expected_revision"},
        }
        if (
            type(action) is not str
            or action not in fields
            or type(data) is not dict
            or set(data) != fields[action] | {"core_epoch"}
        ):
            raise ValueError("invalid request")
        operation = (
            AdminOperation.LIST_MODULES
            if action in {"status", "inspect"}
            else AdminOperation.SET_ENABLED
        )
        epoch = data["core_epoch"]
        effect = await self._check(epoch, grant, operation)
        actor = (
            "hot-admin:"
            + hashlib.sha256(
                (effect.context.adapter_id + ":" + effect.context.session_id).encode()
            ).hexdigest()
        )
        check = self._worker_check(epoch, grant, operation)
        if action == "status":
            items = []
            package_ids = {
                item.package_id
                for item in self.manager.discovery.scan()
                if item.descriptor["role"] == "dlc"
            } | set(self.manager.packages)
            for package_id in sorted(package_ids):
                try:
                    items.append(self._remember(await self._inspect(package_id, epoch)))
                except AdminAuthorizationDenied:
                    raise
                except Exception as exc:
                    items.append(self._unavailable_status(package_id, exc))
                await self._check(epoch, grant, operation)
            publishers = await self._publishers(epoch, grant)
            await self._check(epoch, grant, operation)
            return {
                "ok": True,
                "core_epoch": epoch,
                "packages": items,
                "publishers": publishers,
                "core": self._core_status(),
            }
        if "package_id" in data:
            _package_id(data["package_id"])
        if action == "inspect":
            plan = await self._inspect(data["package_id"], epoch)
            await self._check(epoch, grant, operation)
            return {"ok": True, "core_epoch": epoch, **self._remember(plan)}
        if action == "retry":
            # A fresh request UUID avoids replaying a failed operation forever.
            from uuid import uuid4

            plan = await self._inspect(data["package_id"], epoch)
            await self._check(epoch, grant, operation)
            reply = await self.manager.endpoint.request(
                plan.installation.instance,
                {
                    "protocol": "ygl.control/1",
                    "op": "connect",
                    "request_id": str(uuid4()),
                    "descriptor_digest": plan.descriptor_digest,
                },
            )
            await self._check(epoch, grant, operation)
            return {
                "ok": reply.get("ok") is True,
                "core_epoch": epoch,
                "state": reply.get("state"),
                "operation_id": reply.get("operation_id"),
                "reason_code": reply.get("error", {}).get("code"),
            }
        _revision(data["expected_revision"])
        kwargs = {
            "actor_ref": actor,
            "expected_revision": data["expected_revision"],
            "admin_check": check,
        }
        if action in {"package-approve", "artifact-approve"}:
            old = (
                self._plans.get(data["plan_id"])
                if type(data["plan_id"]) is str
                else None
            )
            if (
                old is None
                or old.expires < monotonic()
                or old.epoch != epoch
                or old.package_id != data["package_id"]
            ):
                raise HotUpdateAdminError("plan_changed")
            plan = await self._inspect(data["package_id"], epoch)
            if (old.grant_revision, old.artifact_revision, old.publisher_revision) != (
                plan.grant_revision,
                plan.artifact_revision,
                plan.publisher_revision,
            ):
                raise HotUpdateAdminError("revision_conflict")
            if (
                old.binding() != plan.binding()
                or old.installation.instance is not plan.installation.instance
            ):
                raise HotUpdateAdminError("plan_changed")
            await self._check(epoch, grant, operation)
            kwargs["admin_check"] = self._worker_check(epoch, grant, operation, plan)
            if plan.signature_state == "host_install":
                raise HotUpdateAdminError("host_install_managed")
            if plan.signature_state not in {"unsigned", "verified"}:
                raise HotUpdateAdminError("publisher_untrusted")
            if plan.reason not in {None, "approval_required"}:
                raise HotUpdateAdminError(plan.reason)
            expected = (
                plan.grant_revision
                if action == "package-approve"
                else plan.artifact_revision
            )
            if data["expected_revision"] != expected:
                raise HotUpdateAdminError("revision_conflict")
            if action == "package-approve":
                record = await self.repository.approve_package(
                    plan.package_id,
                    publisher_key_id=plan.publisher_key_id,
                    plugin_id=json.loads(plan.plugin_json),
                    permissions=json.loads(plan.permission_json),
                    **kwargs,
                )
            else:
                if plan.signature_state != "unsigned" or plan.grant_revision == 0:
                    raise HotUpdateAdminError("approval_required")
                record = await self.repository.approve_artifact(
                    plan.artifact_digest,
                    package_id=plan.package_id,
                    plugin_id=json.loads(plan.plugin_json),
                    grant_revision=plan.grant_revision,
                    **kwargs,
                )
            self._plans.pop(data["plan_id"], None)
        elif action == "package-revoke":
            record = await self.repository.revoke_package(data["package_id"], **kwargs)
        elif action == "artifact-revoke":
            _digest(data["artifact_digest"])
            row = await self.repository.approval(data["artifact_digest"])
            await self._check(epoch, grant, operation)
            if row is None or row["package_id"] != data["package_id"]:
                raise HotUpdateAdminError("revision_conflict")
            record = await self.repository.revoke_artifact(
                data["artifact_digest"], **kwargs
            )
        elif action == "publisher-revoke":
            _digest(data["key_id"])
            record = await self.repository.revoke_publisher(data["key_id"], **kwargs)
        else:
            encoded = data["public_key_base64"]
            if type(encoded) is not str or len(encoded) != 44:
                raise ValueError("invalid public key")
            public = base64.b64decode(encoded, validate=True)
            if len(public) != 32 or base64.b64encode(public).decode() != encoded:
                raise ValueError("invalid public key")
            record = await self.repository.put_publisher(
                public, scope_policy={"channels": data["channels"]}, **kwargs
            )
        await self._check(epoch, grant, operation)
        return {"ok": True, "core_epoch": epoch, "revision": record["revision"]}
