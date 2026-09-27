"""Behavioral contract probes for Core-B03 W1 (CC-B03-01 through 07)."""

import asyncio
import copy
import pickle
import traceback
import unittest
from datetime import UTC, datetime, timedelta
from inspect import signature
from typing import get_type_hints

from ygl_test_subject.api.administration import (
    AdminError,
    CapabilitySummary,
    ConfigSummary,
    DataCount,
    ModuleAdminSnapshot,
    ModuleHealth,
    ModuleLifecycle,
    ModuleStatus,
)
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CapabilityReference,
    ConfigField,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    SourceDeclaration,
)
from ygl_test_subject.api.services import (
    Binding,
    BindingDefaultSnapshot,
    CacheAccessRequest,
    CallerCapability,
    ConfigFieldUpdate,
    ConfigPatch,
    ConfigPatchMode,
    ConfigSnapshot,
    ConfigTarget,
    ConversationKey,
    ConversationKind,
    ConversationRef,
    DependencyInvoker,
    Grant,
    GrantStatus,
    HttpRequest,
    LoginSession,
    LoginSessionStatus,
    ModuleHandlers,
    PersistedConfigPatch,
    Principal,
    SecretMaterial,
    SubscriptionOperations,
    SubscriptionUnavailable,
    validate_cache_access_request,
)
from ygl_test_subject.api.storage import (
    CacheEntry,
    CacheLookup,
    CacheLookupStatus,
    CacheVisibility,
    ClaimedSecretReceipt,
    CollectionDescriptor,
    CollectionIndex,
    GrantReference,
    OwnerScope,
    OwnershipKind,
    ResourceMetadata,
    SecretMetadata,
    SecretMetadataState,
    SecretReceipt,
    SecretReceiptState,
    SecretRef,
    SecretTarget,
)
from ygl_test_subject.core.ports import (
    AuthorizationRepository,
    BindingRepository,
    CallerCapabilityIssuer,
    ConfigRepository,
    FileStage,
    GrantRevocationCoordinator,
    GrantStore,
    IdentityRepository,
    LoginSessionRepository,
    ModuleNotRegistered,
    ModuleRegistrationLookup,
    ModuleRegistrationSnapshot,
    ResourceRepository,
    RevisionConflict,
    SafeFileStore,
    SecretCompensationState,
    SecretOwner,
    SecretReceiptLedger,
    SecretStore,
    SecretTransition,
    UnitOfWork,
)
from ygl_test_subject.core.ports import (
    CacheRepository as PortCacheRepository,
)
from ygl_test_subject.core.registry import Registry


