"""Fixed Host schema hydration preserves raw migration and rollback inputs."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ygl_test_subject.scripts.prepare_ff14_config_migration import (
    load_prepared_input,
    prepare,
)

from tests.host.astrbot_contract import (
    host_config_integrity,
    host_plugin_config_display,
    validate_host_source,
)
from tests.services import test_configuration_migration as migration_fixtures

ROOT = Path(__file__).resolve().parents[2]
LEGACY = {
    "ff14_default_region": "global",
    "ff14_calendar_default_days": 3,
    "ff14_calendar_default_timezone": "UTC",
    "ff14_calendar_default_delivery_time": "09:15",
}


class HiddenLegacySchemaTests(unittest.TestCase):
    def test_fixed_host_vue_namespace_and_regular_field_invisibility(self):
        _, sources = validate_host_source()
        renderer = sources["dashboard/src/components/shared/AstrBotConfig.vue"]
        page = sources["dashboard/src/views/ExtensionPage.vue"]
        self.assertIn(
            "!metadata[metadataKey].items[key]?.invisible && shouldShowItem", renderer
        )
        self.assertIn(':metadataKey="curr_namespace"', page)
        self.assertIn(':metadata="extension_config.metadata"', page)
        self.assertIn(':iterable="extension_config.config"', page)

    def test_fixed_host_wrapper_hides_four_fields_and_keeps_origin(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        config = SimpleNamespace(schema=schema)
        result = host_plugin_config_display(config)
        items = result["metadata"]["game_link"]["items"]
        self.assertEqual(result["metadata"]["game_link"]["type"], "object")
        self.assertIs(result["config"], config)
        self.assertEqual(
            {key for key, item in items.items() if item.get("invisible") is True},
            set(LEGACY),
        )
        self.assertFalse(items["web_public_origin"].get("invisible", False))
        self.assertEqual(
            {key: items[key]["default"] for key in LEGACY},
            {
                "ff14_default_region": "cn",
                "ff14_calendar_default_days": 7,
                "ff14_calendar_default_timezone": "Asia/Shanghai",
                "ff14_calendar_default_delivery_time": "08:00",
            },
        )

    def test_fixed_host_integrity_retains_hidden_values_deleting_schema_loses_them(
        self,
    ):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        defaults = {key: item["default"] for key, item in schema.items()}
        host = host_config_integrity()
        retained = {**LEGACY, "web_public_origin": ""}
        host.check_config_integrity(defaults, retained)
        self.assertEqual(retained, {**LEGACY, "web_public_origin": ""})
        deleted = dict(LEGACY)
        host.check_config_integrity({"web_public_origin": ""}, deleted)
        self.assertEqual(deleted, {"web_public_origin": ""})


class HiddenLegacyUpgradeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = migration_fixtures.MigrationTests()
        await self.fixture.asyncSetUp()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()
        self.temp.cleanup()

    def prepared(self, raw):
        source, output = self.root / "raw.json", self.root / "prepared.json"
        source.write_text(json.dumps(raw), encoding="utf-8")
        prepare(source, output)
        hydrated = dict(raw)
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        host_config_integrity().check_config_integrity(
            {k: v["default"] for k, v in schema.items()}, hydrated
        )
        prepared = load_prepared_input(output)
        self.assertEqual(prepared, raw)
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), raw)
        return prepared, hydrated

    async def test_new_install_preparation_uses_absence_before_host_hydration(self):
        raw, hydrated = self.prepared({})
        self.assertEqual(set(hydrated), {*LEGACY, "web_public_origin"})
        diagnostics = await self.fixture.migration.migrate(raw)
        self.assertTrue(all(item["origin"] == "default" for item in diagnostics))
        self.assertTrue(await self.fixture.migration.complete())

    async def test_unmigrated_values_survive_hidden_host_form_and_import(self):
        raw, hydrated = self.prepared(LEGACY)
        self.assertEqual({k: hydrated[k] for k in LEGACY}, LEGACY)
        diagnostics = await self.fixture.migration.migrate(raw)
        self.assertTrue(all(item["origin"] == "legacy" for item in diagnostics))
        current = await self.fixture.snapshots()
        self.assertEqual(current[self.fixture.core].values["default_region"], "global")
        self.assertEqual(
            current[self.fixture.ff14].values["ff14_calendar_default_days"], 3
        )

    async def test_completed_marker_ignores_hidden_legacy_values_without_double_write(
        self,
    ):
        raw, _ = self.prepared(LEGACY)
        await self.fixture.migration.migrate(raw)
        before = await self.fixture.snapshots()
        self.assertEqual(
            await self.fixture.migration.migrate({"ff14_default_region": "cn"}), ()
        )
        self.assertEqual(await self.fixture.snapshots(), before)

    async def test_limited_rollback_returns_exact_raw_presence_with_cas(self):
        raw, _ = self.prepared({"ff14_default_region": "global"})
        await self.fixture.migration.migrate(raw)
        current = await self.fixture.snapshots()
        returned = await self.fixture.migration.rollback(
            {t: s.revision for t, s in current.items()}
        )
        self.assertEqual(returned, raw)
        self.assertNotIn("ff14_calendar_default_days", returned)
        self.assertFalse(await self.fixture.migration.complete())
