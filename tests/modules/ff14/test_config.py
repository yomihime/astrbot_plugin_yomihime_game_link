"""Ordinary default validation and immutable module snapshot contract."""

import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from modules.ff14.config import (
    CALENDAR_CONFIG_FIELDS,
    DAYS,
    DELIVERY_TIME,
    REGION,
    SYNC_INTERVAL,
    TIMEZONE,
    FF14ConfigError,
    FF14ConfigSnapshot,
)


class FF14ConfigTests(unittest.TestCase):
    def test_default_values_and_boundaries(self):
        self.assertEqual(FF14ConfigSnapshot.from_values({}), FF14ConfigSnapshot())
        for days in (1, 30):
            snapshot = FF14ConfigSnapshot.from_values(
                {REGION: "global", DAYS: days, TIMEZONE: "UTC", DELIVERY_TIME: "23:59"}
            )
            self.assertEqual(snapshot.calendar_default_days, days)
            self.assertEqual(snapshot.default_region, "global")

    def test_invalid_inputs_report_only_bounded_field(self):
        cases = {
            REGION: (None, True, 1, "CN", "other", "private-input"),
            DAYS: (None, True, 0, 31, 7.0, "7"),
            TIMEZONE: (None, 1, "", "Unknown/Timezone", "../secret", "UTC\n"),
            DELIVERY_TIME: (None, 800, "8:00", "24:00", "00:60", "08:00\n"),
            SYNC_INTERVAL: (None, True, False, 900, 0, 21601, 21600.0, "21600"),
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    with self.assertRaises(FF14ConfigError) as caught:
                        FF14ConfigSnapshot.from_values({key: value})
                    self.assertEqual(str(caught.exception), key)

    def test_snapshot_copies_and_freezes_input(self):
        values = {DAYS: 3}
        snapshot = FF14ConfigSnapshot.from_values(values)
        values[DAYS] = 20
        self.assertEqual(snapshot.calendar_default_days, 3)
        with self.assertRaises(FrozenInstanceError):
            snapshot.calendar_default_days = 20
        with self.assertRaises(TypeError):
            snapshot.as_values()[DAYS] = 20

    def test_combined_sdk_view_does_not_consume_core_secret_fields(self):
        snapshot = FF14ConfigSnapshot.from_values(
            {"credential_fflogs_cn": None, "core_only": True, DAYS: 3}
        )
        self.assertEqual(snapshot.calendar_default_days, 3)
        self.assertNotIn("credential_fflogs_cn", snapshot.as_values())

    def test_sync_interval_defaults_and_allowed_values_round_trip(self):
        self.assertEqual(FF14ConfigSnapshot().calendar_sync_interval_seconds, 21600)
        self.assertEqual(FF14ConfigSnapshot().as_values()[SYNC_INTERVAL], 21600)
        for seconds in (21600, 43200, 86400):
            with self.subTest(seconds=seconds):
                snapshot = FF14ConfigSnapshot.from_values({SYNC_INTERVAL: seconds})
                self.assertEqual(snapshot.calendar_sync_interval_seconds, seconds)
                self.assertEqual(snapshot.as_values()[SYNC_INTERVAL], seconds)
                self.assertEqual(
                    FF14ConfigSnapshot.from_values(snapshot.as_values()), snapshot
                )

    def test_sync_interval_declaration_matches_manifest(self):
        manifest_path = (
            Path(__file__).parents[3] / "modules/ff14/yomihime.manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        field = next(
            field for field in CALENDAR_CONFIG_FIELDS if field.name == SYNC_INTERVAL
        )
        declared = next(
            field
            for field in manifest["modules"][0]["config_fields"]
            if field["name"] == SYNC_INTERVAL
        )
        self.assertEqual(field.default, 21600)
        self.assertEqual(field.description, "日历同步周期")
        self.assertEqual(field.group, "calendar")
        self.assertEqual(declared["default"], field.default)
        self.assertEqual(declared["description"], field.description)
        self.assertEqual(declared["group"], field.group)
        self.assertEqual(
            declared["value_schema"], {"type": "integer", "enum": [21600, 43200, 86400]}
        )
        self.assertEqual(dict(field.value_schema), declared["value_schema"])
