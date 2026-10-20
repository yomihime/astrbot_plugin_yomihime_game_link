"""Production dependency floors must coexist with a newer Host environment."""

import unittest
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


class RuntimeDependencyTests(unittest.TestCase):
    def test_only_runtime_minima_and_no_unjustified_upper_or_exact_pins(self):
        expected = {
            "aiohttp": "3.13.2",
            "yarl": "1.22.0",
            "cryptography": "46.0.3",
            "icalendar": "7.3.0",
            "python-dateutil": "2.9.0.post0",
            "tzdata": "2026.4",
            "Pillow": "12.3.0",
            "resvg_py": "0.5.0",
        }
        requirements = [
            Requirement(line)
            for line in (ROOT / "requirements.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertEqual(len(requirements), len(expected))
        self.assertEqual({item.name for item in requirements}, set(expected))
        for requirement in requirements:
            with self.subTest(package=requirement.name):
                floor = expected[requirement.name]
                self.assertEqual(str(requirement.specifier), f">={floor}")
                self.assertIsNone(requirement.url)
                self.assertIsNone(requirement.marker)
                self.assertFalse(requirement.extras)
                self.assertIn(Version(floor), requirement.specifier)
                self.assertIn(Version("9999.0"), requirement.specifier)
                self.assertNotIn(Version("0.0.1"), requirement.specifier)
        aiohttp = next(item for item in requirements if item.name == "aiohttp")
        self.assertIn(Version("3.14.5"), aiohttp.specifier)