class B03ContractTests(unittest.TestCase):
    def test_cc_b03_01_imports_annotations_and_immutable_snapshots(self):
        for cls in (
            ConfigField,
            SourceDeclaration,
            CollectionDescriptor,
            CapabilityReference,
            Principal,
            Binding,
            BindingDefaultSnapshot,
            Grant,
            LoginSession,
            ModuleStatus,
            ModuleManifest,
            PackageManifest,
        ):
            self.assertTrue(get_type_hints(cls), cls.__name__)
        source = SourceDeclaration("steam", "api.example.test", "credential_steam")
        self.assertEqual(source.timeout_seconds, 10.0)
        collection = CollectionDescriptor(
            "prices", 1, OwnershipKind.PUBLIC, (CollectionIndex("by_app", "app"),)
        )
        with self.assertRaises(AttributeError):
            collection.name = "other"  # type: ignore[misc]
        dependency = CapabilityReference("package/target", "target.query")
        capability = CapabilityDescriptor(
            "source.query",
            {"type": "object", "properties": {}, "required": []},
            InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
            CapabilityEffect.READ_ONLY,
            required_capabilities=(dependency,),
        )
        self.assertEqual(capability.required_capabilities, (dependency,))

        class EvilCapabilityId(str):
            def __eq__(self, other: object) -> bool:
                return True

            __hash__ = str.__hash__

        with self.assertRaises(ValueError):
            CapabilityReference("package/target", EvilCapabilityId("wrong"))
        forged_dependency = object.__new__(CapabilityReference)
        object.__setattr__(forged_dependency, "module_id", "package/target")
        object.__setattr__(
            forged_dependency, "capability_id", EvilCapabilityId("wrong")
        )
        with self.assertRaises(ValueError):
            CapabilityReference.validate(forged_dependency)
        with self.assertRaises(ValueError):
            CapabilityDescriptor(
                "source.query",
                {"type": "object", "properties": {}, "required": []},
                InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
                CapabilityEffect.READ_ONLY,
                required_capabilities=(forged_dependency,),
            )
        self.assertNotEqual(
            CapabilityReference("package/target", "wrong"),
            CapabilityReference("package/target", "right"),
        )
        legacy_capability = CapabilityDescriptor(
            "legacy.query",
            {"type": "object", "properties": {}, "required": []},
            InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
            CapabilityEffect.READ_ONLY,
            required_capabilities=("local.lookup",),
        )
        self.assertEqual(legacy_capability.required_capabilities, ("local.lookup",))
        self.assertEqual(
            CallerCapability("package/source", "source.query", 3, 4).module_id,
            "package/source",
        )
        self.assertTrue(get_type_hints(DependencyInvoker.invoke))
        self.assertTrue(get_type_hints(CallerCapabilityIssuer.issue))
        self.assertTrue(get_type_hints(CallerCapabilityIssuer.require))

    def test_cc_b03_02_scopes_and_redaction(self):
        public = OwnerScope.public()
        self.assertEqual(public.kind, OwnershipKind.PUBLIC)
        with self.assertRaises(AttributeError):
            ResourceMetadata("asset", "image/png", public, 1).scope.user_id = "u"  # type: ignore[misc]
        with self.assertRaises(ValueError):
            Grant(
                "grant",
                1,
                "user",
                "module",
                "account",
                ("read",),
                "raw-secret",
                GrantStatus.ACTIVE,
            )
        summary = ConfigSummary("module", 1, {"token": "configured"}, ("token",))
        self.assertEqual(summary.fields["token"], "configured")
        snapshot = ModuleAdminSnapshot(
            ModuleStatus(
                "module", True, ModuleLifecycle.ACTIVE, ModuleHealth.HEALTHY, 1, 2
            ),
            summary,
            (CapabilitySummary("lookup", True),),
            (DataCount("records", 0),),
        )
        self.assertTrue(snapshot.status.enabled)
        with self.assertRaises(ValueError):
            AdminError("failure", "failed secret_token=secret_x")
        with self.assertRaises(ValueError):
            ConfigSummary("module", 1, {"password": "RAW_LEAK"}, ("password",))
        with self.assertRaises(ValueError):
            ConfigSummary("module", 1, {"PASSWORD": "RAW_LEAK"})
        with self.assertRaises(ValueError):
            ConfigSummary("module", 1, {"session_id": "RAW_LEAK"})
        with self.assertRaises(ValueError):
            ConfigSummary("module", 1, {"nested": {"token": "RAW"}})  # type: ignore[dict-item]
        with self.assertRaises(ValueError):
            ModuleAdminSnapshot(
                ModuleStatus(
                    "other", True, ModuleLifecycle.ACTIVE, ModuleHealth.HEALTHY, 1, 2
                ),
                summary,
            )

        class ExplodingMapping(dict):
            def items(self):
                raise RuntimeError("RAW_LEAK")

        with self.assertRaises(ValueError) as admin_mapping_error:
            ConfigSummary("module", 1, ExplodingMapping())
        self.assertEqual(
            str(admin_mapping_error.exception),
            "admin mapping cannot be inspected safely",
        )
        self.assertIsNone(admin_mapping_error.exception.__cause__)
        self.assertIsNone(admin_mapping_error.exception.__context__)
        self.assertNotIn(
            "RAW_LEAK",
            "".join(traceback.format_exception(admin_mapping_error.exception)),
        )

        class EvilStr(str):
            def strip(self):
                raise ValueError("RAW_LEAK_FROM_STRIP")

        with self.assertRaisesRegex(ValueError, "cannot be inspected safely"):
            ConfigSummary("module", 1, {"region": EvilStr("cn")})
        with self.assertRaises(ValueError) as admin_error:
            AdminError("failure", EvilStr("safe"))
        self.assertIsNone(admin_error.exception.__cause__)
        self.assertIsNone(admin_error.exception.__context__)

    def test_cc_b03_03_expected_revisions_are_explicit(self):
        patch = ConfigPatch(
            3,
            (ConfigFieldUpdate("region", ConfigPatchMode.REPLACE, "cn"),),
        )
        self.assertEqual(patch.expected_revision, 3)
        with self.assertRaises(ValueError):
            ConfigPatch(1, (ConfigFieldUpdate("region", ConfigPatchMode.REPLACE),))
        with self.assertRaises(ValueError):
            ConfigPatch(1, (ConfigFieldUpdate("region", ConfigPatchMode.KEEP, "cn"),))
        secret_patch = ConfigPatch(
            3,
            (
                ConfigFieldUpdate(
                    "api_key",
                    ConfigPatchMode.REPLACE,
                    secret=SecretMaterial(b"opaque-input"),
                ),
            ),
            (ConfigField("api_key", sensitive=True),),
        )
        self.assertIsNotNone(secret_patch.updates[0].secret)
        with self.assertRaises(ValueError):
            ConfigPatch(
                3,
                (ConfigFieldUpdate("api_key", ConfigPatchMode.REPLACE, "RAW_LEAK"),),
                (ConfigField("api_key", sensitive=True),),
            )
        with self.assertRaises(ValueError):
            ConfigPatch(
                3,
                (ConfigFieldUpdate("unknown", ConfigPatchMode.REPLACE, "value"),),
                (ConfigField("region"),),
            )
        for field in ("accesskey", "passwd", "pwd", "session_id", "signingkey"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                ConfigFieldUpdate(field, ConfigPatchMode.REPLACE, "RAW_LEAK")
        with self.assertRaises(ValueError):
            ConfigPatch(
                3,
                (ConfigFieldUpdate("pin", ConfigPatchMode.REPLACE, "RAW_LEAK"),),
            )

        target = ConfigTarget("user", "steam")
        receipt = SecretReceipt(
            SecretRef("secret_pin", "user", "steam", "pin", "op-config"),
            SecretTarget("user", "steam", "pin"),
            "op-config",
            3,
            7,
        )
        ref = receipt.secret_ref
        staged = ConfigPatch(
            3,
            (
                ConfigFieldUpdate(
                    "pin", ConfigPatchMode.REPLACE, secret=SecretMaterial(b"opaque")
                ),
            ),
            (ConfigField("pin", sensitive=True),),
            "op-config",
        )
        persisted = staged.to_persisted(target, {"pin": receipt})
        self.assertIsInstance(persisted, PersistedConfigPatch)
        self.assertIsNone(persisted.updates[0].secret)
        self.assertEqual(persisted.updates[0].receipt, receipt)
        self.assertEqual(persisted.target, target)
        self.assertEqual(
            PersistedConfigPatch.validate_for(target, persisted), persisted
        )

        class EvilModuleId(str):
            def __eq__(self, other):
                return True

            __hash__ = str.__hash__

        forged_target = object.__new__(ConfigTarget)
        object.__setattr__(forged_target, "principal_id", "user")
        object.__setattr__(forged_target, "module_id", EvilModuleId("steam"))
        with self.assertRaises(ValueError):
            PersistedConfigPatch.validate_for(forged_target, persisted)
        with self.assertRaises(ValueError):
            PersistedConfigPatch.validate_for(ConfigTarget("other", "steam"), persisted)
        with self.assertRaises(ValueError):
            PersistedConfigPatch.validate_for(ConfigTarget("user", "other"), persisted)
        with self.assertRaises(ValueError):
            staged.to_persisted(target, {})
        with self.assertRaises(ValueError):
            staged.to_persisted(
                target,
                {"pin": receipt, "extra": receipt},
            )

        class DuplicateReceiptMapping(dict):
            def items(self):
                return [("pin", receipt), ("pin", receipt)]

        with self.assertRaises(ValueError):
            staged.to_persisted(target, DuplicateReceiptMapping())
        wrong_target_receipt = SecretReceipt(
            SecretRef("secret_other", "user-b", "steam", "pin", "op-config"),
            SecretTarget("user-b", "steam", "pin"),
            "op-config",
            3,
            8,
        )
        with self.assertRaises(ValueError):
            staged.to_persisted(target, {"pin": wrong_target_receipt})
        for bad_receipt in (
            SecretReceipt(
                SecretRef("secret_wrong_module", "user", "other", "pin", "op-config"),
                SecretTarget("user", "other", "pin"),
                "op-config",
                3,
                9,
            ),
            SecretReceipt(
                SecretRef("secret_wrong_revision", "user", "steam", "pin", "op-config"),
                SecretTarget("user", "steam", "pin"),
                "op-config",
                4,
                10,
            ),
            SecretReceipt(
                SecretRef("secret_wrong_operation", "user", "steam", "pin", "other-op"),
                SecretTarget("user", "steam", "pin"),
                "other-op",
                3,
                11,
            ),
            SecretReceipt(
                SecretRef("secret_active", "user", "steam", "pin", "op-config"),
                SecretTarget("user", "steam", "pin"),
                "op-config",
                3,
                12,
                SecretReceiptState.ACTIVE,
            ),
        ):
            with self.assertRaises(ValueError):
                staged.to_persisted(target, {"pin": bad_receipt})
        snapshot = ConfigSnapshot(
            4,
            {"region": "cn"},
            (SecretMetadata("pin", ref, 4, SecretMetadataState.ACTIVE),),
        )
        self.assertEqual(snapshot.secret_metadata[0].revision, 4)
        with self.assertRaises(ValueError):
            ConfigSnapshot(4, {"PIN": "RAW_LEAK"})

    def test_cc_b03_04_rejects_urls_paths_headers_and_sensitive_values(self):
        with self.assertRaises(ValueError):
            SourceDeclaration("steam", "https://api.example.test")
        with self.assertRaises(ValueError):
            SourceDeclaration("steam", "api/../private")
        with self.assertRaises(ValueError):
            ConfigFieldUpdate("access_token", ConfigPatchMode.REPLACE, "secret")
        for field in ("api_key", "API-KEY", "cookie_value", "AUTHORIZATION"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                ConfigFieldUpdate(field, ConfigPatchMode.REPLACE, "RAW_LEAK")
        with self.assertRaises(ValueError):
            CollectionDescriptor("../private", 1, OwnershipKind.PUBLIC)
        requests = (
            lambda: HttpRequest("steam", "/path", headers={" Authorization": "x"}),
            lambda: HttpRequest(
                "steam", "/path", headers={"X-Trace": "x\r\nAuthorization: x"}
            ),
            lambda: HttpRequest("steam", "/%2e%2e/private"),
            lambda: HttpRequest("steam", "/%252e%252e/private"),
            lambda: HttpRequest("steam", "//authority/private"),
            lambda: HttpRequest("steam", "/path", headers={"Cookie": "x"}),
            lambda: HttpRequest("steam", "/．．/private"),
            lambda: HttpRequest("steam", "/／evil.example/private"),
            lambda: HttpRequest("steam", "/ok\x7f"),
            lambda: HttpRequest("steam", "/path", headers={"Proxy-Authorization": "x"}),
            lambda: HttpRequest("steam", "/path", headers={"Authentication": "x"}),
            lambda: HttpRequest("steam", "/path", headers={"X-Authorization": "x"}),
        )
        for build_request in requests:
            with self.subTest(request=build_request), self.assertRaises(ValueError):
                build_request()

        class ExplodingHeaders(dict):
            def items(self):
                raise RuntimeError("RAW_LEAK_FROM_HEADERS")

        with self.assertRaises(ValueError) as header_error:
            HttpRequest("steam", "/path", headers=ExplodingHeaders())
        self.assertEqual(
            str(header_error.exception),
            "HTTP credentials are managed by the declared source",
        )
        self.assertIsNone(header_error.exception.__cause__)
        self.assertIsNone(header_error.exception.__context__)
        self.assertNotIn(
            "RAW_LEAK", "".join(traceback.format_exception(header_error.exception))
        )

    def test_cc_b03_05_expiry_generation_and_uow_lifecycle_are_typed(self):
        expiry = datetime.now(UTC) + timedelta(minutes=5)
        principal = Principal("user-1", "qq", "10001")
        conversation = ConversationRef(
            "qq", ConversationKind.DIRECT, "chat-1", "route-1"
        )
        binding = Binding(
            "binding-1",
            1,
            "user-1",
            "steam",
            "game",
            "object-1",
            True,
            "user",
            "chat-1",
            "qq",
        )
        session = LoginSession(
            "login-1",
            principal.principal_id,
            "steam",
            2,
            LoginSessionStatus.PENDING,
            expiry,
        )
        self.assertEqual(session.generation, 2)
        self.assertEqual(conversation.kind, ConversationKind.DIRECT)
        self.assertEqual(binding.conversation_id, conversation.conversation_id)
        self.assertEqual(binding.conversation_key, ConversationKey("qq", "chat-1"))
        empty_default = BindingDefaultSnapshot(None, 0)
        self.assertEqual(empty_default.default_revision, 0)
        default_snapshot = BindingDefaultSnapshot(binding, 7)
        self.assertEqual(default_snapshot.binding, binding)
        self.assertEqual(default_snapshot.default_revision, 7)
        with self.assertRaises(ValueError):
            BindingDefaultSnapshot(None, 1)
        with self.assertRaises(ValueError):
            BindingDefaultSnapshot(binding, 0)
        with self.assertRaises(ValueError):
            BindingDefaultSnapshot(binding, -1)

        class FalsePositiveInt(int):
            def __lt__(self, other: object) -> bool:
                return False

            def __eq__(self, other: object) -> bool:
                return True

        with self.assertRaises(ValueError):
            BindingDefaultSnapshot(binding, FalsePositiveInt(-4))
        forged_zero_binding = object.__new__(Binding)
        for field, value in (
            ("binding_id", "forged-zero"),
            ("revision", 0),
            ("principal_id", "user-1"),
            ("module_id", "steam"),
            ("object_type", "game"),
            ("object_id", "object-1"),
            ("is_default", True),
            ("origin", "user"),
            ("conversation_id", "chat-1"),
            ("adapter_id", "qq"),
        ):
            object.__setattr__(forged_zero_binding, field, value)
        with self.assertRaises(ValueError):
            BindingDefaultSnapshot(forged_zero_binding, 1)
        evil_revision_binding = Binding(
            "evil-revision",
            FalsePositiveInt(-4),
            "user-1",
            "steam",
            "game",
            "object-1",
            True,
            "user",
            "chat-1",
            "qq",
        )
        with self.assertRaises(ValueError):
            BindingDefaultSnapshot(evil_revision_binding, 1)
        with self.assertRaises(ValueError):
            BindingDefaultSnapshot(
                Binding(
                    "non-default",
                    1,
                    "user-1",
                    "steam",
                    "game",
                    "object-1",
                    False,
                    "user",
                    "chat-1",
                    "qq",
                ),
                1,
            )
        conflict = RevisionConflict("binding_default", 2, 3)
        self.assertEqual(
            (conflict.code, conflict.expected_revision, conflict.actual_revision),
            ("revision_conflict", 2, 3),
        )
        with self.assertRaises(ValueError):
            Binding.validate_for_repository(
                Binding(
                    "legacy-binding",
                    1,
                    "user-1",
                    "steam",
                    "game",
                    "object-1",
                    True,
                    "user",
                    "chat-1",
                )
            )
        self.assertNotEqual(
            ConversationKey("qq", "chat-1"),
            ConversationKey("discord", "chat-1"),
        )
        self.assertTrue(get_type_hints(UnitOfWork.commit))
        bind_default_hints = get_type_hints(BindingRepository.bind_default)
        self.assertEqual(bind_default_hints["expected_binding_revision"], int)
        self.assertEqual(bind_default_hints["expected_default_revision"], int)
        self.assertEqual(bind_default_hints["return"], Binding)
        snapshot_hints = get_type_hints(BindingRepository.current_default_snapshot)
        self.assertEqual(snapshot_hints["return"], BindingDefaultSnapshot)
        completion_hints = get_type_hints(AuthorizationRepository.complete_login)
        self.assertEqual(completion_hints["expected_generation"], int)
        self.assertEqual(completion_hints["expected_grant_revision"], int | None)
        self.assertEqual(completion_hints["return"], tuple[LoginSession, Grant])
        self.assertTrue(get_type_hints(AuthorizationRepository.revoke_grant))
        with self.assertRaises(ValueError):
            LoginSession(
                "login-1", "user-1", "steam", 0, LoginSessionStatus.PENDING, expiry
            )
        for protocol, method in (
            (BindingRepository, "bind_default"),
            (BindingRepository, "replace_default"),
            (GrantStore, "rotate_grant"),
            (AuthorizationRepository, "complete_login"),
            (AuthorizationRepository, "revoke_grant"),
            (LoginSessionRepository, "expire_pending_on_restart"),
            (IdentityRepository, "find_principal"),
            (IdentityRepository, "current_identity"),
            (BindingRepository, "current_default"),
            (BindingRepository, "current_default_snapshot"),
            (BindingRepository, "clear_default"),
            (PortCacheRepository, "current_revision"),
            (ResourceRepository, "metadata"),
            (ResourceRepository, "current_revision"),
            (SafeFileStore, "recover_orphans"),
            (PortCacheRepository, "invalidate"),
            (GrantRevocationCoordinator, "revoke_grant_with_invalidation"),
            (ConfigRepository, "metadata"),
            (BindingRepository, "list_for"),
        ):
            self.assertTrue(
                get_type_hints(getattr(protocol, method)), (protocol, method)
            )
        repository_hints = get_type_hints(ConfigRepository.update)
        self.assertNotIn("SecretMaterial", str(repository_hints))
        self.assertNotIn("bytes", str(repository_hints))

    def test_cc_b03_06_subscription_placeholder_has_stable_semantics(self):
        error = SubscriptionUnavailable()
        self.assertEqual(error.code, "subscription_unavailable")
        self.assertEqual(str(error), error.message)
        self.assertEqual(
            str(signature(SubscriptionOperations.create)),
            "(self, invocation: 'InvocationView', subscription: 'SubscriptionView') -> 'SubscriptionView'",
        )
        self.assertEqual(
            str(signature(SubscriptionOperations.revise)),
            "(self, invocation: 'InvocationView', subscription: 'SubscriptionView') -> 'SubscriptionView'",
        )
        self.assertEqual(
            str(signature(SubscriptionOperations.list_current)),
            "(self, invocation: 'InvocationView') -> 'tuple[SubscriptionView, ...]'",
        )
        self.assertEqual(
            str(signature(SubscriptionOperations.cancel)),
            "(self, invocation: 'InvocationView', subscription_id: 'str', *, expected_revision: 'int') -> 'None'",
        )

    def test_cc_b03_05_registration_lookup_and_adapter_scoped_identity(self):
        class Lookup:
            def __init__(self):
                self.records = {
                    "steam": ModuleRegistrationSnapshot("steam", True, 7, 3),
                    "disabled": ModuleRegistrationSnapshot("disabled", False, 8, 4),
                }

            async def require_registered(self, module_id):
                if module_id not in self.records:
                    raise ModuleNotRegistered(module_id)
                return self.records[module_id]

        lookup = Lookup()
        registered = asyncio.run(lookup.require_registered("steam"))
        self.assertEqual(registered.registry_revision, 7)
        self.assertTrue(registered.enabled)
        disabled = asyncio.run(lookup.require_registered("disabled"))
        self.assertFalse(disabled.enabled)
        with self.assertRaises(ModuleNotRegistered):
            asyncio.run(lookup.require_registered("unknown"))
        fabricated = ModuleRegistrationSnapshot("unknown", True, 99, 99)
        self.assertNotEqual(fabricated, lookup.records.get("unknown"))
        self.assertTrue(get_type_hints(ModuleRegistrationLookup.require_registered))

    def test_cc_b03_05_registry_global_module_id_flows_through_contract(self):
        declared_collection = CollectionDescriptor(
            "items",
            1,
            OwnershipKind.USER,
            (CollectionIndex("by_name", "name"),),
        )

        class EvilSchemaVersion(int):
            def __eq__(self, other):
                return True

            __hash__ = int.__hash__

        with self.assertRaises(ValueError):
            CollectionDescriptor("items", EvilSchemaVersion(999), OwnershipKind.USER)
        module = ModuleManifest(
            "module-a",
            "route_a",
            ModuleCategory.GAME,
            "demo.factory:create",
            "1.0.0",
            (),
            collections=(declared_collection,),
        )
        package = PackageManifest(
            "package",
            "1.0.0",
            "1.1.0",
            (module,),
            "author",
            "MIT",
            "https://example.test/package",
        )

        class RegistryBackedLookup:
            async def require_registered(self, requested_module_id):
                current = registry.snapshot()
                current_module = current.module(requested_module_id)
                return ModuleRegistrationSnapshot(
                    current_module.module_id,
                    current_module.enabled,
                    current.revision,
                    current_module.epoch,
                    current_module.manifest.collections,
                )

        registry = Registry()
        snapshot = registry.register_package(
            package,
            {"module-a": ModuleHandlers({}, {}, {})},
        )
        registered = snapshot.module("package/module-a")
        registration = asyncio.run(
            RegistryBackedLookup().require_registered(registered.module_id)
        )
        module_id = registration.module_id
        self.assertEqual(registration.module_id, "package/module-a")
        self.assertEqual(registration.collections, (declared_collection,))
        self.assertIn(declared_collection, registration.collections)
        self.assertNotIn(
            CollectionDescriptor("items", 2, OwnershipKind.USER),
            registration.collections,
        )
        self.assertNotIn(
            CollectionDescriptor("other", 1, OwnershipKind.USER),
            registration.collections,
        )
        forged_descriptor = object.__new__(CollectionDescriptor)
        object.__setattr__(forged_descriptor, "name", "items")
        object.__setattr__(forged_descriptor, "schema_version", EvilSchemaVersion(999))
        object.__setattr__(forged_descriptor, "owner_kind", OwnershipKind.USER)
        object.__setattr__(forged_descriptor, "indexes", ())
        object.__setattr__(forged_descriptor, "retention_category", "standard")
        with self.assertRaises(ValueError):
            ModuleRegistrationSnapshot(
                module_id,
                True,
                registration.registry_revision,
                registration.epoch,
                (forged_descriptor,),
            )
        forged_extra = CollectionDescriptor("forged", 1, OwnershipKind.USER)
        forged_snapshot = ModuleRegistrationSnapshot(
            module_id,
            True,
            registration.registry_revision,
            registration.epoch,
            registration.collections + (forged_extra,),
        )
        self.assertIn(forged_extra, forged_snapshot.collections)
        self.assertNotIn(forged_extra, registration.collections)

        def consume_collection(snapshot, descriptor, revision, epoch):
            if snapshot.registry_revision != revision or snapshot.epoch != epoch:
                raise ValueError("module registration snapshot is stale")
            descriptor = CollectionDescriptor.validate(descriptor)
            if descriptor not in snapshot.collections:
                raise ValueError("collection is not declared by the registered module")
            return descriptor

        self.assertEqual(
            consume_collection(
                registration,
                declared_collection,
                registration.registry_revision,
                registration.epoch,
            ),
            declared_collection,
        )
        refreshed_registry_snapshot = registry.set_enabled(module_id, True)
        refreshed = asyncio.run(RegistryBackedLookup().require_registered(module_id))
        self.assertTrue(refreshed.enabled)
        self.assertEqual(
            refreshed.registry_revision, refreshed_registry_snapshot.revision
        )
        # Registry is a directory projection; only Lifecycle allocates run epochs.
        self.assertEqual(refreshed.epoch, registered.epoch)
        with self.assertRaises(ValueError):
            consume_collection(
                registration,
                declared_collection,
                refreshed.registry_revision,
                refreshed.epoch,
            )
        with self.assertRaises(ValueError):
            consume_collection(
                refreshed,
                CollectionDescriptor("items", 2, OwnershipKind.USER),
                refreshed.registry_revision,
                refreshed.epoch,
            )
        with self.assertRaises(ValueError):
            consume_collection(
                refreshed,
                CollectionDescriptor(
                    "items",
                    1,
                    OwnershipKind.USER,
                    (CollectionIndex("by_other", "name"),),
                ),
                refreshed.registry_revision,
                refreshed.epoch,
            )
        self.assertEqual(ConfigTarget("principal", module_id).module_id, module_id)
        binding = Binding(
            "binding",
            1,
            "principal",
            module_id,
            "object",
            "object-id",
            True,
            "manual",
            "conversation",
            "adapter",
        )
        self.assertEqual(binding.module_id, module_id)
        self.assertEqual(
            SecretTarget("principal", module_id, "credential").module_id, module_id
        )
        self.assertEqual(
            SecretOwner("principal", module_id, "credential", "op").module_id,
            module_id,
        )
        self.assertEqual(
            SecretRef(
                "secret_issued", "principal", module_id, "credential", "op"
            ).module_id,
            module_id,
        )
        self.assertEqual(
            Grant(
                "grant",
                1,
                "principal",
                module_id,
                "account",
                ("read",),
                None,
                GrantStatus.ACTIVE,
            ).module_id,
            module_id,
        )
        expiry = datetime.now(UTC) + timedelta(minutes=5)
        self.assertEqual(
            LoginSession(
                "session",
                "principal",
                module_id,
                1,
                LoginSessionStatus.PENDING,
                expiry,
            ).module_id,
            module_id,
        )
        admin_status = ModuleStatus(
            module_id,
            True,
            ModuleLifecycle.ACTIVE,
            ModuleHealth.HEALTHY,
            registered.epoch,
            snapshot.revision,
        )
        self.assertEqual(ConfigSummary(module_id, 1, {}).module_id, module_id)
        self.assertEqual(
            ModuleAdminSnapshot(admin_status, ConfigSummary(module_id, 1, {})).status,
            admin_status,
        )

        class EvilModuleId(str):
            def __eq__(self, other):
                return True

            __hash__ = str.__hash__

        evil = EvilModuleId(module_id)
        with self.assertRaises(ValueError):
            ModuleRegistrationSnapshot(evil, True, 1, 1)
        with self.assertRaises(ValueError):
            ConfigTarget("principal", evil)
        with self.assertRaises(ValueError):
            Binding(
                "binding",
                1,
                "principal",
                evil,
                "object",
                "object-id",
                True,
                "manual",
                "conversation",
                "adapter",
            )
        with self.assertRaises(ValueError):
            SecretTarget("principal", evil, "credential")
        with self.assertRaises(ValueError):
            SecretRef("secret_issued", "principal", evil, "credential", "op")
        with self.assertRaises(ValueError):
            SecretOwner("principal", evil, "credential", "op")

        invalid_ids = (
            "",
            "/module",
            "package/",
            "package//module",
            "package/../module",
            "package/module/extra",
            "package\\module",
            "package%2fmodule",
        )
        for invalid in invalid_ids:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    ModuleRegistrationSnapshot(invalid, True, 1, 1)
                with self.assertRaises(ValueError):
                    ConfigTarget("principal", invalid)
                with self.assertRaises(ValueError):
                    Binding(
                        "binding",
                        1,
                        "principal",
                        invalid,
                        "object",
                        "object-id",
                        True,
                        "manual",
                        "conversation",
                        "adapter",
                    )
                with self.assertRaises(ValueError):
                    SecretTarget("principal", invalid, "credential")
                with self.assertRaises(ValueError):
                    SecretOwner("principal", invalid, "credential", "op")
                with self.assertRaises(ValueError):
                    SecretRef("secret_issued", "principal", invalid, "credential", "op")
                with self.assertRaises(ValueError):
                    ModuleStatus(
                        invalid,
                        True,
                        ModuleLifecycle.ACTIVE,
                        ModuleHealth.HEALTHY,
                        1,
                        1,
                    )
                with self.assertRaises(ValueError):
                    ConfigSummary(invalid, 1, {})

    def test_cc_b03_07_secret_store_compensation_and_cache_resource_scope(self):
        for method in (
            "stage",
            "put",
            "delete",
            "read",
            "mark_orphan",
            "pending",
            "recover",
        ):
            self.assertTrue(get_type_hints(getattr(SecretStore, method)), method)
        owner = SecretOwner("user", "steam", "credential", "op-1")
        secret_ref = SecretRef("secret_new", "user", "steam", "credential", "op-1")
        receipt = SecretReceipt(
            secret_ref,
            SecretTarget("user", "steam", "credential"),
            "op-1",
            1,
            1,
        )
        staged = SecretTransition(
            SecretCompensationState.STAGED,
            secret_ref,
            operation_id="op-1",
            owner=owner,
        )
        orphan = SecretTransition(
            SecretCompensationState.ORPHAN,
            secret_ref,
            operation_id="op-1",
            owner=owner,
        )
        self.assertEqual(staged.state, SecretCompensationState.STAGED)
        self.assertEqual(orphan.secret_ref, secret_ref)
        with self.assertRaises(ValueError):
            SecretTransition(SecretCompensationState.STAGED)
        with self.assertRaises(TypeError):
            SecretTransition(
                SecretCompensationState.STAGED,
                "secret_attacker_chosen",  # type: ignore[arg-type]
                operation_id="op-1",
                owner=owner,
            )
        with self.assertRaises(ValueError):
            SecretTransition(
                SecretCompensationState.STAGED,
                secret_ref,
                operation_id="other-op",
                owner=owner,
            )
        with self.assertRaises(ValueError):
            SecretTransition(
                SecretCompensationState.DELETE_FAILED,
                operation_id="op-1",
                owner=owner,
            )
        self.assertEqual(SecretRef.validate(secret_ref), secret_ref)
        self.assertEqual(SecretReceipt.validate(receipt), receipt)
        self.assertEqual(copy.copy(receipt), receipt)
        self.assertEqual(copy.deepcopy(receipt), receipt)
        self.assertEqual(pickle.loads(pickle.dumps(receipt)), receipt)

        class FakeLedger:
            def __init__(self, state=None):
                state = {} if state is None else state
                self.issued = state.setdefault("issued", {})
                self.claimed = state.setdefault("claimed", set())

            async def stage(
                self, value, *, target, operation_id, expected_config_revision
            ):
                if not isinstance(value, bytes) or not value:
                    raise ValueError("stage requires opaque bytes")
                token = f"secret_issued_{len(self.issued) + 1}"
                staged_receipt = SecretReceipt(
                    SecretRef(
                        token,
                        target.principal_id,
                        target.module_id,
                        target.field,
                        operation_id,
                    ),
                    target,
                    operation_id,
                    expected_config_revision,
                    len(self.issued) + 1,
                )
                self.issued[token] = staged_receipt
                return staged_receipt

            async def claim_for_config(
                self,
                candidate,
                *,
                target,
                operation_id,
                expected_config_revision,
                expected_ledger_revision,
            ):
                candidate = SecretReceipt.validate(candidate)
                issued = self.issued.get(candidate.secret_ref.token)
                if (
                    issued is None
                    or candidate != issued
                    or candidate.secret_ref.token in self.claimed
                    or candidate.state is not SecretReceiptState.STAGED
                    or candidate.target != target
                    or candidate.operation_id != operation_id
                    or candidate.expected_config_revision != expected_config_revision
                    or candidate.ledger_revision != expected_ledger_revision
                ):
                    raise ValueError("secret receipt claim rejected")
                self.claimed.add(candidate.secret_ref.token)
                return ClaimedSecretReceipt(
                    candidate.secret_ref,
                    candidate.target,
                    candidate.operation_id,
                    candidate.expected_config_revision,
                    candidate.ledger_revision,
                )

        class SpyRepository:
            def __init__(self):
                self.calls = 0
                self.target = None
                self.patch = None

            async def update(
                self, target: ConfigTarget, patch: PersistedConfigPatch
            ) -> ConfigSnapshot:
                if not isinstance(target, ConfigTarget):
                    raise TypeError("repository target type mismatch")
                if not isinstance(patch, PersistedConfigPatch):
                    raise TypeError("repository patch type mismatch")
                self.calls += 1
                self.target = target
                self.patch = patch
                return ConfigSnapshot(2, {}, target=target)

        config_target = ConfigTarget("user", "steam")
        secret_target = SecretTarget("user", "steam", "credential")
        persisted_template = ConfigPatch(
            1,
            (
                ConfigFieldUpdate(
                    "credential",
                    ConfigPatchMode.REPLACE,
                    secret=SecretMaterial(b"opaque"),
                ),
            ),
            (ConfigField("credential", sensitive=True),),
            "op-1",
        )

        async def claim_then_cas(candidate, ledger, repository, persisted_patch):
            claimed = await ledger.claim_for_config(
                candidate,
                target=secret_target,
                operation_id="op-1",
                expected_config_revision=1,
                expected_ledger_revision=1,
            )
            self.assertIsInstance(claimed, ClaimedSecretReceipt)
            await repository.update(config_target, persisted_patch)

        ledger = FakeLedger()
        repository = SpyRepository()
        unissued_patch = persisted_template.to_persisted(
            config_target,
            {
                "credential": SecretReceipt(
                    SecretRef("secret_unissued", "user", "steam", "credential", "op-1"),
                    secret_target,
                    "op-1",
                    1,
                    1,
                )
            },
        )
        with self.assertRaises(ValueError):
            asyncio.run(claim_then_cas(receipt, ledger, repository, unissued_patch))
        self.assertEqual(repository.calls, 0)
        issued_receipt = asyncio.run(
            ledger.stage(
                b"opaque",
                target=secret_target,
                operation_id="op-1",
                expected_config_revision=1,
            )
        )
        persisted_patch = persisted_template.to_persisted(
            config_target, {"credential": issued_receipt}
        )
        asyncio.run(claim_then_cas(issued_receipt, ledger, repository, persisted_patch))
        self.assertEqual(repository.calls, 1)
        self.assertIs(repository.target, config_target)
        self.assertIs(repository.patch, persisted_patch)

        async def validate_then_cas(target, patch):
            checked = PersistedConfigPatch.validate_for(target, patch)
            await repository.update(target, checked)

        with self.assertRaises(ValueError):
            asyncio.run(
                validate_then_cas(ConfigTarget("user", "other"), persisted_patch)
            )
        self.assertEqual(repository.calls, 1)
        with self.assertRaises(ValueError):
            asyncio.run(
                claim_then_cas(issued_receipt, ledger, repository, persisted_patch)
            )
        self.assertEqual(repository.calls, 1)
        wrong_target = SecretReceipt(
            SecretRef(
                issued_receipt.secret_ref.token,
                "other-user",
                "steam",
                "credential",
                "op-1",
            ),
            SecretTarget("other-user", "steam", "credential"),
            "op-1",
            1,
            issued_receipt.ledger_revision,
        )
        with self.assertRaises(ValueError):
            asyncio.run(
                claim_then_cas(wrong_target, ledger, repository, persisted_patch)
            )
        self.assertEqual(repository.calls, 1)
        restored_ledger = FakeLedger(
            {"issued": ledger.issued, "claimed": ledger.claimed}
        )
        with self.assertRaises(ValueError):
            asyncio.run(
                claim_then_cas(
                    pickle.loads(pickle.dumps(issued_receipt)),
                    restored_ledger,
                    repository,
                    persisted_patch,
                )
            )
        self.assertTrue(get_type_hints(SecretReceiptLedger.claim_for_config))
        scope = OwnerScope.user("user-1")
        request = CacheLookup(CacheLookupStatus.MISS)
        self.assertEqual(request.status, CacheLookupStatus.MISS)
        entry = CacheEntry(
            "key", {"value": 1}, datetime.now(UTC) + timedelta(seconds=30)
        )
        hit = CacheLookup(CacheLookupStatus.HIT, entry)
        self.assertEqual(hit.entry, entry)
        metadata = ResourceMetadata("asset-1", "image/png", scope, 10, temporary=True)
        self.assertEqual(metadata.scope.user_id, "user-1")
        self.assertEqual(CacheVisibility.USER.value, "user")
        forged = object.__new__(OwnerScope)
        object.__setattr__(forged, "kind", OwnershipKind.AUTHORIZED)
        object.__setattr__(forged, "user_id", "user-1")
        object.__setattr__(forged, "grant", None)
        with self.assertRaises(ValueError):
            validate_cache_access_request(
                CacheAccessRequest("key", CacheVisibility.AUTHORIZED, forged)
            )
        nested = object.__new__(GrantReference)
        object.__setattr__(nested, "grant_id", "bad\nidentifier")
        object.__setattr__(nested, "revision", 0)
        forged_nested_scope = object.__new__(OwnerScope)
        object.__setattr__(forged_nested_scope, "kind", OwnershipKind.AUTHORIZED)
        object.__setattr__(forged_nested_scope, "user_id", "user-1")
        object.__setattr__(forged_nested_scope, "grant", nested)
        with self.assertRaises(ValueError):
            validate_cache_access_request(
                CacheAccessRequest(
                    "key", CacheVisibility.AUTHORIZED, forged_nested_scope
                )
            )
        metadata = ResourceMetadata("asset-2", "image/png", scope, 4, revision=2)
        stage = FileStage("op-file", "asset-2", scope, 4, metadata)
        self.assertEqual(stage.scope, scope)
        replacement = OwnerScope.user("other-user")
        object.__setattr__(metadata, "scope", replacement)
        self.assertEqual(stage.scope.user_id, "user-1")
        self.assertEqual(entry.revision, 1)
        self.assertEqual(metadata.revision, 2)

    def test_cc_b03_01_old_b02_manifests_remain_compatible(self):
        capability = CapabilityDescriptor(
            "lookup",
            {"type": "object"},
            InvocationPolicy.COMMAND_ONLY,
            CapabilityEffect.READ_ONLY,
            output_version="1.0.0",
        )
        module = ModuleManifest(
            "demo",
            "demo",
            ModuleCategory.GAME,
            "demo:factory",
            "1.0.0",
            (capability,),
        )
        package = PackageManifest(
            "demo",
            "1.0.0",
            "1.0.0",
            (module,),
            "author",
            "license",
            "source",
        )
        self.assertEqual(package.contract_version, "1.0.0")


if __name__ == "__main__":
    unittest.main()
