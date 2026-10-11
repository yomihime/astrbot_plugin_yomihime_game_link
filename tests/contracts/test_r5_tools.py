"""Versioned three-entry contracts and public-only Tool error/cache envelopes."""

import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.policy import public_web_allowed, tool_allowed
from ygl_test_subject.extensions.disk_manifest import ManifestError, parse_manifest
from ygl_test_subject.services.cache import CacheAccessCoordinator, CacheAccessError

from yomihime_game_link_sdk.contexts import InvocationOrigin as Origin
from yomihime_game_link_sdk.declarations import (
    CapabilityDescriptor,
    CapabilityEffect,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
)
from yomihime_game_link_sdk.display import Privacy
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    FactDocument,
    ResultStatus,
)
from yomihime_game_link_sdk.storage import CacheVisibility

SCHEMA = dict(type="object", properties={}, required=[], additionalProperties=False)


class R5ContractTests(unittest.IsolatedAsyncioTestCase):
    def cap(self, **kwargs):
        return validate_contract(
            CapabilityDescriptor(
                "price",
                SCHEMA,
                InvocationPolicy.COMMAND_AND_PUBLIC_WEB,
                CapabilityEffect.READ_ONLY,
                **kwargs,
            )
        )

    def test_three_entries_keep_web_optin_public_and_readonly(self):
        cap = self.cap(
            invocation_origins=(Origin.COMMAND, Origin.WEB_PUBLIC, Origin.LLM_TOOL)
        )
        self.assertTrue(tool_allowed(cap))
        self.assertFalse(public_web_allowed("p/m", cap, frozenset()))
        self.assertTrue(public_web_allowed("p/m", cap, frozenset({("p/m", "price")})))
        self.assertFalse(tool_allowed(replace(cap, invocation_origins=None)))

    def test_closed_explicit_origins_reject_duplicates_unknown_private_write_and_removal(
        self,
    ):
        for origins in (
            (),
            (Origin.COMMAND, Origin.COMMAND),
            (Origin.LLM_TOOL,),
            (Origin.COMMAND, Origin.ADMIN),
            ("command", "web_public"),
            (Origin.COMMAND, Origin.LLM_TOOL),
        ):
            with self.subTest(origins=origins), self.assertRaises(ValueError):
                self.cap(invocation_origins=origins)
        cap = self.cap(
            invocation_origins=(Origin.COMMAND, Origin.WEB_PUBLIC, Origin.LLM_TOOL)
        )
        with self.assertRaises(ValueError):
            validate_contract(replace(cap, effect=CapabilityEffect.WRITE))
        with self.assertRaises(ValueError):
            validate_contract(
                replace(
                    cap,
                    privacy_floor=__import__(
                        "yomihime_game_link_sdk"
                    ).PrivacyFloor.PRIVATE,
                )
            )

    def test_old_policy_mappings_and_natural_write_remain_compatible(self):
        expected = {
            InvocationPolicy.COMMAND_ONLY: (Origin.COMMAND,),
            InvocationPolicy.COMMAND_AND_PUBLIC_WEB: (
                Origin.COMMAND,
                Origin.WEB_PUBLIC,
            ),
            InvocationPolicy.NATURAL_LANGUAGE_ALLOWED: (
                Origin.COMMAND,
                Origin.LLM_TOOL,
            ),
        }
        for policy, origins in expected.items():
            cap = replace(self.cap(), invocation_policy=policy)
            self.assertEqual(cap.effective_origins, origins)
        legacy_write = replace(
            self.cap(),
            invocation_policy=InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
            effect=CapabilityEffect.WRITE,
        )
        self.assertFalse(tool_allowed(legacy_write))

    def test_package_old_versions_reject_new_field_and_keep_legacy(self):
        cap = self.cap(
            invocation_origins=(Origin.COMMAND, Origin.WEB_PUBLIC, Origin.LLM_TOOL)
        )
        module = validate_contract(
            ModuleManifest(
                "m", "m", ModuleCategory.GAME, "module:Factory", "1.0.0", (cap,)
            )
        )
        for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate_contract(
                    PackageManifest(
                        "p", "1.0.0", version, (module,), "test", "MIT", "offline"
                    )
                )
            legacy = replace(
                module, capabilities=(replace(cap, invocation_origins=None),)
            )
            with self.assertRaises(ValueError):
                validate_contract(
                    PackageManifest(
                        "p", "1.0.0", version, (legacy,), "test", "MIT", "offline"
                    )
                )
            self.assertEqual(
                validate_contract(
                    PackageManifest(
                        "p", "1.0.0", "2.0", (legacy,), "test", "MIT", "offline"
                    )
                ).contract_version,
                "2.0",
            )

    def test_disk_new_field_version_and_shape_are_closed(self):
        data = dict(
            schema_version=1,
            package_id="p",
            package_version="1.0.0",
            contract_version="2.0",
            author="tests",
            license="MIT",
            source="offline",
            modules=[
                dict(
                    module_id="m",
                    route="m",
                    category="game",
                    factory_entry="module:Factory",
                    module_version="1.0.0",
                    capabilities=[
                        dict(
                            capability_id="price",
                            input_schema=SCHEMA,
                            invocation_policy="command_and_public_web",
                            effect="read_only",
                            invocation_origins=["command", "web_public", "llm_tool"],
                        )
                    ],
                )
            ],
        )
        self.assertTrue(
            tool_allowed(
                parse_manifest(json.dumps(data).encode()).modules[0].capabilities[0]
            )
        )
        data["contract_version"] = "1.5.0"
        with self.assertRaises(ManifestError):
            parse_manifest(json.dumps(data).encode())
        data["contract_version"] = "2.0"
        data["modules"][0]["capabilities"][0]["invocation_origins"] = None
        with self.assertRaises(ManifestError):
            parse_manifest(json.dumps(data).encode())

    def test_error_facts_are_versioned_public_closed_envelope(self):
        error = validate_contract(
            ErrorDetail(ErrorCode.NO_RECORDS, "no public records")
        )
        facts = validate_contract(
            FactDocument(
                dict(
                    status="error",
                    error=dict(code=error.code.value, message=error.message),
                )
            )
        )
        accepted = validate_contract(
            CapabilityResult("r", ResultStatus.ERROR, model_facts=facts, error=error)
        )
        self.assertEqual(accepted.model_facts.facts["status"], "error")
        with self.assertRaises(ValueError):
            validate_contract(replace(facts, schema_version="1.5.0"))
        for changes in (
            dict(schema_version="1.5.0"),
            dict(privacy=Privacy.PRIVATE),
            dict(model_facts=validate_contract(FactDocument({"private": "internal"}))),
            dict(
                model_facts=validate_contract(
                    FactDocument(
                        dict(
                            status="error",
                            error=dict(code="unknown", message="different"),
                        )
                    )
                )
            ),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_contract(replace(accepted, **changes))

    async def test_tool_public_cache_scope_without_principal_and_explicit_private_denial(
        self,
    ):
        # Exercise the exact scope methods without adding a persistence/identity mock.
        cache = CacheAccessCoordinator.__new__(CacheAccessCoordinator)
        invocation = SimpleNamespace(
            origin=Origin.LLM_TOOL, actor_id="trusted-actor", grant_id=None
        )
        object.__setattr__(cache, "_CacheAccessCoordinator__invocation", invocation)
        self.assertIs(cache._default_visibility(), CacheVisibility.PUBLIC)
        self.assertEqual(
            (await cache._scope(CacheVisibility.PUBLIC)).kind.value, "public"
        )
        for visibility in (CacheVisibility.USER, CacheVisibility.AUTHORIZED):
            with self.assertRaises(CacheAccessError):
                await cache._scope(visibility)
        invocation.origin = Origin.COMMAND
        self.assertIs(cache._default_visibility(), CacheVisibility.USER)


if __name__ == "__main__":
    unittest.main()
