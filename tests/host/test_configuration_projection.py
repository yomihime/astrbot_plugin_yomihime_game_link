"""Native schema and constructor snapshot validation agreement."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from ygl_test_subject.adapters.astrbot.web_public import (
    WebPublicRejected,
    normalize_origin,
    origin_configuration,
)
from ygl_test_subject.modules.ff14.config import (
    FF14ConfigSnapshot,
)

from tests.host.astrbot_contract import host_plugin_config_display


class ConfigurationProjectionTests(unittest.TestCase):
    def test_web_origin_is_separate_and_never_disables_ordinary_config(self):
        for value in (
            "",
            "https://User:private@example.test",
            True,
            "https://example.test/query",
        ):
            config = {"web_public_origin": value, "ff14_calendar_default_days": 3}
            state, origin = origin_configuration(config)
            self.assertEqual(state, "unconfigured" if value == "" else "invalid")
            self.assertIsNone(origin)

    def test_single_origin_normalization_and_forbidden_components(self):
        for value, expected in (
            ("HTTPS://Example.TEST:443/", "https://example.test"),
            ("http://localhost:80", "http://localhost"),
            ("http://[::1]:7890/", "http://[::1]:7890"),
        ):
            self.assertEqual(normalize_origin(value), expected)
        for value in (
            "https://a.test/?",
            "https://a.test/#",
            "https://a.test/path",
            "https://u@a.test",
            "https://*.test",
            "null",
            "https://a.test https://b.test",
            " https://a.test",
            "https://a.test:0",
            "https://a.test\\evil",
            "file://a.test",
            "https://a.test/%2f",
        ):
            with self.subTest(value=value), self.assertRaises(WebPublicRejected):
                normalize_origin(value)

    def test_host_schema_has_only_origin_and_current_module_defaults_agree(self):
        from ygl_test_subject.core.contracts.validation_boundary import (
            validate_contract,
        )
        from ygl_test_subject.extensions.disk_manifest import parse_manifest
        from ygl_test_subject.modules.ff14.config import CALENDAR_CONFIG_FIELDS

        root = Path(__file__).resolve().parents[2]
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema), {"web_public_origin"})
        self.assertEqual(schema["web_public_origin"]["default"], "")
        self.assertEqual(schema["web_public_origin"]["type"], "string")
        display = host_plugin_config_display(SimpleNamespace(schema=schema))
        items = display["metadata"]["game_link"]["items"]
        self.assertEqual(set(items), {"web_public_origin"})
        self.assertFalse(items["web_public_origin"].get("invisible", False))
        self.assertIn("重载", schema["web_public_origin"]["hint"])
        manifest = parse_manifest(
            (root / "modules/ff14/yomihime.manifest.json").read_bytes()
        )
        declarations = {
            field.name: field for field in manifest.modules[0].config_fields
        }
        snapshot = dict(FF14ConfigSnapshot().as_values())
        for field in CALENDAR_CONFIG_FIELDS:
            self.assertEqual(declarations[field.name], validate_contract(field))
            self.assertEqual(snapshot[field.name], field.default)
