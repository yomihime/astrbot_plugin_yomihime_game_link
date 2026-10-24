"""Selected external policies through real manifests, storage and source HTTP.

All providers and credentials are synthetic; external factory code is never imported.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ygl_test_subject.core.contracts.services import (
    ConfigFieldUpdate,
    PersistedConfigPatch,
)
from ygl_test_subject.core.contracts.storage import SecretTarget
from ygl_test_subject.extensions.discovery import discover_packages
from ygl_test_subject.extensions.disk_manifest import parse_manifest
from ygl_test_subject.infrastructure.http import SourceHttpService
from ygl_test_subject.infrastructure.http_transport import AioHttpTransport
from ygl_test_subject.infrastructure.secret_codec import AESGCMSecretCodec
from ygl_test_subject.infrastructure.secret_store import SQLiteSecretStore
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_config import (
    SQLiteConfigRepository,
)
from ygl_test_subject.services.external_source_policies import (
    POLICY_FILE,
    ExternalSourcePolicies,
)
from ygl_test_subject.services.source_credentials import (
    SourceCredentialService,
    bind_source_credential_declarations,
)

from tests.fixtures.external_page_catalog import external_catalog_fixture
from tests.services.test_source_authorizations import descriptor
from yomihime_game_link_sdk import ConfigUpdateMode, HttpRequest, HttpResponse
from yomihime_game_link_sdk.errors import SourceHttpError
from yomihime_game_link_sdk.services import ConfigTarget

ROOT = Path(__file__).resolve().parents[2]
OWNER = "minimal/demo"
ALIAS = "credential_example"
HOST = "api.example.test"


def source_policy_fixture():
    """Extend the existing real external-page fixture with generic source data."""
    projection, _, _ = external_catalog_fixture(with_logo=False)
    manifest = json.loads(projection.packages["minimal"][2])
    module = manifest["modules"][0]
    module["sources"] = [
        {"source_id": "api", "host": HOST},
        {
            "source_id": "example_auth",
            "host": "auth.example.test",
            "authorization_only": True,
        },
    ]
    module["config_fields"].append(
        {
            "name": ALIAS,
            "sensitive": True,
            "group": "example_key",
            "description": "Synthetic managed credential",
        }
    )
    policy = {
        "schema_version": 1,
        "credentials": [
            {
                "owner": OWNER,
                "source": "api",
                "field": ALIAS,
                "group": "example_key",
                "description": "Synthetic managed credential",
                "optional": False,
                "injections": [
                    {
                        "path": "/v1/items/*",
                        "method": "GET",
                        "location": "header",
                        "name": "X-Example-Key",
                    }
                ],
            }
        ],
        "authorizations": {
            OWNER: {
                "example_qr": {
                    "credential_field": ALIAS,
                    "descriptor": descriptor(),
                }
            }
        },
    }
    return manifest, policy


def selected_policy_bytes(raw, policy):
    return json.dumps(
        {
            "schema_version": 1,
            "packages": [
                {
                    "manifest": raw.decode("utf-8"),
                    "credentials": policy["credentials"],
                    "authorizations": policy["authorizations"],
                }
            ],
        }
    ).encode("utf-8")


def write_external_package(root, raw):
    package = root / "external"
    package.mkdir()
    (package / "yomihime.manifest.json").write_bytes(raw)
    (package / "pages").mkdir()
    (package / "pages/entry.js").write_bytes(
        (ROOT / "examples/modules/minimal/pages/entry.js").read_bytes()
    )
    (package / "module.py").write_text(
        "raise AssertionError('external Python must not import')\n", encoding="utf-8"
    )
    return package


class ExternalSourcePolicyTests(unittest.TestCase):
    def test_optional_public_execution_budget_requires_exact_declared_target(self):
        manifest, policy = source_policy_fixture()
        value = json.loads(selected_policy_bytes(json.dumps(manifest).encode(), policy))
        package = value["packages"][0]
        package["capability_timeouts"] = {OWNER: {"status": 90}}
        selected = ExternalSourcePolicies(json.dumps(value).encode())
        self.assertEqual(selected.capability_timeouts[(OWNER, "status")], 90)
        for owner, capability, seconds in (
            ("another/demo", "status", 90),
            (OWNER, "unknown", 90),
            (OWNER, "status", True),
            (OWNER, "status", 91),
            (OWNER, "status", 0),
        ):
            package["capability_timeouts"] = {owner: {capability: seconds}}
            with self.assertRaises(ValueError):
                ExternalSourcePolicies(json.dumps(value).encode())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ygl-policy-", dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest, self.policy = source_policy_fixture()
        self.raw = json.dumps(self.manifest, indent=2).encode("utf-8")
        self.package = write_external_package(self.root, self.raw)

    def selected(self, *, manifest=None, policy=None):
        raw = self.raw if manifest is None else json.dumps(manifest).encode("utf-8")
        return ExternalSourcePolicies(
            selected_policy_bytes(raw, self.policy if policy is None else policy)
        )

    def discovered(self):
        packages = tuple(discover_packages(self.root))
        self.assertEqual(len(packages), 1)
        self.assertTrue(packages[0].valid, packages[0].diagnostic)
        return packages[0]

    def test_real_discovery_and_exact_raw_manifest_pin(self):
        selected = self.selected()
        package = self.discovered()
        self.assertTrue(selected.permits(package))
        self.assertEqual(
            selected.manifests["minimal"], hashlib.sha256(self.raw).hexdigest()
        )
        source = selected.credential_policies[0]
        self.assertEqual((source.module_id, source.resource_host), (OWNER, HOST))
        self.assertEqual(source.credential_ref, ALIAS)
        self.assertIn("example_qr", selected.authorizations[OWNER])
        self.assertEqual(selected.managed_credentials[0].project()["name"], ALIAS)

    def test_wrong_package_digest_and_manifest_only_change_do_not_permit(self):
        selected = self.selected()
        package = self.discovered()
        self.assertFalse(selected.permits(replace(package, package_id="another")))
        self.assertFalse(
            selected.permits(
                replace(
                    package,
                    _provenance=replace(package._provenance, manifest_sha256=bytes(32)),
                )
            )
        )
        # Even semantically equivalent JSON has a different reviewed byte pin.
        (self.package / "yomihime.manifest.json").write_bytes(self.raw + b"\n")
        self.assertFalse(selected.permits(self.discovered()))
        changed = copy.deepcopy(self.manifest)
        changed["author"] += " changed"
        (self.package / "yomihime.manifest.json").write_text(
            json.dumps(changed), encoding="utf-8"
        )
        self.assertFalse(selected.permits(self.discovered()))

    def test_unselected_policy_file_is_default_empty_and_grants_nothing(self):
        (self.package / "yomihime.source-policy.json").write_text(
            json.dumps(self.policy), encoding="utf-8"
        )
        selected = ExternalSourcePolicies.load(self.root)
        self.assertEqual(selected.credential_policies, ())
        self.assertEqual(selected.managed_credentials, ())
        self.assertFalse(selected.authorizations)
        self.assertFalse(selected.permits(self.discovered()))

    def test_installed_policy_load_and_duplicate_json_are_checked(self):
        (self.root / POLICY_FILE).write_bytes(
            selected_policy_bytes(self.raw, self.policy)
        )
        self.assertTrue(
            ExternalSourcePolicies.load(self.root).permits(self.discovered())
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            ExternalSourcePolicies(
                b'{"schema_version":1,"schema_version":1,"packages":[]}'
            )

    def test_credential_alias_requires_exact_sensitive_declaration(self):
        for update in (
            {"sensitive": False},
            {"default": "unsafe-default"},
            {"group": "different"},
            {"value_schema": {"type": "string"}},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                manifest = copy.deepcopy(self.manifest)
                manifest["modules"][0]["config_fields"][-1].update(update)
                self.selected(manifest=manifest)
        changed = copy.deepcopy(self.policy)
        changed["credentials"][0]["field"] = "credential_other"
        with self.assertRaises(ValueError):
            self.selected(policy=changed)

    def test_wrong_owner_and_unreviewed_sensitive_field_are_rejected(self):
        changed = copy.deepcopy(self.policy)
        changed["credentials"][0]["owner"] = "another/demo"
        with self.assertRaises(ValueError):
            self.selected(policy=changed)
        manifest = copy.deepcopy(self.manifest)
        manifest["modules"][0]["config_fields"].append(
            {"name": "credential_unreviewed", "sensitive": True}
        )
        with self.assertRaisesRegex(ValueError, "unreviewed"):
            self.selected(manifest=manifest)

    def test_policy_cannot_expand_binding_host_owner_or_alias(self):
        selected = self.selected()
        module = parse_manifest(self.raw).modules[0]
        policies = selected.credential_policies
        effective = bind_source_credential_declarations(
            OWNER, module.sources, module.config_fields, policies
        )
        self.assertEqual(effective[0].credential_ref, ALIAS)
        self.assertIsNone(module.sources[0].credential_ref)
        for owner, sources, fields in (
            ("another/demo", module.sources, module.config_fields),
            (
                OWNER,
                (replace(module.sources[0], host="elsewhere.example.test"),),
                module.config_fields,
            ),
            (
                OWNER,
                module.sources,
                tuple(
                    replace(f, sensitive=False) if f.name == ALIAS else f
                    for f in module.config_fields
                ),
            ),
        ):
            with (
                self.subTest(owner=owner, sources=sources),
                self.assertRaises(ValueError),
            ):
                bind_source_credential_declarations(owner, sources, fields, policies)

    def test_injection_path_method_and_header_targets_are_bounded(self):
        for update in (
            {"path": "https://elsewhere.example.test/key"},
            {"path": "/v1/items/*/../secret"},
            {"path": "/v1/items/**"},
            {"method": "DELETE"},
            {"name": "Host"},
            {"location": "json", "method": "GET"},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                policy = copy.deepcopy(self.policy)
                policy["credentials"][0]["injections"][0].update(update)
                self.selected(policy=policy)

    def test_authorization_requires_selected_field_owner_and_isolated_source(self):
        changes = []
        policy = copy.deepcopy(self.policy)
        policy["authorizations"] = {"another/demo": policy["authorizations"][OWNER]}
        changes.append((self.manifest, policy))
        policy = copy.deepcopy(self.policy)
        policy["authorizations"][OWNER]["example_qr"]["credential_field"] = "missing"
        changes.append((self.manifest, policy))
        manifest = copy.deepcopy(self.manifest)
        manifest["modules"][0]["sources"][1]["authorization_only"] = False
        changes.append((manifest, self.policy))
        policy = copy.deepcopy(self.policy)
        policy["authorizations"][OWNER]["wrong_flow"] = policy["authorizations"][
            OWNER
        ].pop("example_qr")
        changes.append((self.manifest, policy))
        for manifest, policy in changes:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.selected(manifest=manifest, policy=policy)


class ExternalSourcePolicyHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="ygl-policy-http-", dir=ROOT
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        manifest, policy = source_policy_fixture()
        raw = json.dumps(manifest).encode("utf-8")
        self.selected = ExternalSourcePolicies(selected_policy_bytes(raw, policy))
        self.module = parse_manifest(raw).modules[0]
        self.database = SQLiteDatabase(self.root / "state.sqlite3")
        self.addAsyncCleanup(self.database.executor.close, timeout=2)
        self.config = SQLiteConfigRepository(self.database)

        class KeyProvider:
            def get_key(self):
                return b"s" * 32

        self.store = SQLiteSecretStore(
            self.database,
            self.root / "secrets",
            codec=AESGCMSecretCodec(KeyProvider()),
        )
        self.transport = AioHttpTransport()
        self.addAsyncCleanup(self.transport.close)
        self.calls = []
        self.token = "synthetic-private-external-policy-token"

        async def send(method, url, body, headers, timeout_seconds, maximum):
            self.calls.append((method, str(url), body, headers))
            return HttpResponse(
                200,
                {"X-Echo": self.token},
                json.dumps({"echo": self.token, "ok": True}).encode(),
            )

        self.transport._send = send
        self.declarations = bind_source_credential_declarations(
            OWNER,
            self.module.sources,
            self.module.config_fields,
            self.selected.credential_policies,
        )
        self.credentials = SourceCredentialService(
            OWNER,
            self.declarations,
            self.module.config_fields,
            self.selected.credential_policies,
            self.config,
            self.store,
            self.transport,
            config_principal_id="operator",
        )
        self.addAsyncCleanup(self.credentials.close)
        self.http = SourceHttpService(
            self.declarations,
            self.transport,
            module_id=OWNER,
            credential_service=self.credentials,
        )
        target = ConfigTarget("operator", OWNER)
        current = await self.config.current(target)
        secret_target = SecretTarget("operator", OWNER, ALIAS)
        operation = "external-policy-key"
        material = self.selected.managed_credentials[0].material({"token": self.token})
        receipt = await self.store.stage(
            material.value,
            target=secret_target,
            operation_id=operation,
            expected_config_revision=current.revision,
        )
        claimed = await self.store.claim_for_config(
            receipt,
            target=secret_target,
            operation_id=operation,
            expected_config_revision=current.revision,
            expected_ledger_revision=receipt.ledger_revision,
        )
        snapshot = await self.config.update(
            target,
            PersistedConfigPatch(
                current.revision,
                (ConfigFieldUpdate(ALIAS, ConfigUpdateMode.REPLACE, receipt=receipt),),
                self.module.config_fields,
                operation,
                target,
            ),
        )
        await self.store.finalize_active(claimed, metadata_revision=snapshot.revision)

    async def test_selected_policy_injects_only_trusted_wire_and_redacts_output(self):
        response = await self.http.fetch(HttpRequest("api", "/v1/items/player_1"))
        self.assertEqual(len(self.calls), 1)
        method, url, _, headers = self.calls[0]
        self.assertEqual((method, url), ("GET", f"https://{HOST}/v1/items/player_1"))
        self.assertEqual(headers["X-Example-Key"], self.token)
        self.assertNotIn(self.token.encode(), response.body)
        self.assertNotIn(self.token, repr(response))
        self.assertNotIn(self.token, repr(self.selected.credential_policies))
        self.assertTrue(json.loads(response.body)["ok"])

    async def test_wrong_path_method_source_and_extra_segment_never_reach_wire(self):
        for request in (
            HttpRequest("api", "/v1/other/player_1"),
            HttpRequest("api", "/v1/items/player_1", "POST"),
            HttpRequest("api", "/v1/items/player_1/extra"),
            HttpRequest("api", "/v1/items/"),
            HttpRequest("example_auth", "/apply", "POST"),
        ):
            with (
                self.subTest(request=request),
                self.assertRaises(SourceHttpError) as caught,
            ):
                await self.http.fetch(request)
            self.assertNotIn(self.token, str(caught.exception))
        self.assertEqual(self.calls, [])
