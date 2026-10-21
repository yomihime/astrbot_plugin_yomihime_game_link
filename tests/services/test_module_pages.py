from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
)
from ygl_test_subject.services.module_pages import (
    ModulePageGrant,
    module_page_operation,
)

from yomihime_game_link_sdk import (
    ConfigField,
    ConfigSnapshot,
    ConfigTarget,
    RevisionConflict,
)


class _Authorization:
    def __init__(self):
        self.allowed = True
        self.resources = []

    async def authorize(self, operation, **kwargs):
        self.resources.append((operation, kwargs["resources"]))
        if not self.allowed:
            raise AdminAuthorizationDenied
        return object()

    async def validate_generation(self, *args, **kwargs):
        if not self.allowed:
            raise AdminAuthorizationDenied


@asynccontextmanager
async def _mutation(name):
    yield


class ModulePageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner = "sample/sample"
        self.target = ConfigTarget("principal", self.owner)
        self.identity = {
            "owner": self.owner,
            "page": "config",
            "runtime_id": "runtime",
            "module_epoch": 2,
        }
        self.fields = (
            ConfigField("region", default="default", value_schema={"type": "string"}),
            ConfigField("credential", sensitive=True),
        )
        self.module = SimpleNamespace(
            enabled=True,
            epoch=2,
            manifest=SimpleNamespace(
                config_fields=self.fields,
                pages=(
                    SimpleNamespace(
                        route_id="config",
                        config_fields=("region",),
                        credential_fields=("credential",),
                    ),
                ),
            ),
        )
        self.authorization = _Authorization()
        self.snapshot = ConfigSnapshot(
            1,
            {"region": "custom", "other": "private unrelated value"},
            target=self.target,
        )
        self.calls = []
        self.bound = True

        async def read(target, **kwargs):
            return self.snapshot

        async def update(invocation, owner, patch, **kwargs):
            self.calls.append((owner, patch))
            return SimpleNamespace(revision=2)

        async def credential(data, **kwargs):
            self.calls.append(data)
            return {"revision": 2}

        self.ops = SimpleNamespace(
            _require_accepting=lambda: None,
            registry=SimpleNamespace(
                snapshot=lambda: SimpleNamespace(modules={self.owner: self.module})
            ),
            config_principal_id="principal",
            authorization=self.authorization,
            lifecycle=SimpleNamespace(admission=SimpleNamespace(mutation=_mutation)),
            ordinary_resources=lambda: {self.target: {"region"}},
            credential_resources=lambda **kwargs: {self.target: {"credential"}},
            _config_repository=SimpleNamespace(current=read),
            _module_config_validators={},
            update_config=update,
            credential_update=credential,
        )

    async def checker(self, owner, page, epoch, action):
        if not self.bound:
            raise AdminAuthorizationDenied
        return ModulePageGrant(
            owner,
            page,
            "runtime",
            epoch,
            frozenset({"region"}),
            frozenset({"credential"}),
        )

    async def call(self, action, extra=None, identity=None, checker=None):
        return await module_page_operation(
            self.ops,
            action,
            {**(self.identity if identity is None else identity), **(extra or {})},
            authorization=object(),
            runtime_id="runtime",
            check_page_binding=checker or self.checker,
        )

    async def test_only_bound_owner_ordinary_fields_returned(self):
        result = await self.call("module-config-current")
        self.assertEqual(set(result), {"revision", "fields", "descriptors"})
        self.assertEqual(set(result["descriptors"]), {"region"})
        self.assertNotIn("secret", repr(result["descriptors"]))
        self.assertEqual(
            result["fields"],
            {
                "region": {
                    "value": "custom",
                    "present": True,
                    "state": "valid",
                    "source": "sqlite",
                }
            },
        )
        self.assertEqual(
            self.authorization.resources,
            [(AdminOperation.READ_CONFIG, {self.target: frozenset({"region"})})],
        )

    async def test_token_identity_is_not_management_authority(self):
        self.authorization.allowed = False
        with self.assertRaises(AdminAuthorizationDenied):
            await self.call("module-config-current")

    async def test_closed_request_and_stale_identities(self):
        with self.assertRaises(ValueError):
            await self.call("module-config-current", {"path": "master-key-reset"})
        for field, value in (
            ("owner", "game_link/core"),
            ("runtime_id", "old"),
            ("module_epoch", 1),
            ("page", "other"),
        ):
            with self.subTest(field=field), self.assertRaises(AdminAuthorizationDenied):
                await self.call(
                    "module-config-current", identity={**self.identity, field: value}
                )

        async def boolean(*args):
            return True

        with self.assertRaises(AdminAuthorizationDenied):
            await self.call("module-config-current", checker=boolean)

    async def test_cross_field_and_credentials_cannot_use_config(self):
        for values in ({"credential": "secret"}, {"other": "value"}):
            with (
                self.subTest(values=values),
                self.assertRaises(AdminAuthorizationDenied),
            ):
                await self.call(
                    "module-config-save", {"expected_revision": 1, "values": values}
                )
        self.assertEqual(self.calls, [])

    async def test_mutations_use_existing_CAS_paths(self):
        self.assertEqual(
            await self.call(
                "module-config-save",
                {"expected_revision": 1, "values": {"region": "new"}},
            ),
            {"revision": 2},
        )
        self.assertEqual(self.calls[0][1].expected_revision, 1)
        self.assertEqual(
            await self.call(
                "module-credential-replace",
                {
                    "expected_revision": 1,
                    "field": "credential",
                    "pair": {"client_id": "synthetic", "client_secret": "synthetic"},
                },
            ),
            {"revision": 2},
        )
        with self.assertRaises(ValueError):
            await self.call(
                "module-credential-clear",
                {"expected_revision": 1, "field": "credential", "confirmation": ""},
            )
        self.assertEqual(
            await self.call(
                "module-credential-clear",
                {
                    "expected_revision": 1,
                    "field": "credential",
                    "confirmation": "CLEAR MODULE CREDENTIAL",
                },
            ),
            {"revision": 2},
        )

    async def test_revoke_during_read_rejects_result(self):
        async def read(*args, **kwargs):
            self.bound = False
            return self.snapshot

        self.ops._config_repository.current = read
        with self.assertRaises(AdminAuthorizationDenied):
            await self.call("module-config-current")

    async def test_epoch_changes_during_write_rejects_receipt(self):
        async def write(*args, **kwargs):
            self.module.epoch = 3
            return SimpleNamespace(revision=2)

        self.ops.update_config = write
        with self.assertRaises(AdminAuthorizationDenied):
            await self.call(
                "module-config-save",
                {"expected_revision": 1, "values": {"region": "new"}},
            )


