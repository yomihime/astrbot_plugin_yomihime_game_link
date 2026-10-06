"""Fresh-process Core catalog operation without host or game imports."""

import subprocess
import sys
import unittest
from pathlib import Path


class IndependentCatalogTests(unittest.TestCase):
    def test_core_only_runtime_catalog_needs_no_astrbot_or_ff14(self):
        code = r"""
import asyncio, importlib.abc, json, sys, tempfile, time
from pathlib import Path
class Forbidden(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'astrbot' or fullname.startswith('astrbot.') or '.modules.ff14' in fullname:
            raise AssertionError('unexpected host/game import: ' + fullname)
sys.meta_path.insert(0, Forbidden())
import tests
from ygl_test_subject.api.administration import AdminOperation
from ygl_test_subject.api.display import DisplayLimits
from ygl_test_subject.services.core_runtime import CoreRuntime
from ygl_test_subject.services.core_configuration import DEFAULT_REGION, core_config_target
from ygl_test_subject.services.configuration_migration import OrdinaryMigrationField
from tests.services.test_admin_operations import _Codec, _MessagePort, _Renderer
async def run():
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as work:
        root = Path(work); (root/'extensions').mkdir()
        target = core_config_target('fixture')
        core = CoreRuntime(database=root/'core.db', extension_root=root/'extensions', file_root=root/'files', secret_root=root/'secrets', secret_codec=_Codec(), http_transport=lambda request: request, renderer=_Renderer(), display_limits=DisplayLimits(2,4096), message_port=_MessagePort(), admin_context_validator=lambda *_:False, host_ingress_validator=lambda *_:False, config_principal_id='fixture', identity_namespace='fixture', ordinary_migration_fields=(OrdinaryMigrationField(target,DEFAULT_REGION,'legacy'),), ordinary_migration_source=lambda:{}, pump_interval=3600)
        await core.start()
        source = core.admin_authorization.register_source('fixture-adapter', resources=core.admin_operations.ordinary_resources(), operations={AdminOperation.READ_CONFIG})
        request = object()
        context = source.issue(subject='fixture', request=request, expiry=time.time()+30, operations={AdminOperation.READ_CONFIG}, resources=core.admin_operations.ordinary_resources(), live=lambda actual:actual is request)
        try:
            catalog = await core.admin_operations.ordinary_catalog(authorization=context)
            assert len(catalog['fields']) == 1
            assert catalog['fields'][0]['name'] == 'default_region'
            assert catalog['fields'][0]['editable'] is True
            assert not any(name == 'astrbot' or '.modules.ff14' in name for name in sys.modules)
            print('CORE_ONLY_CATALOG_OK')
        finally:
            source.end(context)
            await core.close(timeout=1)
asyncio.run(run())
"""
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", code],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CORE_ONLY_CATALOG_OK", result.stdout)
