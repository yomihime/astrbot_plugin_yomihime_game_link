"""Developer font preparation verifies content and never replaces invalid input."""

from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.prepare_test_font import prepare


class FontFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = b"synthetic-font-fixture"
        (self.root / "presentation").mkdir()
        (self.root / "services").mkdir()
        (self.root / "presentation/bundled_font.py").write_text(
            f"FONT_SIZE = {len(self.payload)}\n"
            f"FONT_SHA256 = '{hashlib.sha256(self.payload).hexdigest()}'\n"
            "FONT_FILENAME = 'fixture.otf'\n"
        )
        (self.root / "services/managed_font.py").write_text(
            "FONT_URL = 'https://fonts.example.test/pinned.otf'\n"
        )
        self.destination = self.root / "presentation/assets/fonts/fixture.otf"

    def test_verified_download_and_cached_read(self):
        with patch(
            "urllib.request.urlopen", return_value=io.BytesIO(self.payload)
        ) as fetch:
            self.assertEqual(prepare(self.root), self.destination)
            fetch.assert_called_once_with(
                "https://fonts.example.test/pinned.otf", timeout=45
            )
        with patch(
            "urllib.request.urlopen", side_effect=AssertionError("must not fetch")
        ):
            self.assertEqual(prepare(self.root).read_bytes(), self.payload)

    def test_invalid_existing_fixture_is_not_overwritten(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"existing-invalid")
        with patch(
            "urllib.request.urlopen", side_effect=AssertionError("must not fetch")
        ):
            with self.assertRaisesRegex(ValueError, "not replaced"):
                prepare(self.root)
        self.assertEqual(self.destination.read_bytes(), b"existing-invalid")

    def test_wrong_or_oversized_download_leaves_no_fixture(self):
        for payload in (b"wrong", self.payload + b"extra"):
            with self.subTest(payload=payload):
                with patch("urllib.request.urlopen", return_value=io.BytesIO(payload)):
                    with self.assertRaisesRegex(ValueError, "integrity"):
                        prepare(self.root)
                self.assertFalse(self.destination.exists())
