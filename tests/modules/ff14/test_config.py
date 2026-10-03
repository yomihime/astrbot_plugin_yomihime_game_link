"""Ordinary default validation and immutable module snapshot contract."""

import unittest
from dataclasses import FrozenInstanceError

from modules.ff14.config import (
    DAYS,
    DELIVERY_TIME,
    REGION,
    TIMEZONE,
    FF14ConfigError,
    FF14ConfigSnapshot,
)


class FF14ConfigTests(unittest.TestCase):
    def test_missing_legacy_fields_and_boundaries(self):
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
