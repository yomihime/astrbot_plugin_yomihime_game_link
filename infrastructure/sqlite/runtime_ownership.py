"""Transactional ownership and crash journal; no Python owner resurrection."""

from __future__ import annotations

import re
import secrets
import sqlite3

from ...services.runtime_effect_gate import EffectRejected

_PHASES = (
    "PREPARED",
    "OLD_FENCED",
    "OLD_QUIET",
    "COMMITTED_CLOSED",
    "PUBLISHED",
    "ACTIVE",
    "FINALIZED",
)


class OwnershipConflict(RuntimeError):
    pass


def _scope(scope):
    if type(scope) is not str or (
        scope != "core"
        and (
            not scope.startswith("package:")
            or len(scope[8:]) > 64
            or re.fullmatch(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*", scope[8:]) is None
        )
    ):
        raise ValueError("invalid owner scope")


def _positive(value):
    return type(value) is int and 1 <= value <= 2**53 - 1


def assert_owner(unit, scope, token, core_epoch, package_epoch, grant_revision=None):
    row = unit.connection.execute(
        "SELECT token,core_epoch,package_epoch,accepting,grant_revision "
        "FROM runtime_owners WHERE scope=?",
        (scope,),
    ).fetchone()
    if row is None or tuple(row[:4]) != (token, core_epoch, package_epoch, 1):
        raise EffectRejected()
    if grant_revision is not None and row[4] != grant_revision:
        raise EffectRejected("grant_revoked")


class RuntimeOwnership:
    """Sync helpers called only inside executor-owned Core control transactions."""

    @staticmethod
    def prepare(
        unit,
        transition_id,
        scope,
        to_digest,
        *,
        boot_id,
        from_boot_id=None,
        from_digest=None,
        from_epoch=0,
        to_epoch,
    ):
        _scope(scope)
        if (
            type(to_digest) is not str
            or not re.fullmatch("[0-9a-f]{64}", to_digest)
            or type(boot_id) is not str
            or not boot_id
            or not _positive(to_epoch)
            or type(from_epoch) is not int
            or not 0 <= from_epoch <= 2**53 - 1
        ):
            raise ValueError("invalid transition")
        old = unit.connection.execute(
            "SELECT core_epoch,package_epoch,boot_id,accepting,digest FROM runtime_owners WHERE scope=?",
            (scope,),
        ).fetchone()
        if old is None:
            if from_epoch != 0 or from_boot_id is not None or from_digest is not None:
                raise OwnershipConflict("stale_owner")
        else:
            old_epoch = old[0] if scope == "core" else old[1]
            if (
                old_epoch != from_epoch
                or old[2] != from_boot_id
                or old[4] != from_digest
            ):
                raise OwnershipConflict("stale_owner")
            if old[2] != boot_id:
                if old[3] != 0:
                    raise OwnershipConflict("old_owner_not_fenced")
            elif to_epoch <= old_epoch:
                raise OwnershipConflict("stale_epoch")
        unit.connection.execute(
            "INSERT INTO runtime_transitions "
            "(id,scope,from_digest,to_digest,from_epoch,to_epoch,from_boot_id,boot_id,phase) "
            "VALUES (?,?,?,?,?,?,?,?,'PREPARED')",
            (
                transition_id,
                scope,
                from_digest,
                to_digest,
                from_epoch,
                to_epoch,
                from_boot_id,
                boot_id,
            ),
        )

    @staticmethod
    def phase(unit, transition_id, expected, phase, *, error_code=None):
        if phase in ("FAILED", "WAITING_CLEANUP"):
            if expected not in _PHASES:
                raise ValueError("invalid phase")
            resume = expected
        else:
            if expected not in _PHASES or phase not in _PHASES:
                raise ValueError("invalid phase")
            if _PHASES.index(phase) != _PHASES.index(expected) + 1:
                raise ValueError("invalid phase transition")
            if phase in ("COMMITTED_CLOSED", "ACTIVE"):
                raise ValueError("owner phase requires atomic owner change")
            resume = None
        cursor = unit.connection.execute(
            "UPDATE runtime_transitions SET phase=?,resume_phase=?,error_code=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND phase=?",
            (phase, resume, error_code, transition_id, expected),
        )
        if cursor.rowcount != 1:
            raise OwnershipConflict("stale_transition")

    @staticmethod
    def resume_waiting(unit, transition_id, resume_phase):
        if resume_phase not in _PHASES:
            raise ValueError("invalid resume phase")
        cursor = unit.connection.execute(
            "UPDATE runtime_transitions SET phase=?,resume_phase=NULL,error_code=NULL,"
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND phase='WAITING_CLEANUP' AND resume_phase=?",
            (resume_phase, transition_id, resume_phase),
        )
        if cursor.rowcount != 1:
            raise OwnershipConflict("stale_transition")

    @staticmethod
    def fence(unit, scope, token, *, expected_revision, transition_id=None):
        _scope(scope)
        if transition_id is not None:
            row = unit.connection.execute(
                "SELECT scope FROM runtime_transitions WHERE id=? AND phase='PREPARED'",
                (transition_id,),
            ).fetchone()
            if row is None or row[0] != scope:
                raise OwnershipConflict("stale_transition")
        cursor = unit.connection.execute(
            "UPDATE runtime_owners SET accepting=0,revision=revision+1 "
            "WHERE scope=? AND token=? AND revision=?",
            (scope, token, expected_revision),
        )
        if cursor.rowcount != 1:
            raise OwnershipConflict("stale_owner")
        if transition_id is not None:
            RuntimeOwnership.phase(unit, transition_id, "PREPARED", "OLD_FENCED")
        return expected_revision + 1

    @staticmethod
    def commit_closed(
        unit,
        transition_id,
        *,
        expected_token,
        expected_revision,
        core_epoch,
        package_epoch=0,
        grant_revision=0,
        boot_id,
        release_commit=None,
        new_token=None,
    ):
        row = unit.connection.execute(
            "SELECT scope,to_digest,to_epoch,from_epoch,from_boot_id,boot_id FROM runtime_transitions WHERE id=? AND phase='OLD_QUIET'",
            (transition_id,),
        ).fetchone()
        if row is None:
            raise OwnershipConflict("stale_transition")
        scope, digest, epoch, from_epoch, from_boot_id, journal_boot_id = row
        if boot_id != journal_boot_id:
            raise OwnershipConflict("stale_boot")
        if epoch != (core_epoch if scope == "core" else package_epoch):
            raise OwnershipConflict("stale_epoch")
        token = new_token or secrets.token_hex(32)
        if token == expected_token or not re.fullmatch("[0-9a-f]{64}", token):
            raise OwnershipConflict("invalid_new_token")
        if expected_token is None:
            if expected_revision != 0:
                raise OwnershipConflict("stale_owner")
            try:
                unit.connection.execute(
                    "INSERT INTO runtime_owners VALUES (?,?,?,?,0,1,?,?,?)",
                    (
                        scope,
                        token,
                        core_epoch,
                        package_epoch,
                        grant_revision,
                        digest,
                        boot_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise OwnershipConflict("owner_exists") from exc
            revision = 1
        else:
            epoch_column = "core_epoch" if scope == "core" else "package_epoch"
            cursor = unit.connection.execute(
                "UPDATE runtime_owners SET token=?,core_epoch=?,package_epoch=?,"
                "accepting=0,revision=revision+1,grant_revision=?,digest=?,boot_id=? "
                "WHERE scope=? AND token=? AND revision=? AND accepting=0 AND boot_id=? "
                f"AND {epoch_column}=?",
                (
                    token,
                    core_epoch,
                    package_epoch,
                    grant_revision,
                    digest,
                    boot_id,
                    scope,
                    expected_token,
                    expected_revision,
                    from_boot_id,
                    from_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise OwnershipConflict("stale_owner")
            revision = expected_revision + 1
        if release_commit is not None:
            release_commit(unit)
        unit.connection.execute(
            "UPDATE runtime_transitions SET phase='COMMITTED_CLOSED',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (transition_id,),
        )
        return {"token": token, "revision": revision}

    @staticmethod
    def open_active(unit, transition_id, token, expected_revision, *, boot_id):
        row = unit.connection.execute(
            "SELECT scope FROM runtime_transitions WHERE id=? AND phase='PUBLISHED'",
            (transition_id,),
        ).fetchone()
        if row is None:
            raise OwnershipConflict("stale_transition")
        cursor = unit.connection.execute(
            "UPDATE runtime_owners SET accepting=1,revision=revision+1 "
            "WHERE scope=? AND token=? AND revision=? AND accepting=0 AND boot_id=?",
            (row[0], token, expected_revision, boot_id),
        )
        if cursor.rowcount != 1:
            raise OwnershipConflict("stale_owner")
        unit.connection.execute(
            "UPDATE runtime_transitions SET phase='ACTIVE',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (transition_id,),
        )
        return expected_revision + 1

    @staticmethod
    def fence_previous_boot(unit, boot_id):
        """Close abandoned owners before recovery; opening requires new-boot CAS."""
        return unit.connection.execute(
            "UPDATE runtime_owners SET accepting=0,revision=revision+1 "
            "WHERE boot_id!=? AND accepting=1",
            (boot_id,),
        ).rowcount

    @staticmethod
    def recoverable(unit):
        cursor = unit.connection.execute(
            "SELECT id,scope,from_digest,to_digest,from_epoch,to_epoch,from_boot_id,boot_id,phase,resume_phase,error_code "
            "FROM runtime_transitions WHERE phase!='FINALIZED' ORDER BY updated_at,id"
        )
        keys = tuple(column[0] for column in cursor.description)
        return tuple(dict(zip(keys, row)) for row in cursor.fetchall())

    @staticmethod
    def check_data_contract(unit, package_id, schema, writes, readable):
        _scope("package:" + package_id)
        if (
            not _positive(schema)
            or not _positive(writes)
            or type(readable) not in (tuple, list)
            or not readable
            or len(readable) > 256
            or any(not _positive(value) for value in readable)
            or len(set(readable)) != len(readable)
        ):
            raise ValueError("invalid data contract")
        row = unit.connection.execute(
            "SELECT schema_version,writes_version FROM runtime_data_contracts WHERE package_id=?",
            (package_id,),
        ).fetchone()
        if writes not in readable or (
            row is not None and tuple(row) != (schema, writes)
        ):
            raise OwnershipConflict("data_incompatible")
        if row is None:
            unit.connection.execute(
                "INSERT INTO runtime_data_contracts VALUES (?,?,?)",
                (package_id, schema, writes),
            )
