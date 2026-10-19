"""Native private-key file safety; all material is synthetic and temporary."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.infrastructure.managed_key_file import (
    ManagedKeyFileError,
    ManagedKeyFiles,
)
from ygl_test_subject.infrastructure.windows.maintenance import _Ace
from ygl_test_subject.infrastructure.windows.managed_key_io import _edge_acl


class _DescriptorNative:
    def __init__(self, owner: bytes, aces: tuple[_Ace, ...]):
        self.owner = owner
        self.aces = aces

    def descriptor(self, _handle):
        return self.owner, self.aces

    def _fixed_sid(self, value):
        if value == "S-1-3-4":
            return bytes.fromhex("010100000000000304000000")
        return value.encode("ascii")


class ManagedKeyAclTests(unittest.TestCase):
    def test_owner_rights_is_resolved_only_for_trusted_owner(self):
        operator = b"operator"
        trusted = (b"system", b"administrators", b"trusted-installer")
        owner_ace = _Ace(0, 0, 0x001F01FF, bytes.fromhex("010100000000000304000000"))
        _edge_acl(
            _DescriptorNative(operator, (owner_ace,)),
            object(),
            operator,
            trusted,
            parent=True,
        )
        with self.assertRaises(ManagedKeyFileError):
            _edge_acl(
                _DescriptorNative(b"outsider", (owner_ace,)),
                object(),
                operator,
                trusted,
                parent=True,
            )

    def test_effective_outsider_replace_rights_are_rejected(self):
        operator = b"operator"
        trusted = (b"system", b"administrators", b"trusted-installer")
        outsider = b"authenticated-users"
        with self.assertRaises(ManagedKeyFileError):
            _edge_acl(
                _DescriptorNative(operator, (_Ace(0, 0, 0x40, outsider),)),
                object(),
                operator,
                trusted,
                parent=True,
            )
        with self.assertRaises(ManagedKeyFileError):
            _edge_acl(
                _DescriptorNative(operator, (_Ace(0, 0, 0x10000, outsider),)),
                object(),
                operator,
                trusted,
                parent=False,
            )
        # Inherit-only rights do not apply to the ancestor itself. Private
        # data-root validation separately checks inherited child exposure.
        _edge_acl(
            _DescriptorNative(operator, (_Ace(0, 0x08, 0x40, outsider),)),
            object(),
            operator,
            trusted,
            parent=True,
        )


class ManagedKeyFilesTests(unittest.TestCase):
    def setUp(self):
        secure_parent = os.environ.get("YGL_MANAGED_KEY_TEST_ROOT")
        self.temp = tempfile.TemporaryDirectory(
            prefix="ygl-key-file-", dir=secure_parent
        )
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.store = ManagedKeyFiles(self.data)
        self.first = "a" * 32
        self.second = "b" * 32

    def test_exclusive_create_durable_read_and_no_overwrite(self):
        self.assertEqual(self.store.entries(), ())
        self.store.create(self.first, bytes(range(32)))
        self.assertEqual(self.store.read(self.first), bytes(range(32)))
        self.assertEqual(self.store.confirm_durable(self.first), bytes(range(32)))
        self.assertEqual(self.store.entries(), (f"master-{self.first}.key",))
        with self.assertRaises(ManagedKeyFileError) as caught:
            self.store.create(self.first, b"z" * 32)
        self.assertEqual(caught.exception.code, "key_file_exists")
        self.assertEqual(self.store.read(self.first), bytes(range(32)))
        self.assertEqual(self.store.confirm_durable(self.first), bytes(range(32)))
        self.store.create(self.second, b"z" * 32)
        self.assertEqual(self.store.read(self.second), b"z" * 32)

    def test_invalid_names_and_lengths_never_create(self):
        for identifier in ("../escape", "A" * 32, "", "a" * 31):
            with (
                self.subTest(identifier=identifier),
                self.assertRaises(ManagedKeyFileError),
            ):
                self.store.create(identifier, b"x" * 32)
        with self.assertRaises(ManagedKeyFileError):
            self.store.create(self.first, b"short")
        self.assertEqual(self.store.entries(), ())

    def test_truncated_file_and_hardlink_are_rejected_without_replacement(self):
        self.store.create(self.first, b"s" * 32)
        original = self.store.root / f"master-{self.first}.key"
        link = self.store.root / "other.key"
        os.link(original, link)
        with self.assertRaises(ManagedKeyFileError):
            self.store.read(self.first)
        with self.assertRaises(ManagedKeyFileError):
            self.store.confirm_durable(self.first)
        link.unlink()
        self.assertEqual(self.store.read(self.first), b"s" * 32)
        # A candidate interrupted before a complete write must not be adopted.
        (self.store.root / f"master-{self.second}.key").write_bytes(b"YGLK\x01")
        with self.assertRaises(ManagedKeyFileError):
            self.store.read(self.second)
        with self.assertRaises(ManagedKeyFileError):
            self.store.confirm_durable(self.second)

    @unittest.skipIf(os.name == "nt", "POSIX directory fsync injection")
    def test_existing_keys_directory_rechecks_parent_fsync(self):
        original_fsync = os.fsync
        parent_identity = (self.data.stat().st_dev, self.data.stat().st_ino)

        def fail_parent(handle):
            info = os.fstat(handle)
            if (info.st_dev, info.st_ino) == parent_identity:
                raise OSError("synthetic parent sync failure")
            return original_fsync(handle)

        with patch("os.fsync", side_effect=fail_parent):
            with self.assertRaises(ManagedKeyFileError):
                self.store.create(self.first, b"x" * 32)
        self.assertTrue(self.store.root.is_dir())
        with patch("os.fsync", side_effect=fail_parent):
            with self.assertRaises(ManagedKeyFileError):
                self.store.create(self.first, b"x" * 32)
        self.store.create(self.first, b"x" * 32)
        with patch("os.fsync", side_effect=fail_parent):
            with self.assertRaises(ManagedKeyFileError):
                self.store.confirm_durable(self.first)
        self.assertEqual(self.store.confirm_durable(self.first), b"x" * 32)

    def test_private_directory_and_existing_link_rejected(self):
        external = self.data / "external"
        external.mkdir()
        try:
            (self.data / "keys").symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("native symlink creation unavailable")
        with self.assertRaises(ManagedKeyFileError):
            self.store.create(self.first, b"s" * 32)
        self.assertFalse((external / f"master-{self.first}.key").exists())


if __name__ == "__main__":
    unittest.main()