class ModulePageEffectiveConfigTests(unittest.IsolatedAsyncioTestCase):
    setUp = ModulePageTests.setUp
    checker = ModulePageTests.checker
    call = ModulePageTests.call

    async def test_page_value_equals_module_effective_projection(self):
        from types import SimpleNamespace

        from ygl_test_subject.services.configuration.legacy_copy import (
            LegacyConfigCopyPolicy,
            LegacyConfigProjection,
        )

        self.snapshot = ConfigSnapshot(1, {}, target=self.target)
        source_target = ConfigTarget("principal", "game_link/core")
        source = ConfigSnapshot(2, {"old_region": "global"}, target=source_target)
        policy = LegacyConfigCopyPolicy(
            "sample.region",
            source_target,
            "old_region",
            self.owner,
            "region",
            self.fields[0].value_schema,
        )
        self.ops._legacy_config_policies = {policy.policy_id: policy}

        async def pair(*args):
            return source, self.snapshot

        self.ops._config_repository.current_pair = pair

        async def current():
            return self.snapshot

        module = await LegacyConfigProjection(
            SimpleNamespace(current=current),
            self.ops._config_repository,
            (policy,),
            self.ops.lifecycle.admission,
            self.fields,
        ).current()
        page = await self.call("module-config-current")
        self.assertEqual(page["fields"]["region"]["value"], module.values["region"])
        self.assertEqual(page["fields"]["region"]["source"], "legacy")
        self.assertTrue(page["fields"]["region"]["migration_pending"])
        self.assertEqual(page["revision"], 1)
        self.assertNotIn("game_link/core", repr(page))

    async def test_effective_target_override_invalid_source_and_revision_race(self):
        from ygl_test_subject.services.configuration.legacy_copy import (
            LegacyConfigCopyPolicy,
        )

        source_target = ConfigTarget("principal", "game_link/core")
        policy = LegacyConfigCopyPolicy(
            "sample.region",
            source_target,
            "old_region",
            self.owner,
            "region",
            self.fields[0].value_schema,
        )
        self.ops._legacy_config_policies = {policy.policy_id: policy}

        async def pair(*args):
            return ConfigSnapshot(
                2, {"old_region": "global"}, target=source_target
            ), self.snapshot

        self.ops._config_repository.current_pair = pair
        self.assertEqual(
            (await self.call("module-config-current"))["fields"]["region"]["value"],
            "custom",
        )
        self.snapshot = ConfigSnapshot(1, {}, target=self.target)

        async def raced(*args):
            return ConfigSnapshot(
                2, {"old_region": "global"}, target=source_target
            ), ConfigSnapshot(2, {"region": "new"}, target=self.target)

        self.ops._config_repository.current_pair = raced
        with self.assertRaises(RevisionConflict):
            await self.call("module-config-current")

    async def test_theme_descriptor_uses_injected_choices_and_null_clears_override(
        self,
    ):
        from ygl_test_subject.services.configuration.defaults import (
            MODULE_REPLY_THEME,
            presentation_choices,
        )

        self.module.manifest.pages[0].config_fields = ("region", "reply_theme")
        self.ops.ordinary_resources = lambda: {self.target: {"region", "reply_theme"}}

        async def checker(*args):
            return ModulePageGrant(
                self.owner,
                "config",
                "runtime",
                2,
                frozenset({"region", "reply_theme"}),
                frozenset({"credential"}),
            )

        result = await module_page_operation(
            self.ops,
            "module-config-current",
            self.identity,
            authorization=object(),
            runtime_id="runtime",
            check_page_binding=checker,
            descriptor_choices=presentation_choices,
        )
        self.assertEqual(
            result["fields"]["reply_theme"],
            {"value": None, "state": "valid", "present": False, "source": "default"},
        )
        descriptor = result["descriptors"]["reply_theme"]
        self.assertEqual(
            descriptor["choices"], presentation_choices(self.owner, MODULE_REPLY_THEME)
        )
        self.assertEqual(descriptor["choices"][0]["value"], None)
        await module_page_operation(
            self.ops,
            "module-config-save",
            {**self.identity, "expected_revision": 1, "values": {"reply_theme": None}},
            authorization=object(),
            runtime_id="runtime",
            check_page_binding=checker,
        )
        self.assertEqual(self.calls[-1][1].updates[0].mode.value, "clear")
        self.snapshot = ConfigSnapshot(1, {"reply_theme": None}, target=self.target)
        result = await module_page_operation(
            self.ops,
            "module-config-current",
            self.identity,
            authorization=object(),
            runtime_id="runtime",
            check_page_binding=checker,
            descriptor_choices=presentation_choices,
        )
        self.assertEqual(result["fields"]["reply_theme"]["state"], "invalid")

    async def test_descriptor_rejects_private_schema_annotations(self):
        self.module.manifest.config_fields = (
            replace(
                self.fields[0],
                value_schema={"type": "string", "private_source": "game_link/core"},
            ),
            self.fields[1],
        )
        with self.assertRaises(ValueError):
            await self.call("module-config-current")

    async def test_effective_source_type_and_secret_metadata_reject_without_value_leak(
        self,
    ):
        from ygl_test_subject.services.configuration.legacy_copy import (
            LegacyConfigCopyPolicy,
        )

        from yomihime_game_link_sdk import SecretMetadata, SecretMetadataState

        source_target = ConfigTarget("principal", "game_link/core")
        policy = LegacyConfigCopyPolicy(
            "sample.region",
            source_target,
            "old_region",
            self.owner,
            "region",
            self.fields[0].value_schema,
        )
        self.ops._legacy_config_policies = {policy.policy_id: policy}
        self.snapshot = ConfigSnapshot(1, {}, target=self.target)
        for source in (
            ConfigSnapshot(2, {"old_region": 123}, target=source_target),
            ConfigSnapshot(
                2,
                {},
                (
                    SecretMetadata(
                        "old_region", None, 2, SecretMetadataState.TOMBSTONED
                    ),
                ),
                source_target,
            ),
        ):

            async def pair(*args):
                return source, self.snapshot

            self.ops._config_repository.current_pair = pair
            with self.assertRaises(ValueError):
                await self.call("module-config-current")
