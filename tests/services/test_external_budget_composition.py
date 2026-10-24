"""Build-selected external timeout declarations reach the real Core constructor.

Only inert manifests are loaded; a timeout projection never installs a module.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.extensions.disk_manifest import parse_manifest
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.services.core_runtime import CoreRuntime
from ygl_test_subject.services.external_source_policies import (
    POLICY_FILE,
    ExternalSourcePolicies,
)

from tests.fixtures.external_page_catalog import external_catalog_fixture
from tests.services.test_core_runtime import _MessagePort, _Renderer, _TestCodec
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.declarations import (
    CapabilityEffect,
    InvocationPolicy,
    PrivacyFloor,
)
from yomihime_game_link_sdk.display import DisplayLimits

ROOT = Path(__file__).resolve().parents[2]
OWNER = "minimal/demo"
TARGET = (OWNER, "status")


class ExternalBudgetCompositionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="external-budget-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        projection, _, _ = external_catalog_fixture(with_logo=False)
        self.raw = projection.packages["minimal"][2]
        self.manifest = json.loads(self.raw)
        self.declaration = parse_manifest(self.raw).modules[0]
        self.runtime_count = 0

    def selected(self, *, timeouts=None, manifest=None):
        package = {
            "manifest": self.raw.decode() if manifest is None else json.dumps(manifest),
            "credentials": [],
            "authorizations": {},
        }
        if timeouts is not None:
            package["capability_timeouts"] = timeouts
        (self.root / POLICY_FILE).write_text(
            json.dumps({"schema_version": 1, "packages": [package]}), encoding="utf-8"
        )
        return ExternalSourcePolicies.load(self.root)

    def runtime(self, **overrides):
        self.runtime_count += 1
        root = self.root / str(self.runtime_count)
        root.mkdir()
        (root / "extensions").mkdir()
        database = SQLiteDatabase(root / "core.sqlite3")
        self.addAsyncCleanup(database.executor.close, timeout=2)
        runtime = CoreRuntime(
            database=database,
            extension_root=root / "extensions",
            file_root=root / "files",
            secret_root=root / "secrets",
            secret_codec=_TestCodec(),
            http_transport=lambda request: request,
            renderer=_Renderer(),
            display_limits=DisplayLimits(2, 4096),
            message_port=_MessagePort(),
            admin_context_validator=lambda *_args: False,
            host_ingress_validator=lambda *_args: False,
            config_principal_id="synthetic-host",
            identity_namespace="synthetic-identities",
            **overrides,
        )
        self.addAsyncCleanup(runtime.close, timeout=2)
        return runtime

    def seconds(self, runtime, target=TARGET):
        return runtime._budget_policy.seconds(
            InvocationOrigin.WEB_PUBLIC,
            30,
            module_id=target[0],
            capability_id=target[1],
        )

    async def test_real_loader_projects_only_declared_owners_as_immutable_manifests(
        self,
    ):
        manifest = copy.deepcopy(self.manifest)
        quiet = copy.deepcopy(manifest["modules"][0])
        quiet["module_id"], quiet["route"] = "quiet", "quiet"
        quiet["pages"], quiet["resources"] = [], []
        manifest["modules"].append(quiet)
        selected = self.selected(timeouts={OWNER: {"status": 90}}, manifest=manifest)
        self.assertEqual(set(selected.timeout_manifests), {OWNER})
        self.assertEqual(selected.timeout_manifests[OWNER].module_id, "demo")
        self.assertEqual(selected.capability_timeouts, {TARGET: 90.0})
        with self.assertRaises(TypeError):
            selected.timeout_manifests["minimal/quiet"] = quiet
        with self.assertRaises(TypeError):
            selected.capability_timeouts[TARGET] = 91
        self.assertFalse(self.selected(timeouts={OWNER: {}}).timeout_manifests)
        self.assertFalse(self.selected().timeout_manifests)
        self.assertFalse(ExternalSourcePolicies().timeout_manifests)

    async def test_selected_ninety_constructs_before_external_module_load_without_grants(
        self,
    ):
        selected = self.selected(timeouts={OWNER: {"status": 90}})
        runtime = self.runtime(
            trusted_external_timeout_manifests=selected.timeout_manifests,
            capability_timeouts=selected.capability_timeouts,
        )
        self.assertEqual(self.seconds(runtime), 90)
        self.assertFalse(runtime.registry.snapshot().modules)
        self.assertFalse(tuple(runtime.extension_root.iterdir()))
        self.assertFalse(runtime._trusted_bundled_manifests)
        self.assertFalse(runtime._trusted_bundled_defaults)
        self.assertFalse(runtime._trusted_subscription_gates)
        self.assertFalse(
            runtime.issuer.allows_public_web(OWNER, self.declaration.capabilities[0])
        )
        self.assertEqual(
            runtime._budget_policy.deadline(
                InvocationOrigin.WEB_PUBLIC,
                100,
                30,
                110,
                module_id=OWNER,
                capability_id="status",
            ),
            110,
        )
        self.assertEqual(
            runtime._budget_policy.seconds(
                InvocationOrigin.LLM_TOOL, 30, module_id=OWNER, capability_id="status"
            ),
            30,
        )

    async def test_projection_alone_defaults_to_thirty_and_does_not_deploy(self):
        selected = self.selected(timeouts={OWNER: {"status": 90}})
        runtime = self.runtime(
            trusted_external_timeout_manifests=selected.timeout_manifests
        )
        self.assertEqual(self.seconds(runtime), 30)
        self.assertFalse(runtime.registry.snapshot().modules)
        self.assertFalse(
            runtime.issuer.allows_public_web(OWNER, self.declaration.capabilities[0])
        )
        self.assertEqual(self.seconds(self.runtime()), 30)

    async def test_absent_or_unknown_projection_cannot_authorize_timeout(self):
        for projection, timeouts in (
            (None, {TARGET: 90}),
            ({}, {TARGET: 90}),
            ({OWNER: self.declaration}, {(OWNER, "unknown"): 90}),
            ({OWNER: self.declaration}, {("another/demo", "status"): 90}),
        ):
            with self.subTest(timeouts=timeouts), self.assertRaises(ValueError):
                self.runtime(
                    trusted_external_timeout_manifests=projection,
                    capability_timeouts=timeouts,
                )

    async def test_core_revalidates_public_read_only_and_effective_web_origin(self):
        original = self.declaration.capabilities[0]
        for privacy, effect in (
            (PrivacyFloor.PRIVATE, CapabilityEffect.READ_ONLY),
            (PrivacyFloor.PUBLIC, CapabilityEffect.WRITE),
            (PrivacyFloor.PUBLIC, CapabilityEffect.READ_ONLY),
        ):
            capability = replace(
                original,
                privacy_floor=privacy,
                effect=effect,
                invocation_policy=InvocationPolicy.COMMAND_ONLY,
                invocation_origins=(InvocationOrigin.COMMAND,),
            )
            declaration = validate_contract(
                replace(
                    self.declaration,
                    capabilities=(capability,),
                    pages=(),
                    resources=(),
                )
            )
            with (
                self.subTest(privacy=privacy, effect=effect),
                self.assertRaises(ValueError),
            ):
                self.runtime(
                    trusted_external_timeout_manifests={OWNER: declaration},
                    capability_timeouts={TARGET: 90},
                )
        # Natural-language eligibility does not imply a WEB_PUBLIC origin.
        narrowed = validate_contract(
            replace(
                self.declaration,
                capabilities=(
                    replace(
                        original,
                        invocation_policy=InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
                        invocation_origins=None,
                    ),
                ),
                pages=(),
                resources=(),
            )
        )
        with self.assertRaises(ValueError):
            self.runtime(
                trusted_external_timeout_manifests={OWNER: narrowed},
                capability_timeouts={TARGET: 90},
            )

    async def test_core_receiving_validation_rejects_wrong_owner_and_hostile_manifest(
        self,
    ):
        for projection in (
            {"missing-separator": self.declaration},
            {"minimal/wrong": self.declaration},
            {OWNER: object()},
            {OWNER: replace(self.declaration, module_id="invalid/name")},
        ):
            with (
                self.subTest(projection=projection),
                self.assertRaises((ValueError, TypeError)),
            ):
                self.runtime(
                    trusted_external_timeout_manifests=projection,
                    capability_timeouts={TARGET: 90},
                )

    async def test_external_timeout_owner_cannot_overlap_bundled_manifest_or_default(
        self,
    ):
        for bundled in (
            {"trusted_bundled_manifests": {OWNER: self.declaration}},
            {"trusted_bundled_defaults": {OWNER: ("status",)}},
        ):
            with (
                self.subTest(bundled=bundled),
                self.assertRaisesRegex(ValueError, "overlap"),
            ):
                self.runtime(
                    trusted_external_timeout_manifests={OWNER: self.declaration},
                    capability_timeouts={TARGET: 90},
                    **bundled,
                )

    async def test_loader_and_core_keep_ninety_ceiling_and_reject_boolean(self):
        for seconds in (True, False, 91, 0, -1, float("inf")):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    self.selected(timeouts={OWNER: {"status": seconds}})
                with self.assertRaises(ValueError):
                    self.runtime(
                        trusted_external_timeout_manifests={OWNER: self.declaration},
                        capability_timeouts={TARGET: seconds},
                    )

    async def test_loader_rejects_unknown_owner_target_and_non_web_declaration(self):
        for owner, capability in (("another/demo", "status"), (OWNER, "unknown")):
            with (
                self.subTest(owner=owner, capability=capability),
                self.assertRaises(ValueError),
            ):
                self.selected(timeouts={owner: {capability: 90}})
        for change in (
            {"privacy_floor": "private"},
            {"effect": "write"},
            {"invocation_policy": "command_only", "invocation_origins": ["command"]},
        ):
            manifest = copy.deepcopy(self.manifest)
            module = manifest["modules"][0]
            module["pages"], module["resources"] = [], []
            module["capabilities"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.selected(timeouts={OWNER: {"status": 90}}, manifest=manifest)

    async def test_bundled_ff14_capability_and_collector_allowlists_are_unchanged(self):
        package = parse_manifest(
            (ROOT / "modules/ff14/yomihime.manifest.json").read_bytes()
        )
        declaration = package.modules[0]
        owner = package.global_module_id(declaration.module_id)
        target = (owner, declaration.capabilities[0].capability_id)
        collector = (owner, declaration.schedules[0].collector_id)
        selected = self.selected(timeouts={OWNER: {"status": 90}})
        runtime = self.runtime(
            trusted_bundled_manifests={owner: declaration},
            trusted_external_timeout_manifests=selected.timeout_manifests,
            capability_timeouts={target: 12, **selected.capability_timeouts},
            collector_timeouts={collector: 45},
        )
        self.assertEqual(self.seconds(runtime, target), 12)
        self.assertEqual(self.seconds(runtime), 90)
        self.assertEqual(runtime.scheduler.collector_timeouts, {collector: 45})
        self.assertEqual(runtime._trusted_bundled_manifests, {owner: declaration})
        with self.assertRaises(ValueError):
            self.runtime(
                trusted_external_timeout_manifests=selected.timeout_manifests,
                collector_timeouts={(OWNER, "status"): 90},
            )
