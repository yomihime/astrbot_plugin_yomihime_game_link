"""Real SQLite approvals and captured page bytes; synthetic Host metadata."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ygl_test_subject.extensions.discovery import discover_packages
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.services.external_installations import ExternalInstallationTrust
from ygl_test_subject.services.external_source_policies import ExternalSourcePolicies

from tests.services.test_external_source_policies import (
    selected_policy_bytes,
    source_policy_fixture,
)
from yomihime_game_link_sdk.errors import RevisionConflict
from yomihime_game_link_sdk.host import ExtensionHostError

ROOT = Path(__file__).resolve().parents[2]


class InstallationTrustTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_configuration_expansion_is_not_an_ordinary_ui_update(self):
        module = self.manifest["modules"][0]
        module["sources"][0]["requests_per_hour"] = 100
        module["sources"][0]["request_budget_config"] = "request_quota"
        module["config_fields"].append(
            {
                "name": "request_quota",
                "default": 100,
                "value_schema": {"type": "integer", "minimum": 1, "maximum": 1000},
            }
        )
        self.write()
        await self.approve()
        module["config_fields"][-1]["description"] = "New label"
        self.write()
        self.assertTrue(self.trust.policies.permits(self.discovered()))
        module["config_fields"][-1]["value_schema"]["maximum"] = 10000
        self.write()
        self.assertFalse(self.trust.policies.permits(self.discovered()))

    async def asyncSetUp(self):
        base = ROOT / ".local-dev" / "test-cache"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="installation-trust-", dir=base)
        self.root = Path(self.temp.name)
        self.plugin = self.root / "plugin"
        self.package = self.plugin / "packages" / "minimal"
        (self.package / "pages").mkdir(parents=True)
        (self.package / "pages/entry.js").write_bytes(
            (ROOT / "examples/modules/minimal/pages/entry.js").read_bytes()
        )
        self.manifest, self.policy = source_policy_fixture()
        self.write()
        self.instance = object()
        self.metadata = SimpleNamespace(
            name="example_plugin",
            activated=True,
            star_cls=self.instance,
            module=SimpleNamespace(__file__=str(self.plugin / "main.py")),
        )
        self.context = SimpleNamespace(
            get_registered_star=lambda name: self.metadata
            if name == "example_plugin"
            else None
        )
        self.database = SQLiteDatabase(self.root / "state.sqlite3")
        self.trust = ExternalInstallationTrust(self.database, self.context)
        await self.trust.load()

    async def asyncTearDown(self):
        await self.database.executor.close()
        self.temp.cleanup()

    def write(self):
        raw = json.dumps(self.manifest, ensure_ascii=False).encode()
        (self.package / "yomihime.manifest.json").write_bytes(raw)
        (self.package / "yomihime.source-policy.json").write_text(
            json.dumps(self.policy), encoding="utf-8"
        )
        (self.package / "module.py").write_text(
            "raise RuntimeError('test must never import plugin')", encoding="utf-8"
        )
        return raw

    def discovered(self):
        packages = tuple(discover_packages(self.package.parent))
        self.assertEqual(len(packages), 1)
        self.assertTrue(packages[0].valid, packages[0].diagnostic)
        return packages[0]

    async def approve(self):
        plan = await self.trust.plan("example_plugin", "minimal")
        result = await self.trust.approve(
            "example_plugin",
            "minimal",
            plan_id=plan["plan_id"],
            expected_revision=plan["expected_revision"],
            actor="verified-test-admin",
            current=lambda: None,
        )
        await self.trust.load()
        return result

    async def test_default_core_requires_approval_then_content_only_update_survives_reopen(
        self,
    ):
        self.assertFalse(self.trust.policies.permits(self.discovered()))
        result = await self.approve()
        self.assertEqual(result["revision"], 1)
        await self.trust.bind("example_plugin", self.package, self.discovered())
        self.manifest["package_version"] = "2.0.0"
        self.manifest["modules"][0]["module_version"] = "2.0.0"
        code = b"export function mount(){return {dispose(){},update(){}}} // v2"
        (self.package / "pages/entry.js").write_bytes(code)
        self.manifest["modules"][0]["resources"][0]["sha256"] = hashlib.sha256(
            code
        ).hexdigest()
        self.write()
        restarted = ExternalInstallationTrust(self.database, self.context)
        await restarted.load()
        self.assertTrue(restarted.policies.permits(self.discovered()))
        await restarted.bind("example_plugin", self.package, self.discovered())
        self.manifest["modules"][0]["sources"][0]["host"] = "unapproved.example.test"
        self.write()
        self.assertFalse(restarted.policies.permits(self.discovered()))
        with self.assertRaises(ExtensionHostError):
            await restarted.bind("example_plugin", self.package, self.discovered())

    async def test_approval_is_cas_and_stale_policy_is_rejected(self):
        plan = await self.trust.plan("example_plugin", "minimal")
        changed = copy.deepcopy(self.policy)
        changed["credentials"][0]["description"] += " reviewed change"
        (self.package / "yomihime.source-policy.json").write_text(
            json.dumps(changed), encoding="utf-8"
        )
        with self.assertRaises(RevisionConflict):
            await self.trust.approve(
                "example_plugin",
                "minimal",
                plan_id=plan["plan_id"],
                expected_revision=0,
                actor="verified-test-admin",
                current=lambda: None,
            )
        result = await self.approve()
        with self.assertRaises(RevisionConflict):
            await self.trust.approve(
                "example_plugin",
                "minimal",
                plan_id=plan["plan_id"],
                expected_revision=0,
                actor="verified-test-admin",
                current=lambda: None,
            )
        self.assertEqual(result["revision"], 1)

    async def test_no_module_self_approval_and_exact_owner_root_fence(self):
        with self.assertRaises(PermissionError):
            await self.trust.approve(
                "example_plugin",
                "minimal",
                plan_id="x",
                expected_revision=0,
                actor="test",
                current=lambda: (_ for _ in ()).throw(PermissionError()),
            )
        await self.approve()
        with self.assertRaises(ExtensionHostError):
            await self.trust.bind("other_plugin", self.package, self.discovered())
        self.metadata.activated = False
        with self.assertRaises(ExtensionHostError):
            await self.trust.plan("example_plugin", "minimal")

    async def test_build_policy_migration_is_audited_without_config_or_secret_changes(
        self,
    ):
        build = ExternalSourcePolicies(selected_policy_bytes(self.write(), self.policy))
        trust = ExternalInstallationTrust(self.database, self.context, build)
        await trust.load()
        await trust.bind("example_plugin", self.package, self.discovered())

        def read(unit):
            row = unit.execute(
                "SELECT action,revision FROM extension_installation_audit"
            ).fetchone()
            return dict(row)

        row = await self.database.executor.run_transaction(read)
        self.assertEqual(
            row, {"action": "equivalent_build_policy_migration", "revision": 1}
        )

    async def test_explicit_generic_integer_floor_migration_is_atomic_and_keeps_finite_1000(
        self,
    ):
        self.manifest["modules"][0]["config_fields"].append(
            {
                "name": "card_limit",
                "default": 500,
                "value_schema": {"type": "integer", "minimum": 20},
            }
        )
        self.policy["config_migrations"] = [
            {
                "owner": "minimal/demo",
                "field": "card_limit",
                "kind": "integer_floor",
                "from_minimum": 1,
                "to_minimum": 20,
            }
        ]
        self.write()
        await self.approve()
        for value in (1, 19, 20, 500, 1000):
            principal = "config-case-" + str(value)
            self.trust.config_principal_id = principal

            def seed(unit):
                unit.execute(
                    "INSERT INTO config_state VALUES(?,?,?)",
                    (principal, "minimal/demo", 7),
                )
                unit.execute(
                    "INSERT INTO config_entries(principal_id,module_id,field,value_json,revision) VALUES(?,?,?,?,?)",
                    (principal, "minimal/demo", "card_limit", str(value), 5),
                )

            await self.database.executor.run_transaction(seed)
            await self.trust.bind(
                "example_plugin", self.package, self.discovered(), current=lambda: True
            )

            def read(unit):
                row = unit.execute(
                    "SELECT value_json,revision FROM config_entries WHERE principal_id=?",
                    (principal,),
                ).fetchone()
                return dict(row)

            row = await self.database.executor.run_transaction(read)
            self.assertEqual(json.loads(row["value_json"]), max(20, value))
            self.assertEqual(row["revision"], 8 if value < 20 else 5)
