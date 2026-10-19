"""Repository-relative documentation links in isolated checkouts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_doc_links import check


class DocumentationLinkTests(unittest.TestCase):
    def test_private_named_checkout_ancestor_does_not_hide_valid_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".architecture-refactor" / "checkout"
            (root / "docs").mkdir(parents=True)
            (root / "README.md").write_text(
                "[guide](docs/guide.md)\n", encoding="utf-8"
            )
            (root / "docs/guide.md").write_text(
                "[home](../README.md)\n", encoding="utf-8"
            )

            self.assertEqual(check(root), [])

    def test_private_target_inside_checkout_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            (root / ".architecture-refactor").mkdir(parents=True)
            (root / ".architecture-refactor/secret.md").write_text(
                "private\n", encoding="utf-8"
            )
            (root / "README.md").write_text(
                "[private](.architecture-refactor/secret.md)\n", encoding="utf-8"
            )

            self.assertEqual(
                check(root), ["README.md: .architecture-refactor/secret.md"]
            )

    def test_target_outside_checkout_is_rejected_even_when_present(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            root.mkdir()
            (root.parent / "outside.md").write_text("outside\n", encoding="utf-8")
            (root / "README.md").write_text(
                "[outside](../outside.md)\n", encoding="utf-8"
            )

            self.assertEqual(check(root), ["README.md: ../outside.md"])


if __name__ == "__main__":
    unittest.main()
