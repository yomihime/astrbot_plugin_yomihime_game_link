"""Synthetic in-memory diagnostics; never consult a running Host environment."""

import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ygl_test_subject.infrastructure.key_provider import (
    EnvironmentKeyProvider,
    SecretKeyUnavailable,
)
from ygl_test_subject.infrastructure.secret_codec import AESGCMSecretCodec
from ygl_test_subject.infrastructure.secret_store import SQLiteSecretStore


class EncryptionDiagnosticsTests(unittest.TestCase):
    def store(self, codec):
        # Deliberately no database/root: any persisted-secret access fails the test.
        store = object.__new__(SQLiteSecretStore)
        store.codec = codec
        return store

    def check(self, result, key_state, codec_state):
        self.assertEqual(set(result), {"ready", "state", "reason_code", "diagnostics"})
        self.assertEqual(
            result["diagnostics"],
            {"key_state": key_state, "codec_state": codec_state},
        )
        ready = codec_state == "available"
        self.assertIs(result["ready"], ready)
        self.assertEqual(result["state"], "ready" if ready else "unavailable")
        self.assertEqual(
            result["reason_code"], "ready" if ready else "secret_encryption_unavailable"
        )
        self.assertNotIn("PRIVATE-SYNTHETIC", json.dumps(result))

    def test_environment_classification_is_strict_single_read_and_has_no_io(self):
        valid = base64.b64encode(bytes(range(32))).decode()
        cases = [
            (None, "not_provided"),
            ("", "not_provided"),
            ("!PRIVATE-SYNTHETIC!", "invalid_encoding_or_length"),
            ("中文", "invalid_encoding_or_length"),
            (valid[:-1], "invalid_encoding_or_length"),
            (valid + "\n", "invalid_encoding_or_length"),
            (base64.b64encode(bytes(31)).decode(), "invalid_encoding_or_length"),
            (base64.b64encode(bytes(33)).decode(), "invalid_encoding_or_length"),
            (valid, "valid_format"),
        ]
        for index, (encoded, expected) in enumerate(cases):
            with self.subTest(case=index):
                env = Mock()
                env.get.return_value = encoded
                store = self.store(
                    AESGCMSecretCodec(EnvironmentKeyProvider("TEST_KEY"))
                )
                with (
                    patch(
                        "ygl_test_subject.infrastructure.key_provider.os.environ", env
                    ),
                    patch("builtins.open", side_effect=AssertionError("unexpected IO")),
                ):
                    result = store.encryption_readiness(diagnostics=True)
                env.get.assert_called_once_with("TEST_KEY")
                self.check(
                    result,
                    expected,
                    "available" if expected == "valid_format" else "not_run",
                )
                if encoded:
                    self.assertFalse(encoded in json.dumps(result))

    def test_same_key_is_used_for_whole_probe_and_not_cached_for_later_call(self):
        provider = Mock()
        provider.get_key.side_effect = [
            bytes(range(32)),
            RuntimeError("PRIVATE-SYNTHETIC"),
        ]
        store = self.store(AESGCMSecretCodec(provider))
        self.check(
            store.encryption_readiness(diagnostics=True), "valid_format", "available"
        )
        self.assertEqual(provider.get_key.call_count, 1)
        self.check(
            store.encryption_readiness(diagnostics=True),
            "cannot_safely_confirm",
            "not_run",
        )
        self.assertEqual(provider.get_key.call_count, 2)

    def test_unknown_provider_errors_and_invalid_return_types_never_claim_missing(self):
        for index, value in enumerate(
            [
                RuntimeError("PRIVATE-SYNTHETIC"),
                SecretKeyUnavailable(),
                "PRIVATE-SYNTHETIC",
                None,
                bytes(31),
                bytearray(32),
            ]
        ):
            with self.subTest(case=index):
                provider = Mock()
                if isinstance(value, Exception):
                    provider.get_key.side_effect = value
                else:
                    provider.get_key.return_value = value
                self.check(
                    self.store(AESGCMSecretCodec(provider)).encryption_readiness(
                        diagnostics=True
                    ),
                    "cannot_safely_confirm",
                    "not_run",
                )
                provider.get_key.assert_called_once()

    def test_component_and_every_self_test_failure_are_closed_and_non_echoing(self):
        provider = Mock()
        provider.get_key.return_value = bytes(range(32))
        store = self.store(AESGCMSecretCodec(provider))
        with patch(
            "ygl_test_subject.infrastructure.secret_codec.AESGCM",
            side_effect=RuntimeError("PRIVATE-SYNTHETIC"),
        ):
            self.check(
                store.encryption_readiness(diagnostics=True),
                "valid_format",
                "component_unavailable",
            )
        probe = b"yomihime:encryption-readiness:v1"
        for index, (encrypted, decrypted) in enumerate(
            [
                (RuntimeError("PRIVATE-SYNTHETIC"), probe),
                (None, probe),
                (probe, probe),
                (b"cipher", RuntimeError("PRIVATE-SYNTHETIC")),
                (b"cipher", b"wrong"),
                (b"cipher", bytearray(probe)),
            ]
        ):
            with self.subTest(case=index):
                cipher = Mock()
                for name, value in [("encrypt", encrypted), ("decrypt", decrypted)]:
                    if isinstance(value, Exception):
                        getattr(cipher, name).side_effect = value
                    else:
                        getattr(cipher, name).return_value = value
                with patch(
                    "ygl_test_subject.infrastructure.secret_codec.AESGCM",
                    return_value=cipher,
                ) as constructor:
                    self.check(
                        store.encryption_readiness(diagnostics=True),
                        "valid_format",
                        "self_test_failed",
                    )
                constructor.assert_called_once()
        with patch(
            "ygl_test_subject.infrastructure.secret_codec.os.urandom",
            side_effect=RuntimeError("PRIVATE-SYNTHETIC"),
        ):
            self.check(
                store.encryption_readiness(diagnostics=True),
                "valid_format",
                "self_test_failed",
            )

    def test_absent_opaque_codec_and_unexpected_projection_are_not_key_guesses(self):
        class BrokenCodec:
            @property
            def encrypt(self):
                raise RuntimeError("PRIVATE-SYNTHETIC")

        self.check(
            self.store(BrokenCodec()).encryption_readiness(diagnostics=True),
            "cannot_safely_confirm",
            "unknown",
        )
        for codec in [
            None,
            object(),
            SimpleNamespace(encrypt=None, decrypt=lambda _: b""),
        ]:
            self.check(
                self.store(codec).encryption_readiness(diagnostics=True),
                "cannot_safely_confirm",
                "component_unavailable",
            )
        good = SimpleNamespace(
            encrypt=lambda p: b"wrapped:" + p, decrypt=lambda c: c[8:]
        )
        self.check(
            self.store(good).encryption_readiness(diagnostics=True),
            "cannot_safely_confirm",
            "available",
        )
        bad = SimpleNamespace(encrypt=lambda p: p, decrypt=lambda p: p)
        self.check(
            self.store(bad).encryption_readiness(diagnostics=True),
            "cannot_safely_confirm",
            "self_test_failed",
        )
        store = self.store(AESGCMSecretCodec(Mock()))
        for result in [
            ("PRIVATE-SYNTHETIC", "available"),
            ([], "available"),
            ("not_provided", "available"),
        ]:
            with patch.object(
                AESGCMSecretCodec, "encryption_diagnostics", return_value=result
            ):
                self.check(
                    store.encryption_readiness(diagnostics=True),
                    "cannot_safely_confirm",
                    "unknown",
                )
        with patch.object(
            AESGCMSecretCodec,
            "encryption_diagnostics",
            side_effect=RuntimeError("PRIVATE-SYNTHETIC"),
        ):
            self.check(
                store.encryption_readiness(diagnostics=True),
                "cannot_safely_confirm",
                "unknown",
            )

    def test_default_three_fields_and_sanitized_exception_contract_stay_unchanged(self):
        provider = Mock()
        provider.get_key.return_value = bytes(range(32))
        store = self.store(AESGCMSecretCodec(provider))
        self.assertEqual(
            store.encryption_readiness(),
            {"ready": True, "state": "ready", "reason_code": "ready"},
        )
        self.assertEqual(
            self.store(None).encryption_readiness(),
            {
                "ready": False,
                "state": "unavailable",
                "reason_code": "secret_encryption_unavailable",
            },
        )
        for value in [1, None, "true", {}]:
            with self.assertRaises(ValueError):
                store.encryption_readiness(diagnostics=value)
        error = SecretKeyUnavailable(reason="not_provided")
        self.assertEqual(str(error), "secret encryption key is unavailable")
        self.assertEqual(error.args, ("secret encryption key is unavailable",))
        with self.assertRaisesRegex(ValueError, "invalid secret key failure category"):
            SecretKeyUnavailable(reason="PRIVATE-SYNTHETIC")

    def test_shared_payload_faults_and_subclass_overrides_are_not_bypassed(self):
        provider = Mock()
        provider.get_key.return_value = bytes(range(32))
        codec = AESGCMSecretCodec(provider)
        store = self.store(codec)
        for method in ("_encrypt_payload", "_decrypt_payload"):
            with (
                self.subTest(method=method),
                patch.object(
                    AESGCMSecretCodec,
                    method,
                    side_effect=RuntimeError("PRIVATE-SYNTHETIC"),
                ),
            ):
                self.check(
                    store.encryption_readiness(diagnostics=True),
                    "valid_format",
                    "self_test_failed",
                )
                self.assertIs(store.encryption_readiness()["ready"], False)
        for index, (encrypt, decrypt) in enumerate(
            [
                (lambda _, value: value, lambda _, value: value),
                (lambda _, value: None, lambda _, value: value),
                (lambda _, value: b"wrapped:" + value, lambda _, value: b"wrong"),
                (
                    lambda _, value: b"wrapped:" + value,
                    lambda _, value: bytearray(value[8:]),
                ),
            ]
        ):
            with self.subTest(case=index):
                subclass = type(
                    "FaultyCodec",
                    (AESGCMSecretCodec,),
                    {"encrypt": encrypt, "decrypt": decrypt},
                )
                self.check(
                    self.store(subclass(provider)).encryption_readiness(
                        diagnostics=True
                    ),
                    "cannot_safely_confirm",
                    "self_test_failed",
                )
