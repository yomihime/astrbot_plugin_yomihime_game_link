"""Windows ACL decisions with synthetic SIDs; no real ACL is modified."""

from __future__ import annotations

import unittest

from ygl_test_subject.infrastructure.managed_key_file import ManagedKeyFileError
from ygl_test_subject.infrastructure.windows.maintenance import (
    INHERIT_ONLY_ACE,
    WindowsMaintenanceError,
    _Ace,
    _validate_descriptor,
)
from ygl_test_subject.infrastructure.windows.managed_key_io import (
    _edge_acl,
    _resolve_owner_rights,
)

OPERATOR = b"operator"
SYSTEM = b"system"
ADMINS = b"admins"
INSTALLER = b"installer"
OWNER_RIGHTS = bytes.fromhex("010100000000000304000000")  # S-1-3-4
AUTHENTICATED_USERS = b"S-1-5-11"
TRUSTED = (SYSTEM, ADMINS, INSTALLER)
FULL_CONTROL = 0x001F01FF
FILE_DELETE_CHILD = 0x40
DELETE = 0x10000


class _Descriptor:
    def __init__(self, owner, aces):
        self.owner = owner
        self.aces = aces

    def descriptor(self, _handle):
        return self.owner, self.aces

    def _fixed_sid(self, sid):
        if sid == "S-1-3-4":
            return OWNER_RIGHTS
        return sid.encode()


def _edge(owner, aces, *, parent):
    return _edge_acl(
        _Descriptor(owner, tuple(aces)), object(), OPERATOR, TRUSTED, parent=parent
    )


class ManagedKeyAclBoundariesTests(unittest.TestCase):
    def test_owner_rights_full_control_tracks_verified_operator_owner(self):
        owner_ace = _Ace(0, 3, FULL_CONTROL, OWNER_RIGHTS)
        _edge(OPERATOR, (owner_ace,), parent=True)
        _edge(OPERATOR, (owner_ace,), parent=False)
        resolved = _resolve_owner_rights(
            _Descriptor(OPERATOR, (owner_ace,)),
            OPERATOR,
            (owner_ace,),
            {OPERATOR, SYSTEM, ADMINS},
        )
        _validate_descriptor(
            OPERATOR,
            resolved,
            OPERATOR,
            SYSTEM,
            ADMINS,
            INSTALLER,
            role="data",
            directory=True,
        )

    def test_owner_rights_does_not_trust_an_unverified_owner(self):
        owner_ace = _Ace(0, 3, FULL_CONTROL, OWNER_RIGHTS)
        with self.assertRaises(ManagedKeyFileError):
            _edge(b"outside-owner", (owner_ace,), parent=True)
        with self.assertRaises(ManagedKeyFileError):
            _resolve_owner_rights(
                _Descriptor(b"outside-owner", (owner_ace,)),
                b"outside-owner",
                (owner_ace,),
                {OPERATOR, SYSTEM, ADMINS},
            )

    def test_broad_parent_and_child_replacement_rights_remain_denied(self):
        with self.assertRaises(ManagedKeyFileError):
            _edge(
                OPERATOR,
                (_Ace(0, 0, FILE_DELETE_CHILD, AUTHENTICATED_USERS),),
                parent=True,
            )
        with self.assertRaises(ManagedKeyFileError):
            _edge(
                OPERATOR,
                (_Ace(0, 0, DELETE, AUTHENTICATED_USERS),),
                parent=False,
            )
        with self.assertRaises(WindowsMaintenanceError):
            _validate_descriptor(
                OPERATOR,
                (_Ace(0, 0, FULL_CONTROL, AUTHENTICATED_USERS),),
                OPERATOR,
                SYSTEM,
                ADMINS,
                INSTALLER,
                role="data",
                directory=True,
            )

    def test_inherit_only_allow_is_not_effective_on_ancestor_edge(self):
        inherited_for_children = _Ace(
            0, INHERIT_ONLY_ACE | 3, FULL_CONTROL, AUTHENTICATED_USERS
        )
        _edge(OPERATOR, (inherited_for_children,), parent=True)
        _edge(OPERATOR, (inherited_for_children,), parent=False)
        # A data directory must still reject rights its children could inherit.
        with self.assertRaises(WindowsMaintenanceError):
            _validate_descriptor(
                OPERATOR,
                (inherited_for_children,),
                OPERATOR,
                SYSTEM,
                ADMINS,
                INSTALLER,
                role="data",
                directory=True,
            )


if __name__ == "__main__":
    unittest.main()
