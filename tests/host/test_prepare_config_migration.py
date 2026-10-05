import json
import tempfile
import unittest
from pathlib import Path

from ygl_test_subject.scripts.prepare_ff14_config_migration import (
    load_prepared_input,
    prepare,
)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        self.source = self.root / "old.json"
        self.output = self.root / "prepared.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_raw_presence_null_filter_digest_and_immutable_output(self):
        self.source.write_text(
            json.dumps(
                {
                    "ff14_default_region": None,
                    "web_public_origin": "private",
                    "secret": "must-not-copy",
                }
            ),
            encoding="utf-8",
        )
        original = self.source.read_bytes()
        prepare(self.source, self.output)
        self.assertEqual(
            load_prepared_input(self.output), {"ff14_default_region": None}
        )
        self.assertNotIn(b"must-not-copy", self.output.read_bytes())
        self.assertNotIn(b"private", self.output.read_bytes())
        self.assertEqual(self.source.read_bytes(), original)
        with self.assertRaises(ValueError):
            prepare(self.source, self.output)
        with self.assertRaises(ValueError):
            prepare(self.source, self.source)

    def test_duplicate_keys_nonfinite_wrong_binding_tamper_rejected(self):
        for raw in (
            '{"ff14_default_region":"cn","ff14_default_region":"global"}',
            '{"nested":{"a":1,"a":2}}',
            '{"ff14_calendar_default_days":NaN}',
        ):
            self.source.write_text(raw, encoding="utf-8")
            with self.assertRaises(ValueError):
                prepare(self.source, self.output)
            self.assertFalse(self.output.exists())
        self.source.write_text("{}", encoding="utf-8")
        prepare(self.source, self.output)
        payload = json.loads(self.output.read_text(encoding="utf-8"))
        payload["fields"]["ff14_default_region"] = "global"
        self.output.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "digest"):
            load_prepared_input(self.output)

    def test_missing_preparation_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "stop the instance"):
            load_prepared_input(self.output)
        self.output.write_bytes(b'{"schema_version":')
        with self.assertRaisesRegex(ValueError, "prepare a new snapshot"):
            load_prepared_input(self.output)

    def test_actual_host_method_hydrates_missing_null_but_preparation_preserves_presence(
        self,
    ):
        from tests.host.astrbot_contract import host_config_integrity

        host = host_config_integrity()
        defaults = {"ff14_default_region": "cn", "ff14_calendar_default_days": 7}
        for raw in (
            {},
            {"ff14_default_region": None},
            {"ff14_default_region": "global"},
        ):
            self.source.write_text(json.dumps(raw), encoding="utf-8")
            prepare(self.source, self.output)
            hydrated = dict(raw)
            host.check_config_integrity(defaults, hydrated)
            self.assertEqual(load_prepared_input(self.output), raw)
            self.assertEqual(hydrated["ff14_calendar_default_days"], 7)
            self.assertEqual(
                hydrated["ff14_default_region"],
                "global" if raw.get("ff14_default_region") == "global" else "cn",
            )
            self.output.unlink()
