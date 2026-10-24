"""Real SQLite shared rolling budget, restart and cooldown boundaries."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from ygl_test_subject.infrastructure.http import SourceHttpService
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.services.source_request_budget import SourceRequestBudget

from yomihime_game_link_sdk import (
    HttpRequest,
    HttpResponse,
    SourceDeclaration,
    SourceHttpError,
)


class SourceBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.db = SQLiteDatabase(self.path)
        self.now = 1700000000.0
        self.clock = lambda: self.now
        self.ledger = SourceRequestBudget(self.db, clock=self.clock)
        self.source = SourceDeclaration(
            "shared", "api.example.test", requests_per_minute=10, requests_per_hour=2
        )

    async def asyncTearDown(self):
        await self.db.executor.close(timeout=2)
        self.temp.cleanup()

    async def test_shared_instances_reopen_and_rolling_expiry(self):
        await self.ledger.reserve(self.source, 2)
        self.now += 10
        other = SourceRequestBudget(self.db, clock=self.clock)
        await other.reserve(self.source, 2)
        with self.assertRaises(SourceHttpError) as caught:
            await other.reserve(self.source, 2)
        self.assertEqual(
            caught.exception.next_available_at, datetime.fromtimestamp(1700003600, UTC)
        )
        await self.db.executor.close(timeout=2)
        self.db = SQLiteDatabase(self.path)
        reopened = SourceRequestBudget(self.db, clock=self.clock)
        self.assertEqual((await reopened.status(self.source, 2)).used, 2)
        self.now = 1700003600
        await reopened.reserve(self.source, 2)
        self.assertEqual((await reopened.status(self.source, 2)).remaining, 0)

    async def test_rpm_and_cooldown_survive_changed_limit(self):
        source = SourceDeclaration(
            "shared", self.source.host, requests_per_minute=1, requests_per_hour=100
        )
        await self.ledger.reserve(source, 100)
        with self.assertRaises(SourceHttpError) as caught:
            await self.ledger.reserve(source, 1000)
        self.assertEqual(caught.exception.retry_after_seconds, 60)
        await self.ledger.cool_down(source, 300)
        self.now += 61
        self.assertEqual(
            (await self.ledger.status(source, 1000)).next_available_at.timestamp(),
            1700000300,
        )

    async def test_http_counts_retries_and_dynamic_config_without_reset(self):
        calls = []

        async def transport(request):
            calls.append(request)
            return HttpResponse(200, {}, b"ok")

        class Config:
            limit = 2

            async def current(self):
                return SimpleNamespace(values={"rph": self.limit})

        config = Config()
        source = SourceDeclaration(
            "shared",
            self.source.host,
            requests_per_hour=100,
            request_budget_config="rph",
        )
        one = SourceHttpService(
            (source,), transport, request_budget=self.ledger, budget_config=config
        )
        two = SourceHttpService(
            (source,), transport, request_budget=self.ledger, budget_config=config
        )
        await one.fetch(HttpRequest("shared", "/data"))
        await two.fetch(HttpRequest("shared", "/data"))
        with self.assertRaises(SourceHttpError):
            await one.fetch(HttpRequest("shared", "/data"))
        self.assertEqual(len(calls), 2)
        config.limit = 3
        await one.fetch(HttpRequest("shared", "/data"))
        config.limit = 0
        with self.assertRaises(SourceHttpError):
            await two.fetch(HttpRequest("shared", "/data"))
        self.assertEqual(len(calls), 3)
        config.limit = ""
        self.assertEqual((await one.budget("shared")).requests_per_hour, 100)

    async def test_429_date_cools_shared_manual_and_background_requests(self):
        calls = []

        async def transport(request):
            calls.append(request)
            return HttpResponse(429, {"Retry-After": "120"}, b"limited")

        one = SourceHttpService((self.source,), transport, request_budget=self.ledger)
        with self.assertRaises(SourceHttpError) as caught:
            await one.fetch(HttpRequest("shared", "/data"))
        self.assertEqual(caught.exception.next_available_at.timestamp(), self.now + 120)
        two = SourceHttpService((self.source,), transport, request_budget=self.ledger)
        with self.assertRaises(SourceHttpError):
            await two.fetch(HttpRequest("shared", "/data"))
        self.assertEqual(len(calls), 1)
