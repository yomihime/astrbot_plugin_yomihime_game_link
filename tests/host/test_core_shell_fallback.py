"""Actual thin shell/cache/private Core/SQLite with offline Host adapter seams."""

import base64
import hashlib
import importlib
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from ygl_test_subject.infrastructure.sqlite.migrations import discover_migrations

from tests.extensions.test_private_runtime_bundle import _release, _write
from tests.host.test_external_packages import _Font, _Transport
from tests.host.test_private_core_integration import runtime_files


class CoreShellFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, changed_migration):
        with tempfile.TemporaryDirectory(prefix="ygl-core-shell-") as temp:
            base = Path(temp)
            installation, data = base / "install", base / "data"
            installation.mkdir()

            class Star:
                def __init__(self, context, config=None):
                    pass

                async def initialize(self):
                    pass

                async def terminate(self):
                    pass

            class Filters:
                def __getattr__(self, name):
                    return lambda *args, **kwargs: lambda callback: callback

            star_module = types.ModuleType("astrbot.api.star")
            star_module.Star = Star
            star_module.register = lambda *args, **kwargs: lambda cls: cls
            star_module.StarTools = SimpleNamespace(get_data_dir=lambda name: data)
            event_module = types.ModuleType("astrbot.api.event")
            event_module.AstrMessageEvent = object
            event_module.filter = Filters()
            with patch.dict(
                sys.modules,
                {"astrbot.api.star": star_module, "astrbot.api.event": event_module},
            ):
                sys.modules.pop("ygl_test_subject.main", None)
                shell = importlib.import_module("ygl_test_subject.main")
            self.addCleanup(sys.modules.pop, "ygl_test_subject.main", None)
            key = Ed25519PrivateKey.generate()
            public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            key_id = hashlib.sha256(public).hexdigest()
            files = runtime_files()
            files["ygl-plugin.json"] = json.dumps(
                {
                    "schema": "ygl.plugin/1",
                    "role": "core",
                    "package_id": "ygl_core",
                    "manifest": "release.json",
                    "control_protocol": {"name": "ygl.control", "major": 1},
                }
            ).encode()
            files["release-key.json"] = json.dumps(
                {
                    "algorithm": "ed25519",
                    "key_id": key_id,
                    "public_key_base64": base64.b64encode(public).decode(),
                }
            ).encode()

            def write(version):
                _write(installation, files)
                release = _release(files, package_id="ygl_core")
                release.update(
                    release=version,
                    release_sequence=1 if version == "1.0.0" else 2,
                    plugin_id={
                        "author": "yomihime",
                        "name": "astrbot_plugin_yomihime_game_link",
                    },
                )
                raw = json.dumps(release).encode()
                (installation / "release.json").write_bytes(raw)
                (installation / "release.sig.json").write_text(
                    json.dumps(
                        {
                            "algorithm": "ed25519",
                            "key_id": key_id,
                            "signature_base64": base64.b64encode(
                                key.sign(raw)
                            ).decode(),
                        }
                    )
                )

            real_import = importlib.import_module

            class Pages:
                def __init__(self, *args):
                    pass

                def register(self):
                    pass

                def close(self):
                    pass

            def controlled_import(name, *args, **kwargs):
                if name.endswith(
                    (".adapters.astrbot.public_pages", ".adapters.astrbot.admin_pages")
                ):
                    return SimpleNamespace(PublicPages=Pages, AdminPages=Pages)
                module = real_import(name, *args, **kwargs)
                if name.endswith(".adapters.astrbot.runtime"):
                    actual = module.AstrBotRuntime

                    def runtime(*args, **kwargs):
                        return actual(
                            *args,
                            **kwargs,
                            font_manager_factory=_Font,
                            bundled_selection=None,
                            http_transport_factory=_Transport,
                            plain_factory=lambda text: text,
                            chain_factory=lambda chain: list(chain),
                        )

                    return SimpleNamespace(AstrBotRuntime=runtime)
                return module

            context = SimpleNamespace(
                get_all_stars=lambda: (), get_registered_star=lambda name: None
            )

            def new_star():
                with patch.dict(sys.modules, {"astrbot.api.star": star_module}):
                    instance = shell.YomihimeGameLink(context)
                instance._plugin_root = installation
                return instance

            write("1.0.0")
            first = new_star()
            with patch.object(
                shell.importlib, "import_module", side_effect=controlled_import
            ):
                await first._start_generation()
            self.assertEqual(first._state, "active")
            database = Path(first._runtime._core.database.path).resolve(strict=True)
            self.assertTrue(database.is_file())
            first_epoch = first._generation.receipt["core_epoch"]
            await first.terminate()
            source = files["services/core_runtime.py"]
            marker = b") -> CoreStartupReport:\n"
            self.assertIn(marker, source)
            files["services/core_runtime.py"] = source.replace(
                marker,
                marker
                + b'        raise RuntimeError("injected signed candidate start failure")\n',
                1,
            )
            if changed_migration:
                migration_dir = "infrastructure/sqlite/migrations"
                latest = discover_migrations(installation / migration_dir)[-1]
                probe_file = f"{latest.version + 1:04d}_fallback_probe.sql"
                files[f"{migration_dir}/{probe_file}"] = (
                    b"CREATE TABLE fallback_probe(id INTEGER);"
                )
            write("1.0.1")
            successor = new_star()
            try:
                with patch.object(
                    shell.importlib, "import_module", side_effect=controlled_import
                ):
                    if changed_migration:
                        with self.assertRaisesRegex(
                            RuntimeError, "injected signed candidate"
                        ):
                            await successor._start_generation()
                        self.assertEqual(successor._state, "failed")
                        from contextlib import closing

                        with closing(
                            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                        ) as connection:
                            self.assertIsNotNone(
                                connection.execute(
                                    "SELECT name FROM sqlite_master WHERE name='fallback_probe'"
                                ).fetchone()
                            )
                    else:
                        await successor._start_generation()
                        self.assertEqual(successor._state, "fallback")
                        self.assertEqual(successor._installed_version, "1.0.1")
                        self.assertEqual(successor._running_version, "1.0.0")
                        self.assertNotEqual(
                            successor._installed_digest, successor._running_digest
                        )
                        self.assertGreater(
                            successor._generation.receipt["core_epoch"], first_epoch + 1
                        )
                        self.assertTrue(successor._runtime.ready)
            finally:
                await successor.terminate()

    async def test_same_migrations_recovers_signed_cache_after_failed_start(self):
        await self.run_case(False)

    async def test_different_migrations_rejects_cache_without_rolling_back_data(self):
        await self.run_case(True)
