"""Persistent policy decisions; administrators authenticate before calling mutations."""

from __future__ import annotations

import hashlib
import inspect
import json
import re

from ...services.extension_permissions import permission_digest, permissions_within


class PolicyError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid digest")
    return value


def _identity(value):
    if (
        type(value) is not dict
        or set(value) != {"author", "name"}
        or any(
            type(v) is not str
            or not v
            or len(v) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in v)
            for v in value.values()
        )
    ):
        raise ValueError("invalid plugin identity")
    return _json(value)


def _revision(value):
    if type(value) is not int or not 0 <= value <= 9007199254740991:
        raise ValueError("invalid expected revision")
    return value


def _package_id(value):
    if (
        type(value) is not str
        or len(value) > 64
        or re.fullmatch(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*", value) is None
    ):
        raise ValueError("invalid package identity")
    return value


def _projection(value):
    """Bounded, JSON-only owner/kind/entry authority maps."""
    from ...extensions.hot_update_protocol import _load

    if type(value) is not dict:
        raise ValueError("invalid permission projection")

    def json_only(item, depth=0):
        if depth > 32:
            raise ValueError
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError
                json_only(child, depth + 1)
        elif type(item) is list:
            for child in item:
                json_only(child, depth + 1)
        elif item is not None and type(item) not in {str, int, float, bool}:
            raise ValueError

    try:
        json_only(value)
        raw = _json(value).encode("utf-8")
        _load(raw, 1048576, depth=32, nodes=20000)
        for owner, kinds in value.items():
            if type(owner) is not str or not owner or type(kinds) is not dict:
                raise ValueError
            for kind, entries in kinds.items():
                if type(kind) is not str or not kind or type(entries) is not dict:
                    raise ValueError
                if any(type(name) is not str or not name for name in entries):
                    raise ValueError
    except (TypeError, ValueError, RecursionError):
        raise ValueError("invalid permission projection") from None
    return value


def _check_admin(unit, check):
    if check is not None:
        result = check(unit)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("admin_check must be synchronous")


def assert_execution_policy(unit, receipt):
    """Check in the same transaction as the effect; receipts confer no cached authority."""
    grant = unit.execute(
        "SELECT * FROM extension_package_grants WHERE package_id=?",
        (receipt.package_id,),
    ).fetchone()
    ceiling_admission = (
        grant is None and receipt.grant_revision == 0 and bool(receipt.publisher_key_id)
    )
    if not ceiling_admission and (
        grant is None
        or grant["revoked"]
        or grant["revision"] != receipt.grant_revision
        or grant["publisher_key_id"] != receipt.publisher_key_id
        or grant["plugin_id"] != receipt.plugin_identity_json
        or grant["permission_digest"] != receipt.approved_permission_digest
    ):
        raise PolicyError("approval_required")
    if receipt.publisher_key_id:
        publisher = unit.execute(
            "SELECT * FROM extension_publisher_trust WHERE key_id=?",
            (receipt.publisher_key_id,),
        ).fetchone()
        if (
            publisher is None
            or publisher["revoked"]
            or publisher["revision"] != receipt.publisher_revision
        ):
            raise PolicyError("publisher_untrusted")
        scope = json.loads(publisher["scope_policy"])
        if receipt.channel not in scope["channels"]:
            raise PolicyError("publisher_untrusted")
        if ceiling_admission:
            ceiling = scope.get("capability_ceiling")
            if (
                ceiling is None
                or permission_digest(ceiling) != receipt.approved_permission_digest
                or not permissions_within(
                    json.loads(receipt.permission_projection_json), ceiling
                )
            ):
                raise PolicyError("approval_required")
    else:
        approval = unit.execute(
            "SELECT * FROM extension_artifact_approvals WHERE artifact_digest=?",
            (receipt.artifact_digest,),
        ).fetchone()
        if (
            approval is None
            or approval["revoked"]
            or approval["revision"] != receipt.artifact_approval_revision
            or approval["grant_revision"] != receipt.grant_revision
            or approval["package_id"] != receipt.package_id
            or approval["plugin_id"] != receipt.plugin_identity_json
        ):
            raise PolicyError("approval_required")


def check_package_owner(unit, package_id, plugin_id, *, host_install, bind=False):
    """Keep package-scoped data attached to the installer identity across releases."""
    _package_id(package_id)
    identity = _identity(plugin_id)
    owner = unit.execute(
        "SELECT plugin_id FROM extension_package_owners WHERE package_id=?",
        (package_id,),
    ).fetchone()
    if owner is not None:
        if owner["plugin_id"] != identity:
            raise PolicyError("package_already_owned")
        return
    if not host_install:
        return

    early = unit.execute(
        "SELECT plugin_name FROM extension_installation_grants WHERE package_id=?",
        (package_id,),
    ).fetchone()
    legacy = unit.execute(
        "SELECT plugin_id FROM extension_package_grants WHERE package_id=?",
        (package_id,),
    ).fetchone()
    if (early is not None and early["plugin_name"] != plugin_id["name"]) or (
        legacy is not None and legacy["plugin_id"] != identity
    ):
        raise PolicyError("package_already_owned")
    if bind:
        unit.execute(
            "INSERT INTO extension_package_owners (package_id, plugin_id) VALUES (?, ?)",
            (package_id, identity),
        )


def advance_release_head(
    unit,
    *,
    package_id,
    publisher_key_id,
    channel,
    sequence,
    artifact_digest,
    expected_sequence=None,
    expected_digest=None,
):
    """Activation-only CAS. Call inside the owner/journal transaction, never prepare."""
    _digest(artifact_digest)
    _package_id(package_id)
    if publisher_key_id:
        _digest(publisher_key_id)
    if expected_sequence is not None:
        _revision(expected_sequence)
    if expected_digest is not None:
        _digest(expected_digest)
    if type(sequence) is not int or not 0 <= sequence <= 9007199254740991:
        raise ValueError("invalid release sequence")
    if channel not in {"stable", "preview"}:
        raise ValueError("invalid channel")
    key = (package_id, publisher_key_id, channel)
    row = unit.execute(
        "SELECT * FROM extension_release_heads WHERE package_id=? AND publisher_key_id=? AND channel=?",
        key,
    ).fetchone()
    actual = None if row is None else (row["release_sequence"], row["artifact_digest"])
    if actual != (
        None
        if expected_sequence is None and expected_digest is None
        else (expected_sequence, expected_digest)
    ):
        raise PolicyError("revision_conflict")
    if row is not None and (
        sequence < row["release_sequence"]
        or (
            sequence == row["release_sequence"]
            and artifact_digest != row["artifact_digest"]
        )
    ):
        raise PolicyError("release_downgrade")
    unit.execute(
        "INSERT INTO extension_release_heads VALUES(?,?,?,?,?) ON CONFLICT(package_id,publisher_key_id,channel) DO UPDATE SET release_sequence=excluded.release_sequence,artifact_digest=excluded.artifact_digest",
        (*key, sequence, artifact_digest),
    )


class ExtensionPolicyRepository:
    def __init__(self, database, *, before_mutation=None):
        """R4 must bind a Core-owned synchronous gate fence in production.

        The hook is not authentication and must never be supplied by DLC code.
        A failed CAS leaves gates conservatively closed pending reconnect.
        """
        if before_mutation is not None and not callable(before_mutation):
            raise TypeError("before_mutation must be callable")
        self.database = database
        self.before_mutation = before_mutation

    def _fence_mutation(self, table, key, actor_ref, expected_revision):
        _revision(expected_revision)
        if (
            type(actor_ref) is not str
            or not actor_ref.strip()
            or len(actor_ref) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in actor_ref)
        ):
            raise ValueError("explicit authenticated actor required")
        if self.before_mutation is not None:
            kind = {
                "extension_publisher_trust": "publisher",
                "extension_package_grants": "package_grant",
                "extension_artifact_approvals": "artifact_approval",
            }[table]
            result = self.before_mutation(kind, key)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError("before_mutation must be synchronous")

    async def _read(self, table, column, key):
        def read(unit):
            row = unit.execute(
                f"SELECT * FROM {table} WHERE {column}=?", (key,)
            ).fetchone()
            return None if row is None else dict(row)

        return await self.database.executor.run_read(read)

    async def publisher(self, key_id):
        return await self._read("extension_publisher_trust", "key_id", key_id)

    async def grant(self, package_id):
        return await self._read("extension_package_grants", "package_id", package_id)

    async def approval(self, artifact_digest):
        return await self._read(
            "extension_artifact_approvals", "artifact_digest", artifact_digest
        )

    async def head(self, package_id, publisher_key_id, channel):
        def read(unit):
            row = unit.execute(
                "SELECT * FROM extension_release_heads WHERE package_id=? AND publisher_key_id=? AND channel=?",
                (package_id, publisher_key_id, channel),
            ).fetchone()
            return None if row is None else dict(row)

        return await self.database.executor.run_read(read)

    async def check_package_owner(self, package_id, plugin_id, *, host_install):
        await self.database.executor.run_read(
            lambda unit: check_package_owner(
                unit, package_id, plugin_id, host_install=host_install
            )
        )

    async def _put(
        self, table, identity, values, *, actor_ref, expected_revision, admin_check=None
    ):
        column, key = identity
        self._fence_mutation(table, key, actor_ref, expected_revision)

        def write(unit):
            _check_admin(unit, admin_check)
            if table == "extension_package_grants":
                check_package_owner(
                    unit, key, json.loads(values["plugin_id"]), host_install=False
                )
            row = unit.execute(
                f"SELECT revision FROM {table} WHERE {column}=?", (key,)
            ).fetchone()
            revision = 0 if row is None else row["revision"]
            if revision != expected_revision:
                raise PolicyError("revision_conflict")
            record = {
                column: key,
                **values,
                "revision": revision + 1,
                "actor_ref": actor_ref,
            }
            columns = list(record)
            unit.execute(
                f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) ON CONFLICT({column}) DO UPDATE SET "
                + ",".join(f"{c}=excluded.{c}" for c in columns[1:]),
                tuple(record.values()),
            )
            return record

        return await self.database.executor.run_transaction(
            write, begin_mode="IMMEDIATE"
        )

    async def put_publisher(
        self,
        public_key,
        *,
        scope_policy,
        revoked=False,
        actor_ref,
        expected_revision,
        admin_check=None,
    ):
        if (
            type(public_key) is not bytes
            or len(public_key) != 32
            or type(revoked) is not bool
        ):
            raise ValueError("invalid publisher")
        if (
            type(scope_policy) is not dict
            or set(scope_policy) - {"channels", "capability_ceiling"}
            or type(scope_policy.get("channels")) is not list
            or not scope_policy["channels"]
            or any(type(channel) is not str for channel in scope_policy["channels"])
            or set(scope_policy["channels"]) - {"stable", "preview"}
        ):
            raise ValueError("invalid publisher scope")
        if "capability_ceiling" in scope_policy:
            ceiling = scope_policy["capability_ceiling"]
            try:
                _projection(ceiling)
            except (TypeError, AttributeError, ValueError):
                raise ValueError("invalid capability ceiling") from None
        return await self._put(
            "extension_publisher_trust",
            ("key_id", hashlib.sha256(public_key).hexdigest()),
            {
                "public_key": public_key,
                "scope_policy": _json(scope_policy),
                "revoked": int(revoked),
            },
            actor_ref=actor_ref,
            expected_revision=expected_revision,
            admin_check=admin_check,
        )

    async def approve_package(
        self,
        package_id,
        *,
        publisher_key_id,
        plugin_id,
        permissions,
        actor_ref,
        expected_revision,
        admin_check=None,
    ):
        _package_id(package_id)
        _projection(permissions)
        if type(publisher_key_id) is not str:
            raise ValueError("invalid publisher identity")
        if publisher_key_id:
            _digest(publisher_key_id)
        return await self._put(
            "extension_package_grants",
            ("package_id", package_id),
            {
                "publisher_key_id": publisher_key_id,
                "plugin_id": _identity(plugin_id),
                "permission_json": _json(permissions),
                "permission_digest": permission_digest(permissions),
                "revoked": 0,
            },
            actor_ref=actor_ref,
            expected_revision=expected_revision,
            admin_check=admin_check,
        )

    async def _revoke(
        self, table, column, key, *, actor_ref, expected_revision, admin_check=None
    ):
        self._fence_mutation(table, key, actor_ref, expected_revision)

        def write(unit):
            _check_admin(unit, admin_check)
            row = unit.execute(
                f"SELECT * FROM {table} WHERE {column}=?", (key,)
            ).fetchone()
            if row is None or row["revision"] != expected_revision:
                raise PolicyError("revision_conflict")
            unit.execute(
                f"UPDATE {table} SET revoked=1,revision=revision+1,actor_ref=? WHERE {column}=? AND revision=?",
                (actor_ref, key, expected_revision),
            )
            return {
                **dict(row),
                "revoked": 1,
                "revision": expected_revision + 1,
                "actor_ref": actor_ref,
            }

        return await self.database.executor.run_transaction(
            write, begin_mode="IMMEDIATE"
        )

    async def revoke_package(
        self, package_id, *, actor_ref, expected_revision, admin_check=None
    ):
        _package_id(package_id)
        return await self._revoke(
            "extension_package_grants",
            "package_id",
            package_id,
            actor_ref=actor_ref,
            expected_revision=expected_revision,
            admin_check=admin_check,
        )

    async def revoke_publisher(
        self, key_id, *, actor_ref, expected_revision, admin_check=None
    ):
        _digest(key_id)
        return await self._revoke(
            "extension_publisher_trust",
            "key_id",
            key_id,
            actor_ref=actor_ref,
            expected_revision=expected_revision,
            admin_check=admin_check,
        )

    async def revoke_artifact(
        self, artifact_digest, *, actor_ref, expected_revision, admin_check=None
    ):
        _digest(artifact_digest)
        return await self._revoke(
            "extension_artifact_approvals",
            "artifact_digest",
            artifact_digest,
            actor_ref=actor_ref,
            expected_revision=expected_revision,
            admin_check=admin_check,
        )

    async def approve_artifact(
        self,
        artifact_digest,
        *,
        package_id,
        plugin_id,
        grant_revision,
        actor_ref,
        expected_revision,
        revoked=False,
        admin_check=None,
    ):
        _digest(artifact_digest)
        _package_id(package_id)
        _revision(grant_revision)
        if grant_revision == 0 or type(revoked) is not bool:
            raise ValueError("invalid artifact approval")
        return await self._put(
            "extension_artifact_approvals",
            ("artifact_digest", artifact_digest),
            {
                "package_id": package_id,
                "plugin_id": _identity(plugin_id),
                "grant_revision": grant_revision,
                "revoked": int(revoked),
            },
            actor_ref=actor_ref,
            expected_revision=expected_revision,
            admin_check=admin_check,
        )

    async def execution_revision_valid(self, receipt):
        def read(unit):
            try:
                assert_execution_policy(unit, receipt)
            except PolicyError:
                return False
            return True

        return await self.database.executor.run_read(read)
