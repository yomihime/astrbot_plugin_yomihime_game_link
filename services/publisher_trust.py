"""Exact-byte release authentication, separate from inventory capture and approval."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..extensions.hot_update_protocol import parse_release
from ..infrastructure.sqlite.repositories_extension_policy import (
    PolicyError,
    _identity,
    _projection,
)
from .extension_permissions import (
    permission_delta,
    permission_digest,
    permissions_within,
)


@dataclass(frozen=True)
class TrustReceipt:
    publisher_key_id: str
    artifact_digest: str
    plugin_identity_json: str
    package_id: str
    grant_revision: int
    approved_permission_digest: str
    publisher_revision: int
    artifact_approval_revision: int
    release_sequence: int
    channel: str
    permission_projection_json: str = "{}"

    @property
    def plugin_id(self):
        return json.loads(self.plugin_identity_json)


class PublisherTrust:
    def __init__(self, repository):
        self.repository = repository

    async def evaluate(
        self, raw_release, signature, *, host_plugin_id, permission_projection
    ):
        return await self._evaluate(
            raw_release,
            signature,
            host_plugin_id=host_plugin_id,
            permission_projection=permission_projection,
            enforce_head=True,
        )

    async def evaluate_cached(
        self,
        raw_release,
        signature,
        *,
        host_plugin_id,
        permission_projection,
        pinned_digest,
    ):
        """Internal exact-pin recovery; never exposed through control or SDK APIs."""
        if hashlib.sha256(raw_release).hexdigest() != pinned_digest:
            raise PolicyError("artifact_tampered")
        return await self._evaluate(
            raw_release,
            signature,
            host_plugin_id=host_plugin_id,
            permission_projection=permission_projection,
            enforce_head=False,
        )

    async def _evaluate(
        self,
        raw_release,
        signature,
        *,
        host_plugin_id,
        permission_projection,
        enforce_head,
    ):
        """Read-only prepare check; caller must verify captured payload inventory too.

        artifact_digest is SHA256(raw release.json), whose validated inventory binds
        payload contents. Signature presence is never treated as unsigned fallback.
        """
        _projection(permission_projection)
        release = parse_release(raw_release)
        identity = _identity(host_plugin_id)
        if _identity(release["plugin_id"]) != identity:
            raise PolicyError("artifact_tampered")
        digest = hashlib.sha256(raw_release).hexdigest()
        key_id, publisher_revision, approval_revision = "", 0, 0
        if signature is not None:
            if (
                type(signature) is not dict
                or set(signature) != {"algorithm", "key_id", "signature_base64"}
                or signature["algorithm"] != "ed25519"
                or type(signature["key_id"]) is not str
            ):
                raise PolicyError("artifact_tampered")
            key_id = signature["key_id"]
            publisher = await self.repository.publisher(key_id)
            if publisher is None or publisher["revoked"]:
                raise PolicyError("publisher_untrusted")
            try:
                public_key = publisher["public_key"]
                if hashlib.sha256(public_key).hexdigest() != key_id:
                    raise ValueError
                encoded = signature["signature_base64"]
                if type(encoded) is not str or len(encoded) > 128:
                    raise ValueError
                sig = base64.b64decode(encoded, validate=True)
                Ed25519PublicKey.from_public_bytes(public_key).verify(sig, raw_release)
            except (ValueError, TypeError, InvalidSignature):
                raise PolicyError("artifact_tampered") from None
            scope = json.loads(publisher["scope_policy"])
            if release["channel"] not in scope["channels"]:
                raise PolicyError("release_downgrade")
            publisher_revision = publisher["revision"]
        grant = await self.repository.grant(release["package_id"])
        if grant is None and key_id and "capability_ceiling" in scope:
            ceiling = scope["capability_ceiling"]
            grant = {
                "revision": 0,
                "revoked": 0,
                "publisher_key_id": key_id,
                "plugin_id": identity,
                "permission_json": json.dumps(ceiling),
                "permission_digest": permission_digest(ceiling),
            }
        if (
            grant is None
            or grant["revoked"]
            or grant["publisher_key_id"] != key_id
            or grant["plugin_id"] != identity
        ):
            raise PolicyError("approval_required")
        approved = json.loads(grant["permission_json"])
        if not permissions_within(permission_projection, approved):
            error = PolicyError("approval_required")
            error.permission_delta = permission_delta(permission_projection, approved)
            raise error
        if not key_id:
            approval = await self.repository.approval(digest)
            if (
                approval is None
                or approval["revoked"]
                or approval["package_id"] != release["package_id"]
                or approval["plugin_id"] != identity
                or approval["grant_revision"] != grant["revision"]
            ):
                raise PolicyError("approval_required")
            approval_revision = approval["revision"]
        head = await self.repository.head(
            release["package_id"], key_id, release["channel"]
        )
        if (
            enforce_head
            and head
            and (
                release["release_sequence"] < head["release_sequence"]
                or (
                    release["release_sequence"] == head["release_sequence"]
                    and digest != head["artifact_digest"]
                )
            )
        ):
            raise PolicyError("release_downgrade")
        receipt = TrustReceipt(
            key_id,
            digest,
            identity,
            release["package_id"],
            grant["revision"],
            grant["permission_digest"],
            publisher_revision,
            approval_revision,
            release["release_sequence"],
            release["channel"],
            json.dumps(
                permission_projection,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
        if not await self.repository.execution_revision_valid(receipt):
            raise PolicyError("revision_conflict")
        return receipt
