"""A durable SENDING claim must recheck grant authority across DB connections."""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.core.contracts.services import CommandOutput
from ygl_test_subject.infrastructure.managed_key_manager import ManagedKeyManager
from ygl_test_subject.infrastructure.secret_codec import AESGCMSecretCodec
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_auth import SQLiteGrantStore

from tests.services import test_output as output_fixture
from tests.storage.test_managed_key_manager import _Files, _Key
from yomihime_game_link_sdk.display import Privacy


class ManagedKeyOutputFenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.case = output_fixture.OutputServiceTests(
            "test_grant_expiring_after_root_sending_cas_aborts_before_port"
        )
        await self.case.asyncSetUp()
        self.other_db = SQLiteDatabase(self.case.db_path)
        self.gate = output_fixture._GatedBeginRootOutputs(self.case.root_outputs)
        self.sender: asyncio.Task | None = None

    async def asyncTearDown(self) -> None:
        self.gate.release.set()
        if self.sender is not None and not self.sender.done():
            self.sender.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await self.sender
        await self.other_db.executor.close()
        await self.case.db.executor.close()
        await self.case.asyncTearDown()

    async def _prepare(self):
        case = self.case
        with patch.dict(os.environ, {}, clear=True):
            root = Path(case.temp.name)
            files = _Files()
            manager_a = ManagedKeyManager(case.db, root, root / "secrets", files=files)
            manager_b = ManagedKeyManager(
                self.other_db, root, root / "secrets", files=files
            )
            initial = await manager_a.startup()
            self.assertEqual(initial["state"], "ready")
            self.assertEqual((await manager_b.startup())["state"], "ready")
            token = "secret_synthetic_01"
            secret_root = root / "secrets"
            secret_root.mkdir(exist_ok=True)
            (secret_root / f"{token}.blob").write_bytes(
                AESGCMSecretCodec(_Key(manager_a.get_key())).encrypt(b"SYNTHETIC ONLY")
            )
            connection = case.db.connect()
            try:
                connection.execute(
                    "INSERT INTO secret_receipts(secret_token,principal_id,module_id,field,operation_id,"
                    "expected_config_revision,ledger_revision,state,payload_name,key_generation) "
                    "VALUES(?,'principal-offline','sample/demo','access_token','op1',0,1,'active',?,?)",
                    (token, f"{token}.blob", initial["generation"]),
                )
                connection.execute(
                    "INSERT INTO grants(grant_id,revision,principal_id,module_id,account_id,scopes_json,"
                    "secret_token,secret_principal_id,secret_module_id,secret_field,secret_operation_id,status) "
                    "VALUES('grant-review',1,'principal-offline','sample/demo','account-review',?,"
                    "?,'principal-offline','sample/demo','access_token','op1','active')",
                    ('["lookup"]', token),
                )
                connection.commit()
            finally:
                connection.close()
            status = await manager_b.status()
            self.assertEqual(status["state"], "ready")
            self.assertEqual(status["unknown_count"], 0)
            return manager_b, status

    async def _start_sending(self) -> None:
        case = self.case
        service = case._service(
            root_outputs=self.gate, grants=SQLiteGrantStore(case.db)
        )
        view = case._view(grant_id="grant-review", grant_revision=1)
        self.sender = asyncio.create_task(
            service.route(
                view,
                CommandOutput(case._result(privacy=Privacy.PRIVATE, facts=False)),
            )
        )
        await asyncio.wait_for(self.gate.committed.wait(), timeout=8)
        self.assertIsNotNone(self.gate.sending_snapshot)

    async def _finish(self, *, expect_sent: bool) -> None:
        self.gate.release.set()
        result = await asyncio.wait_for(self.sender, timeout=8)
        connection = self.case.db.connect()
        try:
            row = connection.execute(
                "SELECT state,error_code FROM b04_root_outputs"
            ).fetchone()
        finally:
            connection.close()
        if expect_sent:
            self.assertEqual(result.status.value, "sent")
            self.assertEqual(len(self.case.message_port.calls), 1)
            self.assertEqual(row["state"], "completed")
        else:
            self.assertEqual(result.error_code, "grant_unavailable")
            self.assertEqual(self.case.message_port.calls, [])
            self.assertEqual(
                (row["state"], row["error_code"]), ("completed", "grant_unavailable")
            )

    async def test_reset_on_other_connection_aborts_private_send(self) -> None:
        manager, status = await self._prepare()
        await self._start_sending()
        await manager.reset(
            expected_revision=status["revision"],
            expected_generation=status["generation"],
            inventory_digest=status["inventory_digest"],
            confirmation="RESET ENCRYPTED CREDENTIALS",
        )
        await self._finish(expect_sent=False)

    async def test_revoke_on_other_connection_aborts_private_send(self) -> None:
        await self._prepare()
        await self._start_sending()
        grants = SQLiteGrantStore(self.other_db)
        grant = await grants.current_grant("grant-review")
        self.assertIsNotNone(grant)
        await grants.revoke_grant(grant, expected_revision=1)
        await self._finish(expect_sent=False)

    async def test_unchanged_grant_allows_private_send(self) -> None:
        await self._prepare()
        await self._start_sending()
        await self._finish(expect_sent=True)
