"""Deterministic FF14 display labels; synthetic source identities only."""

import unittest

from modules.ff14.features.fflogs_jobs import JOBS, job_display, job_identity


class JobDisplayTests(unittest.TestCase):
    def test_every_known_job_has_separate_identity_and_three_display_forms(self):
        self.assertEqual(len({job.key for job in JOBS}), len(JOBS))
        for job in JOBS:
            with self.subTest(job=job.key):
                self.assertEqual(job_display(job.source_name), job.zh)
                self.assertEqual(job_display(job.source_name, "ja"), job.ja)
                self.assertEqual(job_display(job.source_name, "en"), job.abbreviation)
                self.assertRegex(job.abbreviation, r"^[A-Z]{3}$")
                for source in (
                    job.key,
                    job.source_name,
                    job.abbreviation,
                    job.zh,
                    job.ja,
                ):
                    self.assertIs(job_identity(source), job)

    def test_fflogs_compact_and_spaced_names_resolve_exactly(self):
        for source, expected in (
            ("Scholar", "学者"),
            ("Astrologian", "占星术士"),
            ("Sage", "贤者"),
            ("DarkKnight", "暗黑骑士"),
            ("Dark Knight", "暗黑骑士"),
            ("White Mage", "白魔法师"),
        ):
            self.assertEqual(job_display(source), expected)
        self.assertEqual(job_display("Gunbreaker", "ja"), "ガンブレイカー")
        self.assertEqual(job_display("Pictomancer", "ja"), "ピクトマンサー")
        self.assertEqual(len(job_display("Gunbreaker", "ja")), 7)
        self.assertEqual(len(job_display("Pictomancer", "ja")), 7)

    def test_unknown_is_preserved_without_guessing_and_missing_is_distinct(self):
        for source in (
            "Future very long unknown source job name",
            "Scholar II",
            "未知职业",
        ):
            for language in ("zh", "ja", "en"):
                self.assertEqual(job_display(source, language), source)
                self.assertIsNone(job_identity(source))
        self.assertEqual(job_display(None), "—")
        self.assertEqual(job_display(""), "—")
        with self.assertRaises(ValueError):
            job_display("Scholar", "xx")


if __name__ == "__main__":
    unittest.main()
